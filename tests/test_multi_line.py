"""A download spread over several internet connections ("lines";
specs/2026-11-13-multi-line-downloads-design.md).

Two lines are stood in for by two loopback addresses: a connection bound to
127.0.0.2 reaches a server on 127.0.0.1 and the server sees 127.0.0.2, as
it would see a second line's address. 192.0.2.x (TEST-NET-1) is on no PC,
so a connection bound to it fails at once, as one through an unplugged
phone does."""
import collections
import http.server
import threading
import time

import pytest

import lines
import task as T
import utils
from downloader import Downloader, parts_for

MiB = 1024 * 1024


class _LineServer:
    """Ranges at a rate per client address; counts what each was sent."""

    def __init__(self, data):
        self.data = data
        self.rate = {}                          # client ip -> bytes/s
        self.refuse = set()                     # client ips answered 403
        self.sent = collections.Counter()       # client ip -> bytes
        self.ranges = collections.Counter()     # client ip -> range requests
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _go(self, head):
                ip = self.client_address[0]
                if ip in srv.refuse:
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                d = srv.data
                rng = self.headers.get("Range")
                if rng:
                    a, _, b = rng.split("=", 1)[1].partition("-")
                    start, end = int(a), min(int(b) if b else len(d) - 1, len(d) - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(d)}")
                    srv.ranges[ip] += 1
                else:
                    start, end = 0, len(d) - 1
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                if head:
                    return
                rate, t0, pos = srv.rate.get(ip, 0), time.monotonic(), start
                while pos <= end:
                    chunk = d[pos:min(pos + 65536, end + 1)]
                    self.wfile.write(chunk)
                    pos += len(chunk)
                    srv.sent[ip] += len(chunk)
                    if rate:
                        ahead = (pos - start) / rate - (time.monotonic() - t0)
                        if ahead > 0:
                            time.sleep(ahead)

            def do_HEAD(self):
                self._go(True)

            def do_GET(self):
                self._go(False)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name="f.bin"):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{name}"


@pytest.fixture
def line_server(make_payload):
    servers = []

    def make(size):
        s = _LineServer(make_payload(size))
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.httpd.shutdown()
        s.httpd.server_close()


@pytest.fixture
def two_lines(monkeypatch):
    """Lines "A" and "B" at the addresses given."""
    def use(a="127.0.0.1", b="127.0.0.2"):
        found = [("A", a), ("B", b)]
        monkeypatch.setattr(lines, "addresses",
                            lambda names: [x for x in found if x[0] in names])
        monkeypatch.setattr(lines, "route_address", lambda: a)     # traffic leaves by A
        monkeypatch.setattr(utils, "LINES", ["A", "B"])
    return use


def _run(srv, tmp_path, segments=8):
    t = T.DownloadTask(srv.url(), str(tmp_path / "f.bin"))
    d = Downloader(t, segments=segments)
    d.run()
    return t, d


def _said(t, words):
    return any(words in e["message"] for e in t.events)


