"""How ordinary web servers answer, met by the HTTP engine (the third sweep,
2026-11-17): each case was measured with a stand-in server first."""
import gzip
import http.server
import os
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

    def __init__(self, data, host="127.0.0.1"):
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

        self.host = host
        self.httpd = http.server.ThreadingHTTPServer((host, 0), H)
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
        return f"http://{self.host}:{self.httpd.server_address[1]}{path}"


@pytest.fixture
def server():
    made = []

    def make(data, host="127.0.0.1"):
        s = _Server(data, host)
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


def _signed_in_site(srv, to):
    """/download.php redirects to `to`; anything else wants the sign-in cookie
    and answers a sign-in page without it, as such a site does."""
    def answer(h, head):
        if h.path.startswith("/download.php"):
            h.send_response(302)
            h.send_header("Location", to)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        if "sid=1" not in (h.headers.get("Cookie") or ""):
            page = b"<html><body>Please sign in</body></html>"
            h.send_response(403)
            h.send_header("Content-Type", "text/html")
            h.send_header("Content-Length", str(len(page)))
            h.end_headers()
            if not head:
                h.wfile.write(page)
            return True
        return False
    srv.answer = answer


def test_a_redirect_on_the_same_site_keeps_the_sign_in(server, make_payload, tmp_path):
    """requests drops a Cookie header on every redirect: the engine met the
    site's sign-in page, gave the link to yt-dlp, and the PDF finished as
    "report.unknown_video" on one connection."""
    srv = server(make_payload(3 * 1024 * 1024))
    _signed_in_site(srv, "/files/report.pdf")
    t = T.DownloadTask(srv.url("/download.php?id=5"), str(tmp_path / "report.pdf"),
                       filename="report.pdf", headers={"Cookie": "sid=1"})
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert not t.use_ytdlp
    assert t.save_path.endswith("report.pdf")
    assert open(t.save_path, "rb").read() == srv.data
    to_file = [hd for _, path, hd in srv.requests if path.startswith("/files/")]
    assert to_file and all("sid=1" in hd.get("Cookie", "") for hd in to_file)


def test_a_redirect_to_another_host_takes_no_sign_in(server, make_payload, tmp_path):
    other = server(make_payload(1024 * 1024), host="127.0.0.2")
    first = server(b"")
    _signed_in_site(first, other.url("/f.bin"))
    t = T.DownloadTask(first.url("/download.php?id=5"), str(tmp_path / "f.bin"),
                       filename="f.bin", headers={"Cookie": "sid=1"})
    Downloader(t, segments=4).run()
    assert other.requests
    assert not any(hd.get("Cookie") for _, _, hd in other.requests)


def _named_by(srv, disposition):
    def answer(h, head):
        srv.send(h, srv.data, head, [("Content-Type", "application/pdf"),
                                     ("Content-Disposition", disposition)])
        return True
    srv.answer = answer


@pytest.mark.parametrize("disposition, name", [
    ('attachment; filename="Report 2026.pdf"', "Report 2026.pdf"),
    # RFC 6266: filename* before filename, whichever comes first
    ("attachment; filename=\"fallback.pdf\"; filename*=UTF-8''%E6%96%87%E4%BB%B6%20A.pdf",
     "文件 A.pdf"),
    ("attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6%20A.pdf; filename=\"fallback.pdf\"",
     "文件 A.pdf"),
    ('attachment; filename="../../evil.exe"', "evil.exe"),     # a name, never a path
])
def test_a_link_takes_the_name_the_server_gives(server, make_payload, tmp_path, disposition, name):
    """download.php?id=5 is a PDF the server calls "Report 2026.pdf"; it was
    saved as download.php. It is filed by the name the server gives."""
    srv = server(make_payload(2 * 1024 * 1024))
    _named_by(srv, disposition)
    url = srv.url("/download.php?id=5")
    import utils
    fn = utils.filename_from_url(url)
    folder, sort_base = utils.place_download(str(tmp_path), url, fn)
    t = T.DownloadTask(url, str(utils.unique_path(folder, fn)), filename=fn)
    t.sort_base = sort_base
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == name
    assert t.save_path == os.path.join(utils.get_category_dir(str(tmp_path), name), name)
    assert open(t.save_path, "rb").read() == srv.data
    # the folder made for the link's name is not left behind empty
    assert not os.path.isdir(folder) or os.listdir(folder) or folder == os.path.dirname(t.save_path)


@pytest.mark.parametrize("how", ["typed", "browser"])
def test_a_name_someone_chose_is_kept(server, make_payload, tmp_path, how):
    """A name typed in New Download, or the browser's (it read the header
    itself), is not the link's: the server's does not replace it."""
    srv = server(make_payload(2 * 1024 * 1024))
    _named_by(srv, 'attachment; filename="Report 2026.pdf"')
    name = "My copy.pdf" if how == "typed" else "Quarterly.pdf"
    t = T.DownloadTask(srv.url("/download.php?id=5"), str(tmp_path / name), filename=name)
    if how == "typed":
        t.name_chosen = "My copy"
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == name and t.save_path == str(tmp_path / name)


@pytest.mark.parametrize("before, after, kept", [
    ("https://site.example/a", "https://site.example/b", True),
    ("http://site.example/a", "https://site.example/b", True),
    ("https://site.example/a", "http://site.example/b", False),    # never in the clear
    ("https://site.example/a", "https://cdn.example/b", False),
])
def test_which_redirects_keep_the_sign_in(before, after, kept):
    import requests
    from downloader import _Session
    prev = requests.Request("GET", before, headers={"Cookie": "sid=1"}).prepare()
    resp = requests.Response()
    resp.request = prev
    nxt = requests.Request("GET", after).prepare()
    _Session().rebuild_auth(nxt, resp)
    assert ("sid=1" in nxt.headers.get("Cookie", "")) == kept


@pytest.mark.parametrize("length", ["0", None])    # said, or a stream that ends at once
def test_an_empty_file_is_a_finished_download(server, tmp_path, length):
    """A file of no bytes - a placeholder, an empty .txt - ended "Paused" with
    no reason, and every Resume the same."""
    srv = server(b"")

    def answer(h, head):
        h.send_response(200)
        h.send_header("Content-Type", "text/plain")
        if length is not None:
            h.send_header("Content-Length", length)
        else:
            h.send_header("Connection", "close")
        h.end_headers()
        return True
    srv.answer = answer
    t = T.DownloadTask(srv.url("/empty.txt"), str(tmp_path / "empty.txt"), filename="empty.txt")
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert os.path.getsize(t.save_path) == 0
