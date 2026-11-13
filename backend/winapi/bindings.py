"""Documented Windows API structures and lazily loaded DLL bindings."""
from __future__ import annotations

import ctypes
import sys
from functools import lru_cache
from typing import Any

# Microsoft Learn: Windows Data Types.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t

# Microsoft Learn: IOCTL_BATTERY_QUERY_TAG, IOCTL_BATTERY_QUERY_INFORMATION,
# and IOCTL_BATTERY_QUERY_STATUS control-code pages (Poclass.h).
IOCTL_BATTERY_QUERY_TAG = 0x00294040
IOCTL_BATTERY_QUERY_INFORMATION = 0x00294044
IOCTL_BATTERY_QUERY_STATUS = 0x0029404C

# Microsoft Learn: IOCTL_STORAGE_QUERY_PROPERTY and IOCTL_STORAGE_GET_DEVICE_NUMBER.
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080

# Microsoft Learn: SetupDiGetClassDevsW, SetupDiEnumDeviceInterfaces,
# CreateFileW, and DeviceIoControl.
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_FUNCTION = 1
ERROR_INVALID_PARAMETER = 87
ERROR_NO_MORE_ITEMS = 259
ERROR_NOT_SUPPORTED = 50
ERROR_DEVICE_NOT_CONNECTED = 1167
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080

# Microsoft Learn: BATTERY_INFORMATION and BATTERY_STATUS structures.
BATTERY_TAG_INVALID = 0
BATTERY_CAPACITY_RELATIVE = 0x40000000
BATTERY_UNKNOWN_CAPACITY = 0xFFFFFFFF
BATTERY_UNKNOWN_VOLTAGE = 0xFFFFFFFF
BATTERY_UNKNOWN_RATE = -0x80000000
BATTERY_INFORMATION_LEVEL = 0

# Microsoft Learn: STORAGE_PROPERTY_ID and STORAGE_QUERY_TYPE enumerations.
STORAGE_DEVICE_TEMPERATURE_PROPERTY = 52
PROPERTY_STANDARD_QUERY = 0

# Microsoft Learn: GetLogicalProcessorInformationEx function (sysinfoapi.h).
RELATION_PROCESSOR_CORE = 0
RELATION_CACHE = 2
ERROR_INSUFFICIENT_BUFFER = 122

# Microsoft Learn: PROCESSENTRY32W (tlhelp32.h).
MAX_PATH = 260
# Microsoft Learn: CreateToolhelp32Snapshot function (tlhelp32.h).
TH32CS_SNAPPROCESS = 0x00000002

# Microsoft Learn: Process Security and Access Rights.
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000

# Microsoft Learn: MIB_IF_ROW2 (netioapi.h).
IF_MAX_STRING_SIZE = 256
IF_MAX_PHYS_ADDRESS_LENGTH = 32

# Microsoft Learn: IP_ADAPTER_ADDRESSES_LH (iptypes.h).
MAX_ADAPTER_ADDRESS_LENGTH = 8

# Microsoft Learn: MIB_IF_ROW2 (netioapi.h), InterfaceType values.
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_TUNNEL = 131

# Microsoft Learn: IF_OPER_STATUS enumeration (netioapi.h).
IF_OPER_STATUS_UP = 1
IF_OPER_STATUS_DOWN = 2
IF_OPER_STATUS_TESTING = 3
IF_OPER_STATUS_UNKNOWN = 4
IF_OPER_STATUS_DORMANT = 5
IF_OPER_STATUS_NOT_PRESENT = 6
IF_OPER_STATUS_LOWER_LAYER_DOWN = 7

# Microsoft Learn: MEDIA_CONNECT_STATE enumeration (netioapi.h).
MEDIA_CONNECT_STATE_CONNECTED = 1


# Microsoft Learn: FILETIME (minwinbase.h).
class FILETIME(ctypes.Structure):
    """Represent the documented two-DWORD Windows FILETIME structure."""

    _fields_ = [
        ("dwLowDateTime", DWORD),
        ("dwHighDateTime", DWORD),
    ]


