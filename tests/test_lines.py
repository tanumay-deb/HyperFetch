"""lines.py: which of the PC's network adapters are internet connections
("lines") a download can be spread over (specs/2026-11-13-multi-line-
downloads-design.md)."""
import socket
import sys

import pytest

import lines

TABLE = [
    {"name": "Wi-Fi", "up": True, "ipv4": "192.168.1.5", "gateway": "192.168.1.1"},
    {"name": "Tailscale", "up": True, "ipv4": "100.64.0.2", "gateway": ""},
    {"name": "Ethernet", "up": False, "ipv4": "192.168.1.9", "gateway": "192.168.1.1"},
    {"name": "Phone", "up": True, "ipv4": "192.168.42.7", "gateway": "192.168.42.129"},
]


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(lines, "_adapters", lambda: [dict(a) for a in TABLE])


def test_available_keeps_up_adapters_with_ipv4_and_gateway(table):
    assert lines.available() == [("Wi-Fi", "192.168.1.5", "192.168.1.1"),
                                 ("Phone", "192.168.42.7", "192.168.42.129")]


def test_addresses_are_every_line_there_but_those_unticked(table):
    assert lines.addresses(["Gone"]) == [("Wi-Fi", "192.168.1.5"), ("Phone", "192.168.42.7")]
    assert lines.addresses(["Wi-Fi"]) == [("Phone", "192.168.42.7")]


def test_describe():
    assert lines.describe({"Wi-Fi": 6, "Phone": 4}) == "Wi-Fi 6 · Phone 4"
    assert lines.describe({}) == ""


@pytest.mark.skipif(sys.platform != "win32", reason="lines are Windows only")
def test_real_call_gives_addresses_this_pc_has():
    # not "the default route's adapter": a VPN or TUN adapter (no gateway of
    # its own) can hold the default route, as on the PC this was written on
    found = lines.available()
    if not found:
        pytest.skip("no adapter with a gateway here")
    for name, ip, gw in found:
        assert name and gw
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((ip, 0))                 # an address of this PC binds
        finally:
            s.close()


def test_route_address_is_an_address_of_this_pc():
    ip = lines.route_address()
    if not ip:
        pytest.skip("no route out of this machine")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((ip, 0))
    finally:
        s.close()


def test_route_address_asks_about_the_address_given():
    # loopback goes out by loopback, whatever the default route
    assert lines.route_address("127.0.0.1") == "127.0.0.1"


def test_vpn_and_virtual_adapters_are_excluded_but_pppoe_is_kept(monkeypatch):
    """B9: virtual (53), tunnel (131), TAP/Wintun/WireGuard/OpenVPN Ethernet
    adapters, and PPP L2TP/PPTP VPNs are never lines; a PPPoE broadband line
    (23) and physical Wi-Fi/Ethernet still are."""
    adapters = [
        {"name": "Wi-Fi", "up": True, "ipv4": "192.168.1.5", "gateway": "192.168.1.1",
         "if_type": 71, "description": "Intel(R) Wi-Fi 6 AX201"},
        {"name": "Fiber", "up": True, "ipv4": "203.0.113.10", "gateway": "203.0.113.1",
         "if_type": 23, "description": "WAN Miniport (PPPOE)"},
        {"name": "Corp L2TP", "up": True, "ipv4": "10.0.0.2", "gateway": "10.0.0.1",
         "if_type": 23, "description": "WAN Miniport (L2TP)"},
        {"name": "Old PPTP", "up": True, "ipv4": "10.0.1.2", "gateway": "10.0.1.1",
         "if_type": 23, "description": "WAN Miniport (PPTP)"},
        {"name": "TAP", "up": True, "ipv4": "10.8.0.6", "gateway": "10.8.0.5",
         "if_type": 6, "description": "TAP-Windows Adapter V9"},
        {"name": "WG", "up": True, "ipv4": "10.9.0.2", "gateway": "10.9.0.1",
         "if_type": 53, "description": "WireGuard Tunnel"},
        {"name": "Wintun", "up": True, "ipv4": "10.10.0.2", "gateway": "10.10.0.1",
         "if_type": 6, "description": "Wintun Userspace Tunnel"},
        {"name": "DCO", "up": True, "ipv4": "10.11.0.2", "gateway": "10.11.0.1",
         "if_type": 131, "description": "OpenVPN Data Channel Offload"},
    ]
    monkeypatch.setattr(lines, "_adapters", lambda: [dict(a) for a in adapters])
    assert [n for n, _, _ in lines.available()] == ["Wi-Fi", "Fiber"]


def test_vpn_adapters_named_outside_the_old_list_are_not_lines(monkeypatch):
    """A VPN adapter that reports IfType 6 and a gateway, named by its app:
    "vpn" inside a word (ProtonVPN), names without "vpn" (Cisco's renamed
    client, Zscaler, PIA), and one known only by its friendly name."""
    vpns = [
        ("ProtonVPN", "ProtonVPN TUN"),
        ("Ethernet 3", "Cisco Secure Client Virtual Miniport Adapter for Windows x64"),
        ("Ethernet 4", "Zscaler Network Adapter 1.0.2.0"),
        ("Ethernet 5", "Private Internet Access Network Adapter"),
        ("ExpressVPN", "Generic Virtual Adapter"),
    ]
    adapters = [{"name": "Wi-Fi", "up": True, "ipv4": "192.168.1.5", "gateway": "192.168.1.1",
                 "if_type": 71, "description": "Intel(R) Wi-Fi 6 AX201"},
                {"name": "Ethernet 2", "up": True, "ipv4": "172.20.10.2",
                 "gateway": "172.20.10.1", "if_type": 6,
                 "description": "Apple Mobile Device Ethernet"}]
    for i, (name, desc) in enumerate(vpns):
        adapters.append({"name": name, "up": True, "ipv4": "10.20.%d.2" % i,
                         "gateway": "10.20.%d.1" % i, "if_type": 6, "description": desc})
    monkeypatch.setattr(lines, "_adapters", lambda: [dict(a) for a in adapters])
    assert [n for n, _, _ in lines.available()] == ["Wi-Fi", "Ethernet 2"]


def test_a_hyperv_external_switch_with_a_gateway_is_kept_as_a_line(monkeypatch):
    """B9: on a PC with a Hyper-V external virtual switch, 'vEthernet'
    ('Hyper-V Virtual Ethernet Adapter', IfType 6, with default gateway) IS the
    PC's real internet connection; internal/host-only switches have no gateway
    and stay excluded by the gateway rule."""
    adapters = [
        {"name": "vEthernet (External)", "up": True, "ipv4": "192.168.1.10",
         "gateway": "192.168.1.1", "if_type": 6,
         "description": "Hyper-V Virtual Ethernet Adapter"},
        {"name": "vEthernet (WSL)", "up": True, "ipv4": "172.25.0.1",
         "gateway": "", "if_type": 6,
         "description": "Hyper-V Virtual Ethernet Adapter #2"},
    ]
    monkeypatch.setattr(lines, "_adapters", lambda: [dict(a) for a in adapters])
    assert lines.available() == [("vEthernet (External)", "192.168.1.10", "192.168.1.1")]
