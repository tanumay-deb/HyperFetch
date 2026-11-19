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


def test_a_link_with_a_fetch_limit_is_not_wasted_on_a_probe_get(server, make_payload, tmp_path):
    """B6: a server allowing at most 4 GETs (HEAD not counted) for a 4-part
    download must succeed: the probe's GET continues as part 0 instead of
    throwing away a 1-byte GET before the 4 parts start."""
    srv = server(make_payload(4 * 1024 * 1024))
    lock = threading.Lock()
    gets = [0]

    def answer(h, head):
        if head:
            return False
        with lock:
            gets[0] += 1
            n = gets[0]
        if n > 4:
            h.send_response(403)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        return False

    srv.answer = answer
    t = T.DownloadTask(srv.url("/limited.bin"), str(tmp_path / "limited.bin"),
                       filename="limited.bin")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, (t.status, t.error, gets[0])
    assert open(t.save_path, "rb").read() == srv.data
    assert gets[0] <= 4


def test_resume_fails_when_the_file_changed_at_the_same_size_on_the_server(server, tmp_path):
    """B8: a paused download whose file is replaced by another of the same size
    must fail with 'changed on the server' via If-Range, never complete with a
    mix of the two versions. An unchanged file resumes normally."""
    size = 4 * 1024 * 1024
    v1 = b"A" * size
    v2 = b"B" * size
    state = {"data": v1, "etag": '"v1"', "lm": "Wed, 01 Jan 2025 00:00:00 GMT", "slow": True}
    srv = server(v1)

    def answer(h, head):
        d = state["data"]
        extra = [("ETag", state["etag"]), ("Last-Modified", state["lm"])]
        rng = h.headers.get("Range")
        ifr = h.headers.get("If-Range")
        if rng and ifr and ifr not in (state["etag"], state["lm"]):
            # RFC 9110: If-Range mismatch -> 200 with the full new representation
            h.send_response(200)
            for k, v in extra:
                h.send_header(k, v)
            h.send_header("Accept-Ranges", "bytes")
            h.send_header("Content-Length", str(len(d)))
            h.end_headers()
            if not head:
                try:
                    h.wfile.write(d)
                except OSError:
                    pass
            return True
        if not head and state["slow"]:
            import time
            a, _, b = (rng.split("=", 1)[1].partition("-") if rng else ("0", "-", str(len(d) - 1)))
            s, e = int(a), min(int(b) if b else len(d) - 1, len(d) - 1)
            h.send_response(206 if rng else 200)
            if rng:
                h.send_header("Content-Range", f"bytes {s}-{e}/{len(d)}")
            for k, v in extra:
                h.send_header(k, v)
            h.send_header("Accept-Ranges", "bytes")
            h.send_header("Content-Length", str(e - s + 1))
            h.end_headers()
            pos = s
            try:
                while pos <= e:
                    chunk = d[pos:min(pos + 65536, e + 1)]
                    h.wfile.write(chunk)
                    pos += len(chunk)
                    time.sleep(0.01)
            except OSError:
                pass
            return True
        srv.send(h, d, head, extra)
        return True

    srv.answer = answer

    def run_and_pause(path):
        import time
        t = T.DownloadTask(srv.url("/nightly.bin"), str(path), filename=path.name)
        th = threading.Thread(target=lambda: Downloader(t, segments=4).run(), daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and t.downloaded < 128 * 1024:
            time.sleep(0.02)
        t.request_pause()
        th.join(timeout=10)
        assert t.status == T.PAUSED and 0 < t.downloaded < size
        return t

    # 1) Unchanged file resumes cleanly to completion
    t_same = run_and_pause(tmp_path / "same.bin")
    state["slow"] = False
    t_same.clear_pause()
    Downloader(t_same, segments=4).run()
    assert t_same.status == T.COMPLETED, t_same.error
    assert open(t_same.save_path, "rb").read() == v1

    # 2) Same-size replacement on the server fails instead of mixing versions
    state["slow"] = True
    t_changed = run_and_pause(tmp_path / "changed.bin")
    # Also check persistence across restart
    t_changed = T.DownloadTask.from_dict(t_changed.to_dict())
    state["data"] = v2
    state["etag"] = '"v2"'
    state["lm"] = "Thu, 02 Jan 2025 00:00:00 GMT"
    state["slow"] = False
    t_changed.clear_pause()
    Downloader(t_changed, segments=4).run()
    assert t_changed.status == T.ERROR, f"resumed into a mix: status={t_changed.status}"
    assert "changed on the server" in t_changed.error.lower(), t_changed.error
    assert not os.path.exists(t_changed.save_path)


def test_weak_etag_uses_last_modified_for_if_range(server, tmp_path):
    """B8: fresh runs send no If-Range; resumes prefer Last-Modified (never a
    weak ETag W/...), falling back to a strong ETag only when Last-Modified is
    absent."""
    import utils
    size = 2 * 1024 * 1024
    v1 = b"X" * size
    lm = "Wed, 01 Jan 2025 00:00:00 GMT"
    srv = server(v1)
    seen_if_range = []

    def answer(h, head):
        if not head and h.headers.get("If-Range"):
            seen_if_range.append(h.headers.get("If-Range"))
        srv.send(h, v1, head, [("ETag", 'W/"weak-1"'), ("Last-Modified", lm)])
        return True

    srv.answer = answer
    # Fresh run records both validators and sends no If-Range
    t = T.DownloadTask(srv.url("/weak.bin"), str(tmp_path / "weak.bin"), filename="weak.bin")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.etag == 'W/"weak-1"'
    assert t.last_modified == lm
    assert seen_if_range == [], f"fresh run must not send If-Range: {seen_if_range}"

    # Resumed run sends Last-Modified (never the weak ETag)
    t2 = T.DownloadTask(srv.url("/weak.bin"), str(tmp_path / "weak2.bin"), filename="weak2.bin")
    t2.total_size, t2.supports_range = size, True
    t2.etag, t2.last_modified = 'W/"weak-1"', lm
    t2.segments = [T.Segment(0, 0, size // 2 - 1), T.Segment(1, size // 2, size - 1)]
    open(utils.temp_download_path(t2), "wb").write(b"\0" * size)
    Downloader(t2, segments=2).run()
    assert t2.status == T.COMPLETED, t2.error
    assert seen_if_range and all(v == lm for v in seen_if_range), seen_if_range

    # Without Last-Modified: strong ETag is used, weak ETag is never sent
    t3 = T.DownloadTask(srv.url("/weak.bin"), str(tmp_path / "weak3.bin"), filename="weak3.bin")
    t3.etag, t3.last_modified = 'W/"weak-1"', ""
    assert Downloader(t3)._if_range() == ""
    t3.etag = '"strong-1"'
    assert Downloader(t3)._if_range() == '"strong-1"'


def test_mirror_redirector_with_different_etags_completes_and_resumes(server, make_payload, tmp_path):
    """B8: a redirector (like Fedora MirrorManager) sends each request to one
    of two mirrors with the same bytes and Last-Modified but different ETag
    formats (nginx vs Apache). Fresh download and paused resume must both
    complete whole."""
    import time
    size = 4 * 1024 * 1024
    data = make_payload(size)
    lm = "Wed, 01 Jan 2025 00:00:00 GMT"
    slow = {"on": False}

    def make_mirror(etag):
        m = server(data)

        def answer(h, head):
            extra = [("ETag", etag), ("Last-Modified", lm)]
            rng = h.headers.get("Range")
            ifr = h.headers.get("If-Range")
            if rng and ifr and ifr not in (etag, lm):
                h.send_response(200)
                for k, v in extra:
                    h.send_header(k, v)
                h.send_header("Accept-Ranges", "bytes")
                h.send_header("Content-Length", str(len(data)))
                h.end_headers()
                if not head:
                    try:
                        h.wfile.write(data)
                    except OSError:
                        pass
                return True
            if not head and slow["on"]:
                a, _, b = (rng.split("=", 1)[1].partition("-") if rng else ("0", "-", str(len(data) - 1)))
                s, e = int(a), min(int(b) if b else len(data) - 1, len(data) - 1)
                h.send_response(206 if rng else 200)
                if rng:
                    h.send_header("Content-Range", f"bytes {s}-{e}/{len(data)}")
                for k, v in extra:
                    h.send_header(k, v)
                h.send_header("Accept-Ranges", "bytes")
                h.send_header("Content-Length", str(e - s + 1))
                h.end_headers()
                pos = s
                try:
                    while pos <= e:
                        chunk = data[pos:min(pos + 65536, e + 1)]
                        h.wfile.write(chunk)
                        pos += len(chunk)
                        time.sleep(0.01)
                except OSError:
                    pass
                return True
            m.send(h, data, head, extra)
            return True

        m.answer = answer
        return m

    m1 = make_mirror('"6720fe49-400000"')           # nginx form
    m2 = make_mirror('"400000-6259f30189361"')      # Apache form
    redir = server(b"")
    lock = threading.Lock()
    pick = [0]

    def redirect(h, head):
        with lock:
            target = (m1 if pick[0] % 2 == 0 else m2).url("/iso/fedora.iso")
            pick[0] += 1
        h.send_response(302)
        h.send_header("Location", target)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return True

    redir.answer = redirect

    # 1) Fresh download completes across both mirrors
    t1 = T.DownloadTask(redir.url("/fedora.iso"), str(tmp_path / "fresh.iso"), filename="fresh.iso")
    Downloader(t1, segments=4).run()
    assert t1.status == T.COMPLETED, t1.error
    assert open(t1.save_path, "rb").read() == data
    assert m1.requests and m2.requests

    # 2) Paused download resumes cleanly across both mirrors
    slow["on"] = True
    t2 = T.DownloadTask(redir.url("/fedora.iso"), str(tmp_path / "resumed.iso"), filename="resumed.iso")
    th = threading.Thread(target=lambda: Downloader(t2, segments=4).run(), daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and t2.downloaded < 128 * 1024:
        time.sleep(0.02)
    t2.request_pause()
    th.join(timeout=10)
    assert t2.status == T.PAUSED and 0 < t2.downloaded < size

    slow["on"] = False
    t2.clear_pause()
    Downloader(t2, segments=4).run()
    assert t2.status == T.COMPLETED, t2.error
    assert open(t2.save_path, "rb").read() == data


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
