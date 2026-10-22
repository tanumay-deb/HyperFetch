"""UPnP: the search goes out where the router is, and what it says is logged.

Measured on this machine (2026-10-22, Wi-Fi + Tailscale): the app's search
had no answer from any device - Windows sent the multicast out of another
adapter - while the same search from the Wi-Fi address had 24, three of
them the router's Internet gateway. The torrent port had never been
opened, and nothing said so: map_port's result was thrown away.
"""
import logging
import socket

import pytest

import upnp


class _Sock:
    """A UDP socket that records how it was set up; nobody answers."""
    made = []

    def __init__(self, *a):
        self.opts, self.bound, self.sent = [], None, []
        _Sock.made.append(self)

    def setsockopt(self, level, name, value):
        self.opts.append((level, name, value))

    def bind(self, addr):
        self.bound = addr

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        self.sent.append(addr)

    def recvfrom(self, n):
        raise socket.timeout()

    def close(self):
        pass


def test_the_search_goes_out_from_the_default_route(monkeypatch):
    _Sock.made = []
    monkeypatch.setattr(upnp, "_local_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(upnp.socket, "socket", _Sock)
    assert upnp._discover(timeout=0.01) is None
    s = _Sock.made[-1]
    assert s.bound == ("192.168.1.20", 0)
    assert (socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
            socket.inet_aton("192.168.1.20")) in s.opts
    assert s.sent == [upnp._SSDP_ADDR]


def test_no_address_still_searches(monkeypatch):
    _Sock.made = []
    monkeypatch.setattr(upnp, "_local_ip", lambda: "")
    monkeypatch.setattr(upnp.socket, "socket", _Sock)
    upnp._discover(timeout=0.01)
    assert _Sock.made[-1].sent == [upnp._SSDP_ADDR] and _Sock.made[-1].bound is None


@pytest.mark.parametrize("found, control, said, expect", [
    (None, None, None, "no router answered"),
    ("http://r/desc.xml", (None, None), None, "offers no port mapping"),
    ("http://r/desc.xml", ("http://r/ctl", "svc"), {"TCP": "", "UDP": ""}, "opened torrent port"),
    ("http://r/desc.xml", ("http://r/ctl", "svc"), {"TCP": "HTTP 401", "UDP": "HTTP 401"},
     "refused to open torrent port 51413 (TCP: HTTP 401, UDP: HTTP 401)"),
])
def test_what_the_router_said_is_logged(monkeypatch, caplog, found, control, said, expect):
    monkeypatch.setattr(upnp, "_discover", lambda: found)
    monkeypatch.setattr(upnp, "_control_url", lambda loc: control)
    monkeypatch.setattr(upnp, "_local_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(upnp, "_add_mapping", lambda c, s, port, proto, ip: said[proto])
    with caplog.at_level(logging.INFO, logger="hyperfetch.upnp"):
        ok = upnp.map_port(51413)
    assert expect in caplog.text
    assert ok == (expect == "opened torrent port")
