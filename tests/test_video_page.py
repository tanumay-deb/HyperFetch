"""A video page from a site yt-dlp knows goes to yt-dlp, not to the "web page" error.

Seen in the log, 2026-10-03: a link to a video page on www.eporner.com was added
as "download.bin" and ended with "Server sent a web page, not the file
(login/cookies required — use the browser extension)". yt-dlp extracts that
site fine (8 mp4 formats), but a URL only reached it when its host was on the
short fixed list in yt_dl._SITES or "Use yt-dlp" was ticked.

So a web page where a file was expected now gets one yt-dlp extraction (nothing
downloaded) before the app gives up on it. A video there hands the task to the
yt-dlp engine; no video keeps the old error. Everything runs against servers
in this process and a stand-in for yt-dlp - no network.
"""
import http.server
import os
import sys
import threading
import time
import types

import pytest

import task as T
import yt_dl
from downloader import Downloader

WEB_PAGE = ("Server sent a web page, not the file "
            "(login/cookies required — use the browser extension)")
PAGE = (b"<!DOCTYPE html><html><head><title>A video</title></head>"
        b"<body><div id='player'></div></body></html>")
VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"v" * 4096


@pytest.fixture
def page_server():
    """Answers every path with a web page, the way a video site does."""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _answer(self, body):
            hits.append(self.command)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            if body:
                self.wfile.write(PAGE)

        def do_HEAD(self):
            self._answer(False)

        def do_GET(self):
            self._answer(True)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    yield types.SimpleNamespace(
        url=lambda p: f"http://127.0.0.1:{port}/{p.lstrip('/')}", hits=hits)
    httpd.shutdown()
    httpd.server_close()


