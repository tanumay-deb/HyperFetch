"""A download caught on a signed link goes on after that link has run out.

Chrome and Edge hand the app the address a download ended at, and for GitHub's
release files (S3, CloudFront and the like too) that is a signed link that
runs out: GitHub's within the hour, measured 2026-11-03. A capture that waited
its turn that long, or was paused and resumed later, failed "URL expired". The
extension sends the link the download started at as well, and the app keeps it
as a second link (task.mirrors), which takes over when the first is refused -
also before a byte came, which the size check used to ask the first link alone.
"""
import http.server
import threading
from collections import deque

import pytest

from test_torrent_duplicates import _Box, window  # noqa: F401

import task as T
from api_server import create_app
from downloader import Downloader

SIGNED = "https://release-assets.example/f?sp=r&se=2026-11-03T13%3A19%3A56Z&sig=AAA"
ORIG = "https://github.example/o/r/releases/download/v1/f.zip"


class _Queue:
    def __init__(self):
        self.tasks = []

    def add_task(self, t, start=True):
        self.tasks.append(t)


def test_the_server_keeps_the_link_it_started_at(tmp_path):
    q = _Queue()
    c = create_app(q, str(tmp_path), pending=None).test_client()
    for url, orig, kept in ((SIGNED, ORIG, [ORIG]),
                            ("https://cdn.example/f.zip", ORIG, []),     # not signed
                            (SIGNED, "javascript:alert(1)", [])):         # not a web link
        assert c.post("/download", json={"url": url, "filename": "f.zip",
                                         "origUrl": orig}).status_code == 200
        assert q.tasks[-1].mirrors == kept, (url, orig)


def test_the_window_is_handed_it_and_the_stream_s_master(tmp_path):
    pending = deque()
    c = create_app(_Queue(), str(tmp_path), pending=pending).test_client()
    c.post("/download", json={"url": SIGNED, "origUrl": ORIG,
                              "hlsMaster": "https://v.example/master.m3u8"})
    assert pending[0]["orig_url"] == ORIG
    assert pending[0]["hls_master"] == "https://v.example/master.m3u8"


def test_the_window_keeps_it_as_a_second_link(window, tmp_path):
    window.save_dir = str(tmp_path)
    from gui2.app import DownloadAppV2
    DownloadAppV2._add_download(window, SIGNED, "f.zip", None, orig_url=ORIG)
    DownloadAppV2._add_download(window, "https://cdn.example/g.zip", "g.zip", None,
                                orig_url="https://cdn.example/start/g.zip")
    assert window.added[0].mirrors == [ORIG]
    assert window.added[1].mirrors == [], "a link that does not run out got a second one"


@pytest.fixture
def github(tmp_path):
    """A signed link that has run out, and the link that signs a new one: a
    redirect to the file, as GitHub's release links are."""
    data = bytes(i % 251 for i in range(3 * 1024 * 1024))

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _answer(self, body):
            p = self.path.split("?")[0]
            if p == "/signed":
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif p == "/releases/f.zip":
                self.send_response(302)
                self.send_header("Location", "/file.zip")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                a, _, b = (self.headers.get("Range") or "bytes=0-")[6:].partition("-")
                start, end = int(a), int(b) if b else len(data) - 1
                self.send_response(206 if self.headers.get("Range") else 200)
                if self.headers.get("Range"):
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                if body:
                    self.wfile.write(data[start:end + 1])

        def do_HEAD(self):
            self._answer(False)

        def do_GET(self):
            self._answer(True)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    yield base, data
    srv.shutdown()


def test_one_that_waited_past_its_signed_link_still_comes(github, tmp_path):
    base, data = github
    t = T.DownloadTask(base + "/signed?sig=AAA", str(tmp_path / "f.zip"), filename="f.zip")
    t.mirrors = [base + "/releases/f.zip"]
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (tmp_path / "f.zip").read_bytes() == data
    assert any("Stopped using the link" in e["message"] for e in t.events)
