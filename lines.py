"""The PC's internet connections ("lines") a download can be spread over.

A line is a network adapter that is up and has an IPv4 address and a default
gateway: the Wi-Fi, a phone tethered over USB, a cable to a second router.
Tailscale and WSL adapters have no gateway, so they are not lines. Read from
Windows' GetAdaptersAddresses through ctypes: fast, no dependency, and not
in the system's language as ipconfig's text is. Windows only; elsewhere
there are none (specs/2026-11-13-multi-line-downloads-design.md).
"""
import ctypes
import logging
import socket
import sys

log = logging.getLogger("hyperfetch.lines")

_AF_INET = 2
_GAA_FLAGS = 0x2 | 0x4 | 0x8 | 0x80     # skip anycast, multicast, DNS; gateways
_ERROR_BUFFER_OVERFLOW = 111
_IF_OPER_UP = 1


class _SOCKADDR(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_ubyte * 14)]


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.POINTER(_SOCKADDR)), ("iSockaddrLength", ctypes.c_int)]


class _ADDRESS(ctypes.Structure):
    """The head every IP_ADAPTER_*_ADDRESS list entry starts with."""


_ADDRESS._fields_ = [("Length", ctypes.c_ulong), ("Flags", ctypes.c_ulong),
                     ("Next", ctypes.POINTER(_ADDRESS)), ("Address", _SOCKET_ADDRESS)]


class _ADAPTER(ctypes.Structure):
    """IP_ADAPTER_ADDRESSES_LH, as far as FirstGatewayAddress."""


_ADAPTER._fields_ = [
    ("Length", ctypes.c_ulong), ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_ADAPTER)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p), ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8), ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong), ("Mtu", ctypes.c_ulong), ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int), ("Ipv6IfIndex", ctypes.c_ulong),
    ("ZoneIndices", ctypes.c_ulong * 16), ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_uint64), ("ReceiveLinkSpeed", ctypes.c_uint64),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(_ADDRESS)),
]


def _ipv4s(first):
    """The IPv4 addresses of an address list, in its order."""
    out, node = [], first
    while node:
        sa = node.contents.Address.lpSockaddr
        if sa and sa.contents.sa_family == _AF_INET:
            out.append(socket.inet_ntoa(bytes(sa.contents.sa_data[2:6])))
        node = node.contents.Next
    return out


def _adapters():
    """Every adapter as {name, up, ipv4, gateway} ("" when it has none)."""
    if sys.platform != "win32":
        return []
    try:
        get = ctypes.windll.iphlpapi.GetAdaptersAddresses
        size = ctypes.c_ulong(16 * 1024)
        for _ in range(3):                      # it grows between the calls
            buf = ctypes.create_string_buffer(size.value)
            rc = get(_AF_INET, _GAA_FLAGS, None, buf, ctypes.byref(size))
            if rc != _ERROR_BUFFER_OVERFLOW:
                break
        if rc != 0:
            log.debug("GetAdaptersAddresses: %d", rc)
            return []
        out = []
        node = ctypes.cast(buf, ctypes.POINTER(_ADAPTER))
        while node:
            a = node.contents
            ips = [ip for ip in _ipv4s(a.FirstUnicastAddress) if not ip.startswith("169.254.")]
            gws = [g for g in _ipv4s(a.FirstGatewayAddress) if g != "0.0.0.0"]
            out.append({"name": a.FriendlyName or "", "up": a.OperStatus == _IF_OPER_UP,
                        "ipv4": ips[0] if ips else "", "gateway": gws[0] if gws else ""})
            node = a.Next
        return out
    except (OSError, AttributeError, ValueError) as e:
        log.debug("adapters not read: %s", e)
        return []


def available():
    """(name, IPv4, gateway) of each adapter that is a line, in Windows' order."""
    return [(a["name"], a["ipv4"], a["gateway"]) for a in _adapters()
            if a["up"] and a["ipv4"] and a["gateway"] and a["name"]]


def addresses(names):
    """(name, IPv4) of the named lines there are now, in the order of `names`
    (DHCP moves addresses, so this is read as each download starts)."""
    now = {n: ip for n, ip, _ in available()}
    return [(n, now[n]) for n in names if n in now]


def route_address():
    """The IPv4 address this PC's traffic leaves from now - the default
    route's - or "" when there is none. A connected UDP socket sends nothing;
    it only asks Windows which way it would go."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def describe(conns):
    """Connections per line for the window: "Wi-Fi 6 · Phone 4"."""
    return " · ".join(f"{n} {c}" for n, c in conns.items())
