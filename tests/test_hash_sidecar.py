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
