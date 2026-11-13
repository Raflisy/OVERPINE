"""User-mode SetupAPI enumeration and DeviceIoControl helpers."""
from __future__ import annotations

import ctypes
from typing import Any

from backend.winapi.bindings import (
    DIGCF_DEVICEINTERFACE,
    DIGCF_PRESENT,
    DWORD,
    ERROR_INSUFFICIENT_BUFFER,
    ERROR_NO_MORE_ITEMS,
    FILE_ATTRIBUTE_NORMAL,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    GUID,
    HANDLE,
    OPEN_EXISTING,
    SP_DEVICE_INTERFACE_DATA,
    bind_function,
    load_library,
    windows_last_error,
)

_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _is_invalid_handle(handle: Any) -> bool:
    """Recognize null and INVALID_HANDLE_VALUE across ctypes return forms."""
    value = handle.value if isinstance(handle, ctypes.c_void_p) else handle
    return not value or value == _INVALID_HANDLE_VALUE


def enumerate_device_paths(interface_class: GUID) -> list[str]:
    """Return present device paths for one documented device-interface class."""
    setupapi = load_library("setupapi")
    if setupapi is None:
        raise OSError("SetupAPI device enumeration requires Windows")

    get_class_devs = bind_function(
        setupapi,
        "SetupDiGetClassDevsW",
        HANDLE,
        [ctypes.POINTER(GUID), ctypes.c_wchar_p, HANDLE, DWORD],
    )
    info_set = get_class_devs(
        ctypes.byref(interface_class),
        None,
        HANDLE(),
        DWORD(DIGCF_PRESENT | DIGCF_DEVICEINTERFACE),
    )
    if _is_invalid_handle(info_set):
        error = windows_last_error()
        raise OSError(error, "SetupDiGetClassDevsW failed")

    try:
        return _enumerate_paths(setupapi, info_set, interface_class)
    finally:
        destroy = bind_function(
            setupapi,
            "SetupDiDestroyDeviceInfoList",
            ctypes.c_int32,
            [HANDLE],
        )
        if not destroy(info_set):
            error = windows_last_error()
            raise OSError(error, "SetupDiDestroyDeviceInfoList failed")


def _enumerate_paths(
    setupapi: Any,
    info_set: HANDLE,
    interface_class: GUID,
) -> list[str]:
    """Walk the device-interface list and resolve each symbolic path."""
    enumerate_interface = bind_function(
        setupapi,
        "SetupDiEnumDeviceInterfaces",
        ctypes.c_int32,
        [
            HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            DWORD,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ],
    )
    get_detail = bind_function(
        setupapi,
        "SetupDiGetDeviceInterfaceDetailW",
        ctypes.c_int32,
        [
            HANDLE,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
            ctypes.c_void_p,
            DWORD,
            ctypes.POINTER(DWORD),
            ctypes.c_void_p,
        ],
    )

    paths: list[str] = []
    index = 0
    while True:
        interface = SP_DEVICE_INTERFACE_DATA()
        interface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        if not enumerate_interface(
            info_set,
            None,
            ctypes.byref(interface_class),
            DWORD(index),
            ctypes.byref(interface),
        ):
            error = windows_last_error()
            if error == ERROR_NO_MORE_ITEMS:
                return paths
            raise OSError(error, "SetupDiEnumDeviceInterfaces failed")

        required_size = DWORD()
        get_detail(
            info_set,
            ctypes.byref(interface),
            None,
            DWORD(0),
            ctypes.byref(required_size),
            None,
        )
        error = windows_last_error()
        if error != ERROR_INSUFFICIENT_BUFFER or required_size.value < 4:
            raise OSError(error, "SetupDiGetDeviceInterfaceDetailW size query failed")

        detail = ctypes.create_string_buffer(required_size.value)
        detail_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        ctypes.cast(detail, ctypes.POINTER(DWORD)).contents.value = detail_size
        if not get_detail(
            info_set,
            ctypes.byref(interface),
            ctypes.cast(detail, ctypes.c_void_p),
            DWORD(required_size.value),
            ctypes.byref(required_size),
            None,
        ):
            error = windows_last_error()
            raise OSError(error, "SetupDiGetDeviceInterfaceDetailW failed")
        path_offset = ctypes.sizeof(DWORD)
        paths.append(ctypes.wstring_at(ctypes.addressof(detail) + path_offset))
        index += 1


def open_device(path: str, access: int) -> HANDLE:
    """Open a device path with shared read/write access and the requested rights."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        raise OSError("Device handles require Windows")
    create_file = bind_function(
        kernel32,
        "CreateFileW",
        HANDLE,
        [
            ctypes.c_wchar_p,
            DWORD,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            DWORD,
            HANDLE,
        ],
    )
    handle = create_file(
        path,
        DWORD(access),
        DWORD(FILE_SHARE_READ | FILE_SHARE_WRITE),
        None,
        DWORD(OPEN_EXISTING),
        DWORD(FILE_ATTRIBUTE_NORMAL),
        HANDLE(),
    )
    if _is_invalid_handle(handle):
        error = windows_last_error()
        raise OSError(error, f"CreateFileW failed for {path}")
    return handle


def close_device(handle: HANDLE) -> None:
    """Close a device handle and report a native close failure."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        raise OSError("Device handles require Windows")
    close_handle = bind_function(
        kernel32,
        "CloseHandle",
        ctypes.c_int32,
        [HANDLE],
    )
    if not close_handle(handle):
        error = windows_last_error()
        raise OSError(error, "CloseHandle failed")


def device_io_control(
    handle: HANDLE,
    control_code: int,
    input_buffer: Any | None,
    output_buffer: Any,
) -> int:
    """Run a synchronous DeviceIoControl request and return its byte count."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        raise OSError("DeviceIoControl requires Windows")
    function = bind_function(
        kernel32,
        "DeviceIoControl",
        ctypes.c_int32,
        [
            HANDLE,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            ctypes.POINTER(DWORD),
            ctypes.c_void_p,
        ],
    )
    input_pointer = (
        ctypes.cast(ctypes.byref(input_buffer), ctypes.c_void_p)
        if input_buffer is not None
        else None
    )
    input_size = ctypes.sizeof(input_buffer) if input_buffer is not None else 0
    output_pointer = ctypes.cast(ctypes.byref(output_buffer), ctypes.c_void_p)
    output_size = ctypes.sizeof(output_buffer)
    bytes_returned = DWORD()
    if not function(
        handle,
        DWORD(control_code),
        input_pointer,
        DWORD(input_size),
        output_pointer,
        DWORD(output_size),
        ctypes.byref(bytes_returned),
        None,
    ):
        error = windows_last_error()
        raise OSError(error, f"DeviceIoControl 0x{control_code:08X} failed")
    return int(bytes_returned.value)


def open_battery_device(path: str) -> HANDLE:
    """Open a battery interface with the read access required by query IOCTLs."""
    return open_device(path, GENERIC_READ)


def open_storage_device(path: str) -> HANDLE:
    """Open a disk with zero requested access for read-only property queries."""
    return open_device(path, 0)
