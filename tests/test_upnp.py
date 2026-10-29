"""UPnP: the search goes out where the router is, and what it says is logged.

Measured on this machine (2026-10-22, Wi-Fi + Tailscale): the app's search
had no answer from any device - Windows sent the multicast out of another
adapter - while the same search from the Wi-Fi address had 24, three of
them the router's Internet gateway. The torrent port had never been
opened, and nothing said so: map_port's result was thrown away.
"""
import http.server
import logging
import socket
import threading

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
    ("http://r/desc.xml", [], None, "offers no port mapping"),
    ("http://r/desc.xml", [("http://r/ctl", "svc")], {"TCP": "", "UDP": ""}, "opened torrent port"),
    ("http://r/desc.xml", [("http://r/ctl", "svc")], {"TCP": "HTTP 401", "UDP": "HTTP 401"},
     "refused to open torrent port 51413 (TCP: HTTP 401, UDP: HTTP 401)"),
])
def test_what_the_router_said_is_logged(monkeypatch, caplog, found, control, said, expect):
    monkeypatch.setattr(upnp, "_discover", lambda: found)
    monkeypatch.setattr(upnp, "_control_urls", lambda loc: control)
    monkeypatch.setattr(upnp, "_local_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(upnp, "_add_mapping", lambda c, s, port, proto, ip: said[proto])
    with caplog.at_level(logging.INFO, logger="hyperfetch.upnp"):
        ok = upnp.map_port(51413)
    assert expect in caplog.text
    assert ok == (expect == "opened torrent port")


# ---- a WAN service the line does not use, listed first ----------------------------
# As on the user's router (2026-10-29: Airtel, Mediatek): WANIPConnection
# first, answering every action with UPnP error 401 "Invalid Action", and
# WANPPPConnection, the line, after it. Only the first was ever asked. Here
# the PPP service maps, as the standard one does; the user's has no
# AddPortMapping at all, so theirs still opens nothing.
_DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">
<URLBase>http://127.0.0.1:%(port)d</URLBase><device><serviceList>
<service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType>
<controlURL>/ctrl?1</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctrl?11</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:WANPPPConnection:1</serviceType>
<controlURL>/ctrl?12</controlURL></service>
</serviceList></device></root>"""
_INVALID_ACTION = ("<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\"><s:Body>"
                   "<s:Fault><detail><UPnPError><errorCode>401</errorCode>"
                   "<errorDescription>Invalid Action</errorDescription></UPnPError>"
                   "</detail></s:Fault></s:Body></s:Envelope>")


@pytest.fixture
def router():
    mapped = []

    class Router(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (_DESC % {"port": self.server.server_port}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"])).decode()
            action = self.headers["SOAPAction"]
            if self.path == "/ctrl?12" and "WANPPPConnection:1#AddPortMapping" in action:
                proto = data.split("<NewProtocol>")[1].split("<")[0]
                mapped.append((proto, data.split("<NewExternalPort>")[1].split("<")[0],
                               data.split("<NewInternalClient>")[1].split("<")[0]))
                body, code = b"<ok/>", 200
            else:
                body, code = _INVALID_ACTION.encode(), 500
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Router)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.mapped = mapped
    yield srv
    srv.shutdown()
    srv.server_close()


def test_the_port_is_mapped_on_the_service_the_line_uses(monkeypatch, caplog, router):
    monkeypatch.setattr(upnp, "_discover",
                        lambda: "http://127.0.0.1:%d/DeviceDescription.xml" % router.server_port)
    monkeypatch.setattr(upnp, "_local_ip", lambda: "192.168.1.20")
    with caplog.at_level(logging.INFO, logger="hyperfetch.upnp"):
        assert upnp.map_port(56666)
    assert router.mapped == [("TCP", "56666", "192.168.1.20"), ("UDP", "56666", "192.168.1.20")]
    assert "opened torrent port 56666 (TCP+UDP)" in caplog.text
