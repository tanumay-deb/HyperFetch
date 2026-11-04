"""A server that answers a range with less than was asked still gets the
whole file.

HTTP lets a server send a shorter range than the one asked for. Each part
took one answer and stopped, and the download ended "Paused" with no reason:
measured against a server sending 1 MB an answer, a 5 MB file stopped at
2 MB (2026-11-03). The rest of a part is asked for now - and a server that
sends nothing at all for a range is not asked forever.
"""
import http.server
import threading

import pytest

import task as T
from downloader import Downloader

SIZE = 5 * 1024 * 1024
DATA = bytes(i % 251 for i in range(SIZE))


def _server(cap):
    """A server answering every range with at most `cap` bytes of it."""
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(SIZE))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

        def do_GET(self):
            a, _, b = self.headers.get("Range", "bytes=0-")[6:].partition("-")
            start = int(a)
            end = min(int(b) if b else SIZE - 1, start + cap - 1) if cap else start - 1
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, SIZE))
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(DATA[start:end + 1])

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/file.bin" % srv.server_address[1]


@pytest.fixture
def capped():
    srv, url = _server(1024 * 1024)
    yield url
    srv.shutdown()


@pytest.fixture
def empty():
    srv, url = _server(0)
    yield url
    srv.shutdown()


def test_a_server_that_sends_a_megabyte_at_a_time_gives_the_whole_file(capped, tmp_path):
    t = T.DownloadTask(capped, str(tmp_path / "file.bin"), filename="file.bin")
    Downloader(t, segments=2).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (tmp_path / "file.bin").read_bytes() == DATA


def test_one_that_sends_nothing_for_a_range_is_not_asked_forever(empty, tmp_path):
    t = T.DownloadTask(empty, str(tmp_path / "file.bin"), filename="file.bin")
    done = threading.Event()
    threading.Thread(target=lambda: (Downloader(t, segments=2).run(), done.set()),
                     daemon=True).start()
    assert done.wait(30), "it asked the server for the same range forever"
    assert t.status == T.ERROR and "Resume" in t.error, (t.status, t.error)