def test_two_lines_share_the_file_and_the_faster_serves_more(line_server, two_lines, tmp_path):
    srv = line_server(32 * MiB)
    srv.rate = {"127.0.0.1": 16 * MiB, "127.0.0.2": 4 * MiB}
    two_lines()
    t, _ = _run(srv, tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data
    assert srv.ranges["127.0.0.1"] and srv.ranges["127.0.0.2"]
    assert srv.sent["127.0.0.1"] > srv.sent["127.0.0.2"]
    assert _said(t, "2 internet connections")


def test_a_line_that_cannot_connect_is_dropped(line_server, two_lines, tmp_path):
    srv = line_server(8 * MiB)
    srv.rate = {"127.0.0.1": 8 * MiB}         # A busy (see the next test)
    two_lines(b="192.0.2.1")
    t, _ = _run(srv, tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data
    assert _said(t, "Stopped using B")


def test_a_line_the_server_refuses_is_dropped_not_an_error(line_server, two_lines, tmp_path):
    srv = line_server(8 * MiB)
    srv.refuse = {"127.0.0.2"}               # a link tied to the first address
    # A busy: with nothing of A's running, B's part could go to A at its
    # first refusal (the first of equals), and B never fail twice
    srv.rate = {"127.0.0.1": 8 * MiB}
    two_lines()
    t, _ = _run(srv, tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data
    assert _said(t, "Stopped using B")


def test_every_line_dropped_goes_on_unbound(line_server, two_lines, tmp_path):
    srv = line_server(8 * MiB)
    two_lines(a="192.0.2.1", b="192.0.2.2")
    t, _ = _run(srv, tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data


@pytest.mark.parametrize("names", [[], ["A"]])
def test_one_line_or_off_changes_nothing(line_server, two_lines, monkeypatch, tmp_path, names):
    srv = line_server(32 * MiB)
    two_lines()
    monkeypatch.setattr(utils, "LINES", names)
    t, d = _run(srv, tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert len(t.segments) == parts_for(32 * MiB, 8)
    assert d._lines == [] and not d._line_sessions
    assert srv.ranges["127.0.0.2"] == 0


def test_parts_for_two_lines(line_server, two_lines, tmp_path):
    two_lines()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    big = line_server(64 * MiB)
    t, d = _run(big, tmp_path / "a")
    assert t.status == T.COMPLETED, t.error
    assert len(t.segments) == 64 and d._conn_cap == 16
    small = line_server(10 * MiB)
    t, d = _run(small, tmp_path / "b")
    assert t.status == T.COMPLETED, t.error
    assert len(t.segments) == 10


def test_parts_cut_for_two_lines_resume_on_one_line_count(line_server, monkeypatch, tmp_path):
    """Lines off on the resume of a download cut into 64 parts for two lines:
    it runs on the connections the setting gives, not one per part."""
    srv = line_server(64 * MiB)
    monkeypatch.setattr(utils, "LINES", [])
    t = T.DownloadTask(srv.url(), str(tmp_path / "f.bin"))
    t.total_size, t.supports_range = 64 * MiB, True
    part = MiB
    t.segments = [T.Segment(i, i * part, (i + 1) * part - 1) for i in range(64)]
    d = Downloader(t, segments=8)
    d.run()
    assert t.status == T.COMPLETED, t.error
    assert d._conn_cap == 8


def test_proxy_or_relay_keeps_one_line(two_lines, monkeypatch, tmp_path):
    import socket
    two_lines()
    t = T.DownloadTask("http://files.example/f.bin", str(tmp_path / "f.bin"))
    t.total_size, t.supports_range = 64 * MiB, True
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))])
    assert len(Downloader(t, segments=8)._pick_lines()) == 2
    monkeypatch.setattr(utils, "PROXIES", {"http": "http://127.0.0.1:3128",
                                           "https": "http://127.0.0.1:3128"})
    assert Downloader(t, segments=8)._pick_lines() == []
    monkeypatch.setattr(utils, "PROXIES", None)
    # "Get past provider blocks": the name resolves to the local relay
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 50123))])
    assert Downloader(t, segments=8)._pick_lines() == []


def test_traffic_through_another_adapter_keeps_one_line(two_lines, monkeypatch, tmp_path):
    """A VPN or tunnel adapter that carries this PC's traffic (2026-11-17: one
    named "net-merge" held the default route) would be gone around by
    connections bound to the lines: the download stays on one, and says why."""
    two_lines()
    monkeypatch.setattr(lines, "route_address", lambda: "198.18.0.1")
    t = T.DownloadTask("http://127.0.0.1:9/f.bin", str(tmp_path / "f.bin"))
    t.total_size, t.supports_range = 64 * MiB, True
    assert Downloader(t, segments=8)._pick_lines() == []
    assert _said(t, "another adapter")
    monkeypatch.setattr(lines, "route_address", lambda: "")     # cannot tell: lines kept
    assert len(Downloader(t, segments=8)._pick_lines()) == 2