# Microsoft Learn: MEMORYSTATUSEX (sysinfoapi.h).
class MEMORYSTATUSEX(ctypes.Structure):
    """Receive physical and virtual memory availability."""

    _fields_ = [
        ("dwLength", DWORD),
        ("dwMemoryLoad", DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


# Microsoft Learn: PERFORMANCE_INFORMATION (psapi.h).
class PERFORMANCE_INFORMATION(ctypes.Structure):
    """Receive system commit, cache, and physical-memory page counts."""

    _fields_ = [
        ("cb", DWORD),
        ("CommitTotal", SIZE_T),
        ("CommitLimit", SIZE_T),
        ("CommitPeak", SIZE_T),
        ("PhysicalTotal", SIZE_T),
        ("PhysicalAvailable", SIZE_T),
        ("SystemCache", SIZE_T),
        ("KernelTotal", SIZE_T),
        ("KernelPaged", SIZE_T),
        ("KernelNonpaged", SIZE_T),
        ("PageSize", SIZE_T),
        ("HandleCount", DWORD),
        ("ProcessCount", DWORD),
        ("ThreadCount", DWORD),
    ]


# Microsoft Learn: SYSTEM_POWER_STATUS (winbase.h).
class SYSTEM_POWER_STATUS(ctypes.Structure):
    """Receive the system AC and battery status."""

    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", DWORD),
        ("BatteryFullLifeTime", DWORD),
    ]


# Microsoft Learn: PROCESSOR_POWER_INFORMATION structure.
class PROCESSOR_POWER_INFORMATION(ctypes.Structure):
    """Receive reported processor frequencies and idle-state details."""

    _fields_ = [
        ("Number", DWORD),
        ("MaxMhz", DWORD),
        ("CurrentMhz", DWORD),
        ("MhzLimit", DWORD),
        ("MaxIdleState", DWORD),
        ("CurrentIdleState", DWORD),
    ]


# Microsoft Learn: PROCESS_MEMORY_COUNTERS_EX (psapi.h).
class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    """Receive working-set and private-usage values for a process."""

    _fields_ = [
        ("cb", DWORD),
        ("PageFaultCount", DWORD),
        ("PeakWorkingSetSize", SIZE_T),
        ("WorkingSetSize", SIZE_T),
        ("QuotaPeakPagedPoolUsage", SIZE_T),
        ("QuotaPagedPoolUsage", SIZE_T),
        ("QuotaPeakNonPagedPoolUsage", SIZE_T),
        ("QuotaNonPagedPoolUsage", SIZE_T),
        ("PagefileUsage", SIZE_T),
        ("PeakPagefileUsage", SIZE_T),
        ("PrivateUsage", SIZE_T),
    ]


# Microsoft Learn: SYSTEM_INFO (sysinfoapi.h).
class SYSTEM_INFO_PROCESSOR_ARCHITECTURE(ctypes.Structure):
    """Represent the documented architecture branch of SYSTEM_INFO."""

    _fields_ = [
        ("wProcessorArchitecture", WORD),
        ("wReserved", WORD),
    ]


# Microsoft Learn: SYSTEM_INFO (sysinfoapi.h).
class SYSTEM_INFO_UNION(ctypes.Union):
    """Represent the documented SYSTEM_INFO anonymous union."""

    _fields_ = [
        ("dwOemId", DWORD),
        ("architecture", SYSTEM_INFO_PROCESSOR_ARCHITECTURE),
    ]


# Microsoft Learn: SYSTEM_INFO (sysinfoapi.h).
class SYSTEM_INFO(ctypes.Structure):
    """Receive the system properties needed for processor power queries."""

    _anonymous_ = ("processor",)
    _fields_ = [
        ("processor", SYSTEM_INFO_UNION),
        ("dwPageSize", DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", SIZE_T),
        ("dwNumberOfProcessors", DWORD),
        ("dwProcessorType", DWORD),
        ("dwAllocationGranularity", DWORD),
        ("wProcessorLevel", WORD),
        ("wProcessorRevision", WORD),
    ]


# Microsoft Learn: GROUP_AFFINITY (winnt.h).
class GROUP_AFFINITY(ctypes.Structure):
    """Represent processor affinity for one Windows processor group."""

    _fields_ = [
        ("Mask", ULONG_PTR),
        ("Group", WORD),
        ("Reserved", WORD * 3),
    ]


# Microsoft Learn: PROCESSOR_RELATIONSHIP (winnt.h).
class PROCESSOR_RELATIONSHIP(ctypes.Structure):
    """Represent one core and its processor-group affinity masks."""

    _fields_ = [
        ("Flags", ctypes.c_ubyte),
        ("EfficiencyClass", ctypes.c_ubyte),
        ("RelativePerformance", WORD),
        ("RelativeEfficiency", WORD),
        ("Reserved", ctypes.c_ubyte * 16),
        ("GroupCount", WORD),
        ("GroupMask", GROUP_AFFINITY * 1),
    ]


# Microsoft Learn: CACHE_RELATIONSHIP (winnt.h).
class CACHE_RELATIONSHIP(ctypes.Structure):
    """Represent one processor cache and its group affinity."""

    _fields_ = [
        ("Level", ctypes.c_ubyte),
        ("Associativity", ctypes.c_ubyte),
        ("LineSize", WORD),
        ("CacheSize", DWORD),
        ("Type", DWORD),
        ("Reserved", ctypes.c_ubyte * 18),
        ("GroupCount", WORD),
        ("GroupMasks", GROUP_AFFINITY * 1),
    ]


# Microsoft Learn: SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX (winnt.h).
class LOGICAL_PROCESSOR_RELATIONSHIP_DATA(ctypes.Union):
    """Hold the documented variable data for processor or cache records."""

    _fields_ = [
        ("Processor", PROCESSOR_RELATIONSHIP),
        ("Cache", CACHE_RELATIONSHIP),
    ]


# Microsoft Learn: SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX (winnt.h).
class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX(ctypes.Structure):
    """Read the fixed header and relationship union of a variable-size record."""

    _anonymous_ = ("RelationshipData",)
    _fields_ = [
        ("Relationship", DWORD),
        ("Size", DWORD),
        ("RelationshipData", LOGICAL_PROCESSOR_RELATIONSHIP_DATA),
    ]


# Microsoft Learn: MIB_IF_ROW2 (netioapi.h).
class GUID(ctypes.Structure):
    """Represent the documented Windows GUID value embedded in interface rows."""

    _fields_ = [
        ("Data1", DWORD),
        ("Data2", WORD),
        ("Data3", WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# Microsoft Learn: System-Defined Device Setup Classes Available to Vendors
# lists the Battery class GUID; Battery Information uses its class interface.
GUID_DEVINTERFACE_BATTERY = GUID(
    0x72631E54,
    0x78A4,
    0x11D0,
    (ctypes.c_ubyte * 8)(0xBC, 0xF7, 0x00, 0xAA, 0x00, 0xB7, 0xB3, 0x2A),
)

# Microsoft Learn: GUID_DEVINTERFACE_DISK.
GUID_DEVINTERFACE_DISK = GUID(
    0x53F56307,
    0xB6BF,
    0x11D0,
    (ctypes.c_ubyte * 8)(0x94, 0xF2, 0x00, 0xA0, 0xC9, 0x1E, 0xFB, 0x8B),
)


# Microsoft Learn: SP_DEVICE_INTERFACE_DATA (setupapi.h).
class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    """Represent one SetupAPI device interface record."""

    _fields_ = [
        ("cbSize", DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", DWORD),
        ("Reserved", ULONG_PTR),
    ]


# Microsoft Learn: BATTERY_INFORMATION structure (Poclass.h).
class BATTERY_INFORMATION(ctypes.Structure):
    """Receive battery capacity and cycle-count information."""

    _fields_ = [
        ("Capabilities", DWORD),
        ("Technology", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte * 3),
        ("Chemistry", ctypes.c_ubyte * 4),
        ("DesignedCapacity", DWORD),
        ("FullChargedCapacity", DWORD),
        ("DefaultAlert1", DWORD),
        ("DefaultAlert2", DWORD),
        ("CriticalBias", DWORD),
        ("CycleCount", DWORD),
    ]


# Microsoft Learn: BATTERY_QUERY_INFORMATION structure (Poclass.h).
class BATTERY_QUERY_INFORMATION(ctypes.Structure):
    """Select the information level for a tagged battery query."""

    _fields_ = [
        ("BatteryTag", DWORD),
        ("InformationLevel", DWORD),
        ("AtRate", ctypes.c_int32),
    ]


# Microsoft Learn: BATTERY_WAIT_STATUS structure (Poclass.h).
class BATTERY_WAIT_STATUS(ctypes.Structure):
    """Request an immediate battery status sample without a wait."""

    _fields_ = [
        ("BatteryTag", DWORD),
        ("Timeout", DWORD),
        ("PowerState", DWORD),
        ("LowCapacity", DWORD),
        ("HighCapacity", DWORD),
    ]


# Microsoft Learn: BATTERY_STATUS structure (Poclass.h).
class BATTERY_STATUS(ctypes.Structure):
    """Receive current battery power flags, capacity, voltage, and rate."""

    _fields_ = [
        ("PowerState", DWORD),
        ("Capacity", DWORD),
        ("Voltage", DWORD),
        ("Rate", ctypes.c_int32),
    ]


# Microsoft Learn: STORAGE_PROPERTY_QUERY (winioctl.h).
class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    """Request a standard storage-device property."""

    _fields_ = [
        ("PropertyId", DWORD),
        ("QueryType", DWORD),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


# Microsoft Learn: STORAGE_DEVICE_NUMBER (winioctl.h).
class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    """Receive the physical device number for a disk interface."""

    _fields_ = [
        ("DeviceType", DWORD),
        ("DeviceNumber", DWORD),
        ("PartitionNumber", DWORD),
    ]


# Microsoft Learn: STORAGE_TEMPERATURE_INFO (ntddstor.h).
class STORAGE_TEMPERATURE_INFO(ctypes.Structure):
    """Receive one storage-device temperature sensor reading."""

    _fields_ = [
        ("Index", WORD),
        ("Temperature", ctypes.c_int16),
        ("OverThreshold", ctypes.c_int16),
        ("UnderThreshold", ctypes.c_int16),
        ("OverThresholdChangable", ctypes.c_ubyte),
        ("UnderThresholdChangable", ctypes.c_ubyte),
        ("EventGenerated", ctypes.c_ubyte),
        ("Reserved0", ctypes.c_ubyte),
        ("Reserved1", DWORD),
    ]


# Microsoft Learn: STORAGE_TEMPERATURE_DATA_DESCRIPTOR (ntddstor.h).
class STORAGE_TEMPERATURE_DATA_DESCRIPTOR(ctypes.Structure):
    """Receive the header and first variable-length temperature entry."""

    _fields_ = [
        ("Version", DWORD),
        ("Size", DWORD),
        ("CriticalTemperature", ctypes.c_int16),
        ("WarningTemperature", ctypes.c_int16),
        ("InfoCount", WORD),
        ("Reserved0", ctypes.c_ubyte * 2),
        ("Reserved1", DWORD * 2),
        ("TemperatureInfo", STORAGE_TEMPERATURE_INFO * 1),
    ]


# Microsoft Learn: MIB_IF_ROW2 (netioapi.h).
class MIB_IF_OPER_STATUS_FLAGS(ctypes.Structure):
    """Represent the documented network-interface status bit fields."""

    _fields_ = [
        ("HardwareInterface", ctypes.c_ubyte, 1),
        ("FilterInterface", ctypes.c_ubyte, 1),
        ("ConnectorPresent", ctypes.c_ubyte, 1),
        ("NotAuthenticated", ctypes.c_ubyte, 1),
        ("NotMediaConnected", ctypes.c_ubyte, 1),
        ("Paused", ctypes.c_ubyte, 1),
        ("LowPower", ctypes.c_ubyte, 1),
        ("EndPointInterface", ctypes.c_ubyte, 1),
    ]


# Microsoft Learn: MIB_IF_ROW2 (netioapi.h).
class MIB_IF_ROW2(ctypes.Structure):
    """Represent the documented network-interface row and 64-bit counters."""

    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", DWORD),
        ("InterfaceGuid", GUID),
        ("Alias", ctypes.c_wchar * (IF_MAX_STRING_SIZE + 1)),
        ("Description", ctypes.c_wchar * (IF_MAX_STRING_SIZE + 1)),
        ("PhysicalAddressLength", DWORD),
        ("PhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("Mtu", DWORD),
        ("Type", DWORD),
        ("TunnelType", DWORD),
        ("MediaType", DWORD),
        ("PhysicalMediumType", DWORD),
        ("AccessType", DWORD),
        ("DirectionType", DWORD),
        ("InterfaceAndOperStatusFlags", MIB_IF_OPER_STATUS_FLAGS),
        ("OperStatus", DWORD),
        ("AdminStatus", DWORD),
        ("MediaConnectState", DWORD),
        ("NetworkGuid", GUID),
        ("ConnectionType", DWORD),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64),
        ("InUcastPkts", ctypes.c_uint64),
        ("InNUcastPkts", ctypes.c_uint64),
        ("InDiscards", ctypes.c_uint64),
        ("InErrors", ctypes.c_uint64),
        ("InUnknownProtos", ctypes.c_uint64),
        ("InUcastOctets", ctypes.c_uint64),
        ("InMulticastOctets", ctypes.c_uint64),
        ("InBroadcastOctets", ctypes.c_uint64),
        ("OutOctets", ctypes.c_uint64),
        ("OutUcastPkts", ctypes.c_uint64),
        ("OutNUcastPkts", ctypes.c_uint64),
        ("OutDiscards", ctypes.c_uint64),
        ("OutErrors", ctypes.c_uint64),
        ("OutUcastOctets", ctypes.c_uint64),
        ("OutMulticastOctets", ctypes.c_uint64),
        ("OutBroadcastOctets", ctypes.c_uint64),
        ("OutQLen", ctypes.c_uint64),
    ]


# Microsoft Learn: MIB_IF_TABLE2 (netioapi.h).
class MIB_IF_TABLE2(ctypes.Structure):
    """Represent the variable-length table returned by GetIfTable2."""

    _fields_ = [
        ("NumEntries", DWORD),
        ("Table", MIB_IF_ROW2 * 1),
    ]


# Microsoft Learn: SOCKET_ADDRESS (ws2def.h).
class SOCKET_ADDRESS(ctypes.Structure):
    """Represent a pointer to a socket address and its byte length."""

    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int32),
    ]


# Microsoft Learn: IP_ADAPTER_ADDRESSES_LH (iptypes.h).
class IP_ADAPTER_ADDRESSES_INDICES(ctypes.Structure):
    """Represent the documented adapter length and IPv4 index fields."""

    _fields_ = [
        ("Length", DWORD),
        ("IfIndex", DWORD),
    ]


# Microsoft Learn: IP_ADAPTER_ADDRESSES_LH (iptypes.h).
class IP_ADAPTER_ADDRESSES_ALIGNMENT(ctypes.Union):
    """Represent the documented adapter alignment and index union."""

    _anonymous_ = ("Indices",)
    _fields_ = [
        ("Alignment", ctypes.c_uint64),
        ("Indices", IP_ADAPTER_ADDRESSES_INDICES),
    ]


# Microsoft Learn: IP_ADAPTER_ADDRESSES_LH (iptypes.h).
class IP_ADAPTER_ADDRESSES_PREFIX(ctypes.Structure):
    """Read adapter indices, names, status, and the unicast-address pointer."""

    _anonymous_ = ("IndexData",)
    _fields_ = [
        ("IndexData", IP_ADAPTER_ADDRESSES_ALIGNMENT),
        ("Next", ctypes.c_void_p),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.c_void_p),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * MAX_ADAPTER_ADDRESS_LENGTH),
        ("PhysicalAddressLength", DWORD),
        ("Flags", DWORD),
        ("Mtu", DWORD),
        ("IfType", DWORD),
        ("OperStatus", DWORD),
        ("Ipv6IfIndex", DWORD),
    ]


# Microsoft Learn: IP_ADAPTER_UNICAST_ADDRESS_LH (iptypes.h).
class IP_ADAPTER_UNICAST_ADDRESS_PREFIX(ctypes.Structure):
    """Read one unicast socket address from the adapter linked list."""

    _fields_ = [
        ("Alignment", ctypes.c_uint64),
        ("Next", ctypes.c_void_p),
        ("Address", SOCKET_ADDRESS),
    ]


# Microsoft Learn: SOCKADDR_IN (ws2def.h).
class IN_ADDR(ctypes.Union):
    """Represent the IPv4 address bytes in a SOCKADDR_IN structure."""

    _fields_ = [
        ("S_addr", DWORD),
        ("S_bytes", ctypes.c_ubyte * 4),
    ]


# Microsoft Learn: SOCKADDR_IN (ws2def.h).
class SOCKADDR_IN(ctypes.Structure):
    """Represent a documented IPv4 socket address."""

    _fields_ = [
        ("sin_family", WORD),
        ("sin_port", WORD),
        ("sin_addr", IN_ADDR),
        ("sin_zero", ctypes.c_ubyte * 8),
    ]


# Microsoft Learn: SOCKADDR_IN6 (ws2def.h).
class SOCKADDR_IN6(ctypes.Structure):
    """Represent a documented IPv6 socket address."""

    _fields_ = [
        ("sin6_family", WORD),
        ("sin6_port", WORD),
        ("sin6_flowinfo", DWORD),
        ("sin6_addr", ctypes.c_ubyte * 16),
        ("sin6_scope_id", DWORD),
    ]


# Microsoft Learn: PROCESSENTRY32W (tlhelp32.h).
class PROCESSENTRY32W(ctypes.Structure):
    """Receive a process entry from a Tool Help process snapshot."""

    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


# Microsoft Learn: PDH_FMT_COUNTERVALUE (pdh.h).
class PDH_COUNTER_VALUE_UNION(ctypes.Union):
    """Represent the documented formatted PDH value union."""

    _fields_ = [
        ("longValue", ctypes.c_int32),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_int64),
        ("AnsiStringValue", ctypes.c_void_p),
        ("WideStringValue", ctypes.c_void_p),
    ]


