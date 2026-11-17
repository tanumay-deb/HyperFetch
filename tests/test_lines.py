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


def test_addresses_in_the_order_given_and_only_present(table):
    assert lines.addresses(["Phone", "Gone", "Wi-Fi"]) == [
        ("Phone", "192.168.42.7"), ("Wi-Fi", "192.168.1.5")]


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
