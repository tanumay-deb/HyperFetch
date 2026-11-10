"""Where the .sha256 beside a download is looked for, and how much is read
(2026-11-04, list #5).

The suffix went on the end of the whole link: for download.php?id=5 the app
asked for download.php?id=5.sha256, which a site reads as id=5 - the file
itself, read whole into memory. Hash checking (Settings) and Force Recheck
both ask.
"""
import hashlib
import http.server
import threading
import time

import pytest

import task as T
from downloader import Downloader

DIGEST = hashlib.sha256(b"the file").hexdigest()
TOKEN = "0123456789abcdef" * 4          # 64 hex digits that are no checksum
BOM = bytes([0xEF, 0xBB, 0xBF])
PAGES = {
    # a site that answers every address with its page, and says 200
    "/page.bin.sha256": ("<!DOCTYPE html><html><head><script src='/a.js?v=%s'></script>"
                         "</head><body>Not found</body></html>" % TOKEN).encode(),
    "/text.bin.sha256": ("Sorry, no such file. Reference %s." % TOKEN).encode(),
    "/bsd.bin.sha256": ("SHA256 (bsd.bin) = %s\n" % DIGEST.upper()).encode(),
    "/star.bin.sha256": BOM + ("%s *star.bin\r\n" % DIGEST).encode(),
    "/bare.bin.sha256": DIGEST.encode(),
}


@pytest.fixture
def site():
    asked = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            asked.append(self.path)
            path = self.path.split("?")[0]
            if path == "/download.php":          # whatever the query says
                body = b"x" * (2 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/f.bin.sha256":        # a line, then on and on, slowly
                line = ("%s  f.bin\n" % DIGEST).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(line) + 100 * 1024))
                self.end_headers()
                try:
                    self.wfile.write(line)
                    for _ in range(100):
                        self.wfile.write(b"." * 1024)
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass
            elif path in PAGES:
                body = PAGES[path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.asked = asked
    httpd.url = "http://127.0.0.1:%d" % httpd.server_address[1]
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _expected(url, tmp_path):
    return Downloader(T.DownloadTask(url, str(tmp_path / "f.bin")))._fetch_expected_hash()


def test_the_checksum_is_asked_for_beside_the_file_not_in_its_query(site, tmp_path):
    assert _expected(site.url + "/download.php?id=5", tmp_path) is None
    assert site.asked == ["/download.php.sha256?id=5", "/download.php.sha256sum?id=5"]


def test_only_the_start_of_a_checksum_answer_is_read(site, tmp_path):
    t0 = time.monotonic()
    assert _expected(site.url + "/f.bin", tmp_path) == DIGEST
    assert time.monotonic() - t0 < 2, "it read the whole answer"


@pytest.mark.parametrize("name", ["page.bin", "text.bin"])
def test_a_web_page_where_the_checksum_would_be_is_no_checksum(site, tmp_path, name):
    """Any 64 hex digits in the answer were taken for it, and a good download
    read "hash mismatch" (2026-11-06)."""
    assert _expected(site.url + "/" + name, tmp_path) is None


@pytest.mark.parametrize("name", ["bsd.bin", "star.bin", "bare.bin"])
def test_the_usual_shapes_of_a_checksum_file_are_read(site, tmp_path, name):
    assert _expected(site.url + "/" + name, tmp_path) == DIGEST
