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
    from downloader import Session
    prev = requests.Request("GET", before, headers={"Cookie": "sid=1"}).prepare()
    resp = requests.Response()
    resp.request = prev
    nxt = requests.Request("GET", after).prepare()
    Session().rebuild_auth(nxt, resp)
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


def _leads_to(srv, to):
    def answer(h, head):
        if not h.path.startswith(to.split("?")[0]):
            h.send_response(302)
            h.send_header("Location", to)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        srv.send(h, srv.data, head, [("Content-Type", "application/octet-stream")])
        return True
    srv.answer = answer


@pytest.mark.parametrize("link, to, name", [
    # download.mozilla.org/?product=... and go.microsoft.com/fwlink/?linkid=...
    ("/?product=firefox-latest&os=win64", "/pub/Firefox%20Setup%20133.0.exe",
     "Firefox Setup 133.0.exe"),
    ("/fwlink/?linkid=2084649", "/vs/vs_Community.exe", "vs_Community.exe"),
    # where it leads names no type: the link's name stays
    ("/fwlink/?linkid=7", "/blob/a81f0c", "download.bin"),
])
def test_a_link_that_leads_to_a_file_takes_its_name(server, make_payload, tmp_path, link, to, name):
    """No Content-Disposition: the browser names the file by where the link
    leads, and so does HyperFetch now - it was "download.bin"."""
    srv = server(make_payload(2 * 1024 * 1024))
    _leads_to(srv, to)
    import utils
    url = srv.url(link)
    fn = utils.filename_from_url(url)
    folder, sort_base = utils.place_download(str(tmp_path), url, fn)
    t = T.DownloadTask(url, str(utils.unique_path(folder, fn)), filename=fn)
    t.sort_base = sort_base
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == name
    assert open(t.save_path, "rb").read() == srv.data


def test_a_link_led_to_a_sign_in_page_keeps_its_name(server, tmp_path):
    srv = server(b"<html>sign in</html>")

    def answer(h, head):
        if not h.path.startswith("/login.php"):
            h.send_response(302)
            h.send_header("Location", "/login.php?next=/report")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        srv.send(h, srv.data, head, [("Content-Type", "text/html")])
        return True
    srv.answer = answer
    t = T.DownloadTask(srv.url("/report"), str(tmp_path / "report.bin"), filename="report.bin")
    Downloader(t, segments=4).run()
    assert t.filename == "report.bin"


def test_a_named_attachment_is_a_file_whatever_its_type(server, make_payload, tmp_path):
    """A download.php that names its file but leaves PHP's default type
    (text/html): the browser saves it - an attachment is a file. It failed
    "Server sent a web page"."""
    srv = server(make_payload(2 * 1024 * 1024))

    def answer(h, head):
        srv.send(h, srv.data, head, [("Content-Type", "text/html; charset=UTF-8"),
                                     ("Content-Disposition", 'attachment; filename="backup.zip"')])
        return True
    srv.answer = answer
    url = srv.url("/download.php?id=9")
    t = T.DownloadTask(url, str(tmp_path / "download.php"), filename="download.php")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == "backup.zip"
    assert open(t.save_path, "rb").read() == srv.data


def test_a_signed_in_torrent_link_that_redirects_keeps_the_sign_in(server, tmp_path):
    """A private tracker's download.php?id=... that redirects on its own
    site to the .torrent: its fetch lost the cookie and met the sign-in page
    ("not a .torrent")."""
    import torrent
    meta = (b"d8:announce18:http://t.example/a4:infod6:lengthi5e4:name5:a.txt"
            b"12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee")
    srv = server(meta)
    _signed_in_site(srv, "/torrents/a.torrent")
    got = torrent.fetch_torrent(srv.url("/download.php?id=5"), {"Cookie": "sid=1"})
    assert got == meta