# Microsoft Learn: PDH_FMT_COUNTERVALUE (pdh.h).
class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    """Receive the formatted value and validity status of one PDH counter."""

    _fields_ = [
        ("CStatus", DWORD),
        ("value", PDH_COUNTER_VALUE_UNION),
    ]


# Microsoft Learn: PDH_COUNTER_PATH_ELEMENTS_W (pdh.h).
class PDH_COUNTER_PATH_ELEMENTS_W(ctypes.Structure):
    """Represent the documented counter-path union member."""

    _fields_ = [
        ("szMachineName", ctypes.c_wchar_p),
        ("szObjectName", ctypes.c_wchar_p),
        ("szInstanceName", ctypes.c_wchar_p),
        ("szParentInstance", ctypes.c_wchar_p),
        ("dwInstanceIndex", DWORD),
        ("szCounterName", ctypes.c_wchar_p),
    ]


# Microsoft Learn: PDH_DATA_ITEM_PATH_ELEMENTS_W (pdh.h).
class PDH_DATA_ITEM_PATH_ELEMENTS_W(ctypes.Structure):
    """Represent the documented data-item path union member."""

    _fields_ = [
        ("szMachineName", ctypes.c_wchar_p),
        ("szObjectName", ctypes.c_wchar_p),
        ("szInstanceName", ctypes.c_wchar_p),
        ("szParentInstance", ctypes.c_wchar_p),
        ("dwInstanceIndex", DWORD),
        ("szDataItemName", ctypes.c_wchar_p),
    ]


