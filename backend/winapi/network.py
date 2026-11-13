"""Network-interface counters and adapter addresses from Windows IP Helper."""
from __future__ import annotations

import ctypes
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from backend.winapi.bindings import (
    DWORD,
    IF_TYPE_TUNNEL,
    IF_OPER_STATUS_UP,
    IF_TYPE_SOFTWARE_LOOPBACK,
    IP_ADAPTER_ADDRESSES_PREFIX,
    IP_ADAPTER_UNICAST_ADDRESS_PREFIX,
    MIB_IF_ROW2,
    MIB_IF_TABLE2,
    MEDIA_CONNECT_STATE_CONNECTED,
    SOCKADDR_IN,
    SOCKADDR_IN6,
    WORD,
    bind_function,
    load_library,
)

log = logging.getLogger("overpine")
_lock = threading.RLock()
_previous_counters: dict[int, tuple[int, int, float]] = {}
_UINT64_MASK = (1 << 64) - 1
_MIN_RATE_SAMPLE_INTERVAL_S = 1.0
# Microsoft Learn: GetAdaptersAddresses function (iphlpapi.h).
_ERROR_BUFFER_OVERFLOW = 111
_ERROR_NO_DATA = 232
# Microsoft Learn: GetAdaptersAddresses function (iphlpapi.h), AF_UNSPEC.
_AF_UNSPEC = 0
_GAA_FLAGS = 0


def _is_real_interface_row(row: MIB_IF_ROW2) -> bool:
    """Keep hardware interfaces while excluding filters and software tunnels."""
    flags = row.InterfaceAndOperStatusFlags
    return (
        bool(flags.HardwareInterface)
        and not bool(flags.FilterInterface)
        and int(row.Type)
        not in (IF_TYPE_SOFTWARE_LOOPBACK, IF_TYPE_TUNNEL)
    )


@dataclass(frozen=True, slots=True)
class NetworkInterface:
    """Contain one interface's documented identity, state, rates, and IPs."""

    interface_index: int
    name: str
    description: str
    interface_type: int
    hardware_interface: bool
    operational_status: int | None
    media_connected: bool | None
    transmit_link_speed_bps: int | None
    receive_link_speed_bps: int | None
    received_octets: int | None
    sent_octets: int | None
    hardware_address: str | None
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NetworkRates:
    """Contain aggregate byte rates and whether the reading is available."""

    received_bytes_per_second: float | None
    sent_bytes_per_second: float | None
    state: str


def _counter_delta(current: int, previous: int) -> int:
    """Return a documented 64-bit interface-counter delta including wraparound."""
    return (current - previous) & _UINT64_MASK


def _interface_table(iphlpapi: Any) -> list[MIB_IF_ROW2]:
    """Copy interface rows before releasing the table with FreeMibTable."""
    # Microsoft Learn: GetIfTable2 function (netioapi.h).
    get_table = bind_function(
        iphlpapi,
        "GetIfTable2",
        DWORD,
        [ctypes.POINTER(ctypes.c_void_p)],
    )
    table_pointer = ctypes.c_void_p()
    status = int(get_table(ctypes.byref(table_pointer)))
    if status != 0:
        raise OSError(status, "GetIfTable2 failed")
    if not table_pointer.value:
        raise OSError("GetIfTable2 returned a null table")

    try:
        table = ctypes.cast(
            table_pointer,
            ctypes.POINTER(MIB_IF_TABLE2),
        ).contents
        count = int(table.NumEntries)
        first_row_address = (
            int(table_pointer.value) + MIB_IF_TABLE2.Table.offset
        )
        rows = []
        for index in range(count):
            address = (
                first_row_address + index * ctypes.sizeof(MIB_IF_ROW2)
            )
            rows.append(
                MIB_IF_ROW2.from_buffer_copy(
                    ctypes.string_at(address, ctypes.sizeof(MIB_IF_ROW2))
                )
            )
        return rows
    finally:
        # Microsoft Learn: FreeMibTable function (netioapi.h).
        free_table = bind_function(
            iphlpapi,
            "FreeMibTable",
            None,
            [ctypes.c_void_p],
        )
        free_table(table_pointer)