class _FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. Records every extract_info call."""

    def __init__(self, site, opts):
        self.site = site
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        self.site.calls.append({"url": url, "download": download, "opts": self.opts})
        self.site.entered.set()
        if self.site.hold is not None:
            self.site.hold.wait(10)             # yt-dlp taking its time
        if not self.site.video:
            raise RuntimeError(f"ERROR: Unsupported URL: {url}")
        info = {"id": "8xGJx1", "title": "A video", "ext": "mp4",
                "formats": [{"format_id": "720p", "ext": "mp4",
                             "url": "https://cdn.test/8xGJx1-720p.mp4"}]}
        if download:
            path = self.prepare_filename(info)
            with open(path, "wb") as f:
                f.write(VIDEO)
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "finished", "filename": path})
        return info

    def prepare_filename(self, info):
        return self.opts["outtmpl"] % info


@pytest.fixture
def ytdlp(monkeypatch):
    """A fake yt_dlp module. Set .video False for a page with nothing on it,
    .hold to an Event to keep yt-dlp busy until it is set."""
    site = types.SimpleNamespace(video=True, hold=None, calls=[],
                                 entered=threading.Event())
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _FakeYDL(site, opts)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    yield site
    if site.hold is not None:
        site.hold.set()                         # let an abandoned look finish


def _page_task(page_server, tmp_path, **kw):
    """A pasted video-page link, named the way the app names it."""
    return T.DownloadTask(page_server.url("video-8xGJx1/a-video/"),
                          str(tmp_path / "download.bin"), **kw)


def _downloads(ytdlp):
    return [c["download"] for c in ytdlp.calls]


# ---- the reported case ------------------------------------------------------
def test_a_video_page_is_downloaded_by_ytdlp(page_server, ytdlp, tmp_path):
    t = _page_task(page_server, tmp_path)
    Downloader(t).run()

    assert t.status == T.COMPLETED, t.error
    assert _downloads(ytdlp) == [False, True], "one look, then the yt-dlp engine"
    assert t.filename == "A video.mp4"
    assert open(t.save_path, "rb").read() == VIDEO
    assert not os.path.exists(tmp_path / "download.bin"), "the page was saved as the file"


def test_once_handed_over_a_resume_goes_straight_to_ytdlp(page_server, ytdlp, tmp_path):
    """The same path a ticked "Use yt-dlp" takes: no second probe, no second look."""
    t = _page_task(page_server, tmp_path)
    Downloader(t).run()
    assert t.use_ytdlp is True
    hits, calls = len(page_server.hits), len(ytdlp.calls)

    Downloader(t).run()

    assert len(page_server.hits) == hits, "the page was probed again"
    assert _downloads(ytdlp)[calls:] == [True]
    assert t.status == T.COMPLETED, t.error


# ---- nothing for yt-dlp: today's error --------------------------------------
def test_a_page_ytdlp_finds_nothing_on_keeps_the_error(page_server, ytdlp, tmp_path):
    ytdlp.video = False
    t = _page_task(page_server, tmp_path)
    Downloader(t).run()

    assert t.status == T.ERROR
    assert t.error == WEB_PAGE
    assert _downloads(ytdlp) == [False], "exactly one extraction attempt, nothing downloaded"
    assert not os.path.exists(tmp_path / "download.bin")
    assert not getattr(t, "use_ytdlp", False)


def test_an_auth_gated_page_gets_one_look_with_its_browser_context(page_server, ytdlp,
                                                                   tmp_path):
    """A login wall the extension sent cookies for: yt-dlp is asked once, with the
    same request the engine would make, and the answer is the old error."""
    ytdlp.video = False
    t = T.DownloadTask(page_server.url("uc?id=abc&export=download"),
                       str(tmp_path / "uc.bin"),
                       headers={"Cookie": "SID=x", "Referer": "https://drive.test/",
                                "User-Agent": "UA", "X-Requested-With": "x"})
    Downloader(t).run()

    assert t.status == T.ERROR
    assert t.error == WEB_PAGE
    (look,) = ytdlp.calls
    assert look["download"] is False
    assert look["opts"]["http_headers"] == {
        "Cookie": "SID=x", "Referer": "https://drive.test/", "User-Agent": "UA"}


def test_without_ytdlp_the_error_stays(page_server, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)       # import yt_dlp fails
    t = _page_task(page_server, tmp_path)
    Downloader(t).run()
    assert t.status == T.ERROR
    assert t.error == WEB_PAGE


def test_a_look_that_takes_too_long_counts_as_nothing_found(page_server, ytdlp, tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(yt_dl, "EXTRACT_TIMEOUT", 0.5)
    ytdlp.hold = threading.Event()              # never answers in time
    t = _page_task(page_server, tmp_path)
    t0 = time.monotonic()
    Downloader(t).run()

    assert time.monotonic() - t0 < 3, "the task waited out yt-dlp"
    assert t.status == T.ERROR
    assert t.error == WEB_PAGE


# ---- pause / cancel while yt-dlp looks --------------------------------------
@pytest.mark.parametrize("stop,status", [("request_pause", T.PAUSED),
                                         ("request_cancel", T.CANCELLED)])
def test_pause_and_cancel_do_not_wait_for_ytdlp(page_server, ytdlp, tmp_path, stop, status):
    ytdlp.hold = threading.Event()              # yt-dlp is slow to answer
    t = _page_task(page_server, tmp_path)
    th = threading.Thread(target=Downloader(t).run, daemon=True)
    th.start()
    assert ytdlp.entered.wait(5), "yt-dlp was never asked"

    getattr(t, stop)()
    t0 = time.monotonic()
    th.join(5)

    assert not th.is_alive(), "still waiting on yt-dlp after the user stopped it"
    assert time.monotonic() - t0 < 1.0
    assert t.status == status
    assert t.error == ""
    assert _downloads(ytdlp) == [False], "the yt-dlp engine started anyway"


# ---- untouched ----------------------------------------------------------------
def test_a_real_file_never_meets_ytdlp(file_server, ytdlp, tmp_path):
    """A nameless link (saved as download.bin, like the page) that is a file."""
    data = file_server.put("get/", n_bytes=2 * 1024 * 1024)
    dst = str(tmp_path / "download.bin")
    t = T.DownloadTask(file_server.url("get/"), dst)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert open(dst, "rb").read() == data
    assert ytdlp.calls == []
    assert not getattr(t, "use_ytdlp", False)


@pytest.mark.parametrize("name", ["page.html", "page.htm"])
def test_a_page_asked_for_by_name_is_saved_as_the_page(page_server, ytdlp, tmp_path, name):
    dst = str(tmp_path / name)
    t = T.DownloadTask(page_server.url(name), dst)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert open(dst, "rb").read() == PAGE
    assert ytdlp.calls == []


# ---- a link that asked for something else -----------------------------------
@pytest.mark.parametrize("name", ["setup.exe", "pack.zip", "manual.pdf", "photo.jpg"])
def test_a_link_named_as_another_kind_of_file_never_becomes_a_video(page_server, ytdlp,
                                                                    tmp_path, name):
    """A file that was taken down often redirects to the vendor's home page,
    and a home page often has a clip on it. yt-dlp would find that clip - and
    the download called setup.exe would finish as somebody's promo video. A
    name that says program, archive, document or picture asked for that file,
    so the web page is the error it always was and yt-dlp is not asked."""
    t = T.DownloadTask(page_server.url("files/" + name), str(tmp_path / name))
    Downloader(t).run()

    assert t.status == T.ERROR
    assert t.error == WEB_PAGE
    assert ytdlp.calls == [], "yt-dlp was asked about a link that named another kind of file"
    saved = [n for n in os.listdir(tmp_path) if n != "appdata"]    # conftest's app-data
    assert saved == [], "something was saved in the file's place"
    assert not getattr(t, "use_ytdlp", False)


@pytest.mark.parametrize("name", ["clip.mp4", "song.mp3", "watch.php", "episode.bin"])
def test_a_media_name_or_one_that_names_no_type_can_be_a_video_page(page_server, ytdlp,
                                                                    tmp_path, name):
    t = T.DownloadTask(page_server.url("v/" + name), str(tmp_path / name))
    Downloader(t).run()

    assert t.status == T.COMPLETED, t.error
    assert _downloads(ytdlp) == [False, True]
    assert t.filename == "A video.mp4"


# ---- what counts as "yt-dlp found a video" ----------------------------------
@pytest.mark.parametrize("info,found", [
    ({"id": "a", "formats": [{"url": "https://v.test/a.mp4"}]}, True),
    ({"id": "a", "url": "https://v.test/a.mp4"}, True),
    ({"_type": "playlist", "entries": [{"_type": "url", "url": "https://v.test/1"}]}, True),
    ({"_type": "multi_video", "entries": [{"id": "1", "url": "https://v.test/1.mp4"}]}, True),
    ({"_type": "playlist", "entries": []}, False),
    ({"id": "a", "formats": []}, False),
    ({}, False),
    (None, False),
])
def test_what_counts_as_a_video(info, found):
    assert yt_dl._has_media(info) is found
