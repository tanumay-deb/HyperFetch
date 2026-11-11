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
    "/json.bin.sha256": ('{"name": "json.bin", "sha256": "%s"}' % DIGEST).encode(),
    # one list for every file of a release, answered at each file's .sha256
    "/multi.bin.sha256": ("%s *setup.exe\n%s *multi.bin\n"
                          % (hashlib.sha256(b"another").hexdigest(), DIGEST)).encode(),
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


@pytest.mark.parametrize("name", ["bsd.bin", "star.bin", "bare.bin", "json.bin", "multi.bin"])
def test_the_usual_shapes_of_a_checksum_file_are_read(site, tmp_path, name):
    assert _expected(site.url + "/" + name, tmp_path) == DIGEST


@pytest.mark.parametrize("text", [
    "SHA2-256(f.bin)= %s" % DIGEST,                          # openssl 3
    "SHA256(f.bin)= %s" % DIGEST,                            # openssl 1
    "sha256 (f.bin) = %s" % DIGEST,
    "sha256: %s" % DIGEST,
    "SHA-256=%s" % DIGEST.upper(),
    "Hash      : %s" % DIGEST.upper(),                       # Get-FileHash | Format-List
    "SHA256          %s       C:/dl/f.bin" % DIGEST.upper(),     # its table's row
    "Algorithm : SHA256\nHash      : %s\nPath      : C:/dl/f.bin" % DIGEST.upper(),
])
def test_a_checksum_after_its_label_is_read(text):
    """Shapes the first cut of checksum_in turned away, which "any 64 hex
    digits" had taken: such a file read as no checksum at all."""
    from downloader import checksum_in
    assert checksum_in(text) == DIGEST


@pytest.mark.parametrize("text", [
    "Sorry, no such file. Reference %s." % TOKEN,
    '{"error": "not found", "request": "%s"}' % TOKEN,
    "  <html><body>%s</body></html>" % TOKEN,
    "token=%s" % TOKEN,
    TOKEN + TOKEN,                                           # a SHA-512, not this
])
def test_hex_digits_that_are_no_checksum_line(text):
    from downloader import checksum_in
    assert checksum_in(text) == ""


@pytest.mark.parametrize("text", [
    '{"sha256": "%s"}' % DIGEST,
    '{\n  "file": "f.bin",\n  "sha256": "%s"\n}\n' % DIGEST.upper(),
    '{"algorithm": "SHA-256", "hash": "%s"}' % DIGEST,
    '{"digest": "sha256:%s"}' % DIGEST,
    '{"md5": "%s", "SHA256": "%s"}' % ("0" * 32, DIGEST),
    '[{"name": "f.bin", "checksums": {"sha256": "%s"}}]' % DIGEST,
])
def test_a_checksum_sent_as_json_is_read(text):
    """Read while "any 64 hex digits" did; the line rule that came after it
    turned every JSON answer away (2026-11-11)."""
    from downloader import checksum_in
    assert checksum_in(text) == DIGEST


@pytest.mark.parametrize("text", [
    '{"token": "%s"}' % TOKEN,
    '{"algorithm": "blake3", "hash": "%s"}' % TOKEN,                 # 64 hex, not SHA-256
    '{"files": [{"name": "a", "sha256": "%s"}, {"name": "b", "sha256": "%s"}]}'
    % (DIGEST, TOKEN),                                               # which one is ours?
    '{"sha256": ',                                                   # cut short
    "[" * 5000,                                                      # nested past reading
])
def test_json_that_names_no_one_sha256(text):
    from downloader import checksum_in
    assert checksum_in(text) == ""


def test_a_list_of_several_files_gives_this_ones_sum():
    """The first line was taken, another file's: a good download read "hash
    mismatch" (2026-11-12). This file's line, or none."""
    from downloader import checksum_in
    other = hashlib.sha256(b"another").hexdigest()
    listed = "%s  setup.exe\n%s  dir/f.bin\n" % (other, DIGEST)
    assert checksum_in(listed, ["f.bin"]) == DIGEST
    assert checksum_in(listed, ["F.BIN"]) == DIGEST
    assert checksum_in("SHA256 (setup.exe) = %s\nSHA256 (f.bin) = %s\n" % (other, DIGEST),
                       ["f.bin"]) == DIGEST
    assert checksum_in(listed, ["missing.bin"]) == "", "another file's sum: a false mismatch"
    assert checksum_in(listed) == ""
    assert checksum_in("%s  f.bin\n%s  f.bin\n" % (DIGEST, DIGEST)) == DIGEST, "one sum, twice"