def _socket_address_text(address: Any) -> str | None:
    """Convert a documented IPv4 or IPv6 socket address to display text."""
    pointer = address.lpSockaddr
    if not pointer:
        return None
    family = ctypes.cast(pointer, ctypes.POINTER(WORD)).contents.value
    if family == socket.AF_INET and address.iSockaddrLength >= ctypes.sizeof(SOCKADDR_IN):
        native = ctypes.cast(pointer, ctypes.POINTER(SOCKADDR_IN)).contents
        packed = bytes(native.sin_addr.S_bytes)
        return socket.inet_ntop(socket.AF_INET, packed)
    if family == socket.AF_INET6 and address.iSockaddrLength >= ctypes.sizeof(SOCKADDR_IN6):
        native = ctypes.cast(pointer, ctypes.POINTER(SOCKADDR_IN6)).contents
        rendered = socket.inet_ntop(socket.AF_INET6, bytes(native.sin6_addr))
        if native.sin6_scope_id:
            return f"{rendered}%{native.sin6_scope_id}"
        return rendered
    return None


def _adapter_addresses(iphlpapi: Any) -> dict[int, tuple[str, str, int, tuple[str, ...]]]:
    """Read adapter names, state, and IP addresses with GetAdaptersAddresses."""
    # Microsoft Learn: GetAdaptersAddresses function (iphlpapi.h).
    get_adapters = bind_function(
        iphlpapi,
        "GetAdaptersAddresses",
        DWORD,
        [
            DWORD,
            DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(DWORD),
        ],
    )
    required_size = DWORD(0)
    status = int(
        get_adapters(
            DWORD(_AF_UNSPEC),
            DWORD(_GAA_FLAGS),
            None,
            None,
            ctypes.byref(required_size),
        )
    )
    if status == _ERROR_NO_DATA:
        return {}
    if status != _ERROR_BUFFER_OVERFLOW or required_size.value == 0:
        raise OSError(status, "GetAdaptersAddresses could not size its result")

    buffer = ctypes.create_string_buffer(required_size.value)
    status = int(
        get_adapters(
            DWORD(_AF_UNSPEC),
            DWORD(_GAA_FLAGS),
            None,
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(required_size),
        )
    )
    if status == _ERROR_NO_DATA:
        return {}
    if status != 0:
        raise OSError(status, "GetAdaptersAddresses failed")

    adapters: dict[int, tuple[str, str, int, tuple[str, ...]]] = {}
    address = ctypes.addressof(buffer)
    while address:
        adapter = ctypes.cast(
            address,
            ctypes.POINTER(IP_ADAPTER_ADDRESSES_PREFIX),
        ).contents
        index = int(adapter.IfIndex or adapter.Ipv6IfIndex)
        friendly_name = str(adapter.FriendlyName or "")
        description = str(adapter.Description or "")
        addresses: list[str] = []
        unicast_pointer = adapter.FirstUnicastAddress
        while unicast_pointer:
            unicast = ctypes.cast(
                unicast_pointer,
                ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS_PREFIX),
            ).contents
            rendered = _socket_address_text(unicast.Address)
            if rendered and rendered not in addresses:
                addresses.append(rendered)
            unicast_pointer = unicast.Next
        if index:
            adapters[index] = (
                friendly_name or description,
                description,
                int(adapter.OperStatus),
                tuple(addresses),
            )
        address = int(adapter.Next or 0)
    return adapters


