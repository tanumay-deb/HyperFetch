"""How ordinary web servers answer, met by the HTTP engine (the third sweep,
2026-11-17): each case was measured with a stand-in server first."""
import gzip
import http.server
import random
import threading

import pytest

import task as T
from downloader import Downloader


def _csv(rows):
    """Text that packs to about a third: big enough packed to be cut in parts."""
    rnd = random.Random(1)
    return "".join(f"{i},{rnd.random():.9f}\n" for i in range(rows)).encode()


class _Server:
    """One file at any path. `.answer(handler, head)` may answer a request
    itself (return True); else the file is served with byte ranges."""

    def __init__(self, data):
        self.data = data
        self.requests = []
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self._go(True)

            def do_GET(self):
                self._go(False)

            def _go(self, head):
                srv.requests.append((self.command, self.path, dict(self.headers)))
                if srv.answer(self, head):
                    return
                srv.send(self, srv.data, head)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def answer(self, h, head):
        return False

    @staticmethod
    def send(h, d, head, extra=()):
        rng = h.headers.get("Range")
        if rng:
            a, _, b = rng.split("=", 1)[1].partition("-")
            s, e = int(a), min(int(b) if b else len(d) - 1, len(d) - 1)
            h.send_response(206)
            h.send_header("Content-Range", f"bytes {s}-{e}/{len(d)}")
        else:
            s, e = 0, len(d) - 1
            h.send_response(200)
        for k, v in extra:
            h.send_header(k, v)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(e - s + 1))
        h.end_headers()
        if not head:
            h.wfile.write(d[s:e + 1])

    def url(self, path):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"


@pytest.fixture
def server():
    made = []

    def make(data):
        s = _Server(data)
        made.append(s)
        return s
    yield make
    for s in made:
        s.httpd.shutdown()
        s.httpd.server_close()


def test_a_server_that_sends_a_compressed_copy_is_asked_for_the_file(server, tmp_path):
    """nginx gzip_static: a client that accepts gzip gets the ready-compressed
    copy, its size and ranges over ITS bytes. A slice from its middle cannot
    be unpacked: the download failed "Connection lost". Asked for the file as
    it is stored, the server sends that."""
    raw = _csv(400000)
    packed = gzip.compress(raw, 6)
    assert len(packed) > 2 * 1024 * 1024          # cut in parts
    srv = server(raw)

    def answer(h, head):
        if "gzip" in (h.headers.get("Accept-Encoding") or ""):
            srv.send(h, packed, head, [("Content-Encoding", "gzip"), ("Content-Type", "text/csv")])
            return True
        return False
    srv.answer = answer
    t = T.DownloadTask(srv.url("/data.csv"), str(tmp_path / "data.csv"), filename="data.csv")
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == raw