def test_a_signed_in_stream_that_redirects_keeps_the_sign_in(server):
    import hls
    master = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n"
              b"low.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720\nhigh.m3u8\n")
    srv = server(master)
    _signed_in_site(srv, "/hls/master.m3u8")
    found = hls.probe(srv.url("/download.php?id=5"), {"Cookie": "sid=1"})
    assert [v["height"] for v in found["variants"]] == [720, 360]


def test_a_length_said_twice_is_read(server, make_payload, tmp_path):
    """A proxy that repeats Content-Length gives "3145728, 3145728": the
    probe raised ValueError, and the download ended "unexpectedly"."""
    srv = server(make_payload(3 * 1024 * 1024))

    def answer(h, head):
        if not head:
            return False
        h.send_response(200)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(len(srv.data)))
        h.send_header("Content-Length", str(len(srv.data)))
        h.end_headers()
        return True
    srv.answer = answer
    t = T.DownloadTask(srv.url("/f.zip"), str(tmp_path / "f.zip"), filename="f.zip")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.total_size == len(srv.data) and len(t.segments) > 1
    assert open(t.save_path, "rb").read() == srv.data


def test_a_range_of_unknown_total_is_not_the_file(server, make_payload, tmp_path):
    """No HEAD, and the one-byte look answered "bytes 0-0/*" (total not
    known): the look's own length, 1, was taken for the file's, and the
    download "completed" with one byte."""
    srv = server(make_payload(3 * 1024 * 1024))

    def answer(h, head):
        if head:
            h.send_response(405)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        rng = h.headers.get("Range")
        if rng:
            a, _, b = rng.split("=", 1)[1].partition("-")
            s, e = int(a), min(int(b) if b else len(srv.data) - 1, len(srv.data) - 1)
            h.send_response(206)
            h.send_header("Content-Range", f"bytes {s}-{e}/*")
            h.send_header("Content-Length", str(e - s + 1))
            h.end_headers()
            h.wfile.write(srv.data[s:e + 1])
            return True
        h.send_response(200)
        h.send_header("Connection", "close")
        h.end_headers()
        h.wfile.write(srv.data)
        return True
    srv.answer = answer
    t = T.DownloadTask(srv.url("/f.zip"), str(tmp_path / "f.zip"), filename="f.zip")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data


def test_ranges_promised_but_not_kept_fetch_the_file_whole(server, make_payload, tmp_path):
    """HEAD says Accept-Ranges, the GET ignores Range and sends the whole
    file (a download script, a proxy that drops Range): the first run failed
    "no longer sends this file in parts - Restart", and Restart did the same."""
    srv = server(make_payload(6 * 1024 * 1024))

    def answer(h, head):
        h.send_response(200)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(len(srv.data)))
        h.end_headers()
        if not head:
            h.wfile.write(srv.data)
        return True
    srv.answer = answer
    t = T.DownloadTask(srv.url("/f.zip"), str(tmp_path / "f.zip"), filename="f.zip")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == srv.data


@pytest.mark.parametrize("url, video", [
    ("https://app.box.com/s/x.zip", False),                     # "x.com" inside box.com
    ("https://www.dropbox.com/s/k/setup.exe?dl=1", False),
    ("https://cdn.example.com/f.zip?ref=youtube.com", False),
    ("https://mirror.example/ok.ru.iso", False),
    ("http://files.example/fox.com-backup.zip", False),
    ("https://www.netflix.com/title/1", False),
    ("https://youtu.be/abc", True),
    ("https://m.youtube.com/watch?v=abc", True),
    ("https://player.vimeo.com/video/1", True),
    ("https://www.reddit.com/r/x/comments/1/", True),
    ("https://x.com/user/status/1", True),
    ("https://YOUTUBE.COM/watch?v=a", True),
])
def test_a_video_site_is_known_by_its_host_only(url, video):
    """BUGS.md B1: a site's name anywhere in a link sent a plain zip to
    yt-dlp, which saved it as .unknown_video."""
    import yt_dl
    assert yt_dl.is_ytdlp_url(url) is video