def network_interfaces() -> list[NetworkInterface] | None:
    """Return interface counters, link speeds, statuses, and IP addresses."""
    iphlpapi = load_library("iphlpapi")
    if iphlpapi is None:
        return None
    rows = _interface_table(iphlpapi)
    try:
        adapters = _adapter_addresses(iphlpapi)
    except OSError as error:
        log.warning("Could not read adapter names and addresses: %s", error)
        adapters = {}

    result: list[NetworkInterface] = []
    seen_indices: set[int] = set()
    for row in rows:
        if not _is_real_interface_row(row):
            continue
        index = int(row.InterfaceIndex)
        if index in seen_indices:
            continue
        seen_indices.add(index)
        adapter = adapters.get(index)
        name = str(row.Alias or "")
        description = str(row.Description or "")
        status = int(row.OperStatus)
        if adapter:
            name = adapter[0] or name
            description = adapter[1] or description
            if status == 0:
                status = adapter[2]
            addresses = adapter[3]
        else:
            addresses = ()
        result.append(
            NetworkInterface(
                interface_index=index,
                name=name or description or f"Interface {index}",
                description=description,
                interface_type=int(row.Type),
                hardware_interface=bool(
                    row.InterfaceAndOperStatusFlags.HardwareInterface
                ),
                operational_status=status if status > 0 else None,
                media_connected=(
                    int(row.MediaConnectState) == MEDIA_CONNECT_STATE_CONNECTED
                    if int(row.MediaConnectState) > 0
                    else None
                ),
                transmit_link_speed_bps=int(row.TransmitLinkSpeed),
                receive_link_speed_bps=int(row.ReceiveLinkSpeed),
                received_octets=int(row.InOctets),
                sent_octets=int(row.OutOctets),
                hardware_address=(
                    ":".join(
                        f"{byte:02X}"
                        for byte in row.PhysicalAddress[
                            : min(
                                int(row.PhysicalAddressLength),
                                len(row.PhysicalAddress),
                            )
                        ]
                    )
                    or None
                ),
                addresses=addresses,
            )
        )
    return result


def network_rate_sample(
    _inventory: dict[str, Any] | None = None,
) -> NetworkRates:
    """Calculate aggregate physical-interface rates without fabricating a baseline."""
    global _previous_counters
    try:
        interfaces = network_interfaces()
    except OSError as error:
        log.exception("Could not read Windows network-interface counters")
        return NetworkRates(None, None, f"error: {error}")
    if interfaces is None:
        return NetworkRates(None, None, "Windows IP Helper APIs are unavailable")
    now = time.monotonic()
    current: dict[int, tuple[int, int, float]] = {}
    received_rates: list[float] = []
    sent_rates: list[float] = []
    eligible_count = 0
    seen_interface_indices: set[int] = set()
    with _lock:
        previous = _previous_counters
        for interface in interfaces:
            if (
                interface.interface_type == IF_TYPE_SOFTWARE_LOOPBACK
                or not interface.hardware_interface
                or interface.operational_status != IF_OPER_STATUS_UP
                or interface.media_connected is not True
                or interface.received_octets is None
                or interface.sent_octets is None
            ):
                continue
            if interface.interface_index in seen_interface_indices:
                continue
            seen_interface_indices.add(interface.interface_index)
            eligible_count += 1
            sample = (
                interface.received_octets,
                interface.sent_octets,
                now,
            )
            current[interface.interface_index] = sample
            old_sample = previous.get(interface.interface_index)
            if old_sample is None:
                continue
            elapsed = now - old_sample[2]
            if elapsed < _MIN_RATE_SAMPLE_INTERVAL_S:
                current[interface.interface_index] = old_sample
                continue
            receive_delta = _counter_delta(sample[0], old_sample[0])
            send_delta = _counter_delta(sample[1], old_sample[1])
            received_rates.append(receive_delta / elapsed)
            sent_rates.append(send_delta / elapsed)
        _previous_counters = current

    if not eligible_count:
        return NetworkRates(None, None, "no connected physical interfaces")
    if not received_rates or not sent_rates:
        return NetworkRates(None, None, "no data yet")
    return NetworkRates(
        received_bytes_per_second=sum(received_rates),
        sent_bytes_per_second=sum(sent_rates),
        state="valid",
    )


def network_io_rates(
    inventory: dict[str, Any] | None = None,
) -> tuple[float | None, float | None]:
    """Return aggregate received/sent bytes per second for existing consumers."""
    sample = network_rate_sample(inventory)
    return sample.received_bytes_per_second, sample.sent_bytes_per_second