# Microsoft Learn: PDH_COUNTER_PATH_ELEMENTS_W and PDH_DATA_ITEM_PATH_ELEMENTS_W (pdh.h).
class PDH_COUNTER_PATH_DATA(ctypes.Union):
    """Hold either documented PDH counter path representation."""

    _fields_ = [
        ("CounterPath", PDH_COUNTER_PATH_ELEMENTS_W),
        ("DataItemPath", PDH_DATA_ITEM_PATH_ELEMENTS_W),
    ]


# Microsoft Learn: PDH_COUNTER_INFO_W (pdh.h).
class PDH_COUNTER_INFO_W(ctypes.Structure):
    """Read a PDH counter path, type, and provider explanation text."""

    _anonymous_ = ("PathData",)
    _fields_ = [
        ("dwLength", DWORD),
        ("dwType", DWORD),
        ("CVersion", DWORD),
        ("CStatus", DWORD),
        ("lScale", ctypes.c_int32),
        ("lDefaultScale", ctypes.c_int32),
        ("dwUserData", ULONG_PTR),
        ("dwQueryUserData", ULONG_PTR),
        ("szFullPath", ctypes.c_wchar_p),
        ("PathData", PDH_COUNTER_PATH_DATA),
        ("szExplainText", ctypes.c_wchar_p),
        ("DataBuffer", DWORD * 1),
    ]


@lru_cache(maxsize=None)
def load_library(name: str) -> Any | None:
    """Load a Windows DLL only when the process is running on Windows."""
    if sys.platform != "win32":
        return None
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("ctypes.WinDLL is unavailable on this Python runtime")
    return loader(name, use_last_error=True)


def bind_function(
    library: Any,
    name: str,
    result_type: Any,
    argument_types: list[Any],
) -> Any:
    """Set a documented ctypes signature and return the native function."""
    function = getattr(library, name)
    function.restype = result_type
    function.argtypes = argument_types
    return function


def windows_last_error() -> int:
    """Return the thread-local Windows error associated with a failed API call."""
    get_last_error = getattr(ctypes, "get_last_error", None)
    return int(get_last_error()) if get_last_error is not None else 0
