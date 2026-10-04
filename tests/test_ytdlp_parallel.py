"""A video yt-dlp would fetch as one plain file comes down over parallel ranges.

yt-dlp downloads a single http(s) file over one connection. The app's own
engine splits a file across several, which is the point of the app; the rough
numbers that started this (2026-10-03, one site): 9.3 Mbit/s for a direct link
through the engine, 4.7-6.0 Mbit/s for the same site's video through yt-dlp.

So when the format yt-dlp chose is one direct file, the engine fetches its
bytes; yt-dlp still extracts, names and finishes it. A merge, HLS, DASH and
anything the engine cannot start stay yt-dlp's, exactly as before. The task's
URL stays the page: the media link is signed and short-lived on many sites, so
every run extracts afresh, and a partial on disk is continued only when the
server describes the same file.

The stand-in for yt-dlp models the part the engine meets: extract_info picks a
format and, when downloading, gives it to process_info, which names the file,
leaves one already there, and calls dl() - yt-dlp's own transport, one plain
GET - once, or once per part of a merge. Two tests at the end run the real
yt-dlp against pages served here. No network.
"""
import http.cookiejar
import http.server
import json
import os
import socket
import sys
import threading
import time
import types
import uuid

import pytest
import requests

import task as T
import utils
import yt_dl
from downloader import Downloader

SIZE = 4 * 1024 * 1024
TITLE = "A video"


def _cookie(name, value, domain):
    return http.cookiejar.Cookie(0, name, value, None, False, domain, False, False,
                                 "/", True, False, None, False, None, None, {})


class _Site:
    """What the stand-in extracts: one 720p mp4 (or a 480p one when the format
    string asks for <=480), on a link signed with a fresh token each time."""

    def __init__(self, server):
        self.server = server
        self.calls, self.own = [], []        # extractions; yt-dlp's own downloads
        self.fields = {}                     # extra fields for the chosen format
        self.merge = False
        server.tokens = set()

    def extract(self, ydl):
        tok = uuid.uuid4().hex[:8]
        self.server.tokens.add(tok)
        fmt_req = ydl.opts.get("format", "")
        path = "media/480.mp4" if "height<=480" in fmt_req else "media/clip.mp4"
        sent = {k: v for k, v in (ydl.opts.get("http_headers") or {}).items()
                if k.lower() != "cookie"}    # yt-dlp keeps cookies in its jar
        fmt = {"format_id": "480p" if "480" in path else "720p", "ext": "mp4",
               "protocol": "http", "url": self.server.url(path) + "?t=" + tok,
               "http_headers": {"Accept": "*/*", "User-Agent": "yt-dlp-UA", **sent}}
        fmt.update(self.fields)
        # what the site set on the way: a cookie for the CDN's host
        ydl.cookiejar.set_cookie(_cookie("cdn", "ok", "127.0.0.1"))
        info = {"id": "8xGJx1", "title": TITLE, "formats": [fmt], **fmt}
        if self.merge:
            audio = {**fmt, "format_id": "audio", "ext": "m4a"}
            info.pop("url")
            info.update({"format_id": "720p+audio", "protocol": "http+http",
                         "requested_formats": [fmt, audio]})
        return info


class _YDL:
    def __init__(self, site, opts):
        self.site, self.opts = site, opts
        self.cookiejar = http.cookiejar.CookieJar()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        self.site.calls.append({"url": url, "download": download, "opts": self.opts})
        info = self.site.extract(self)
        if download:
            self.process_info(info)
        return info

    def process_info(self, info):
        name = self.prepare_filename(info)
        if not info.get("requested_formats"):
            # yt-dlp asks its downloader even when the file is there (its
            # temp name is the final name); the downloader says "already
            # downloaded"
            self.dl(name, info)
            return
        if os.path.exists(name):
            return
        parts = []
        for f in info["requested_formats"]:
            part = {k: v for k, v in info.items() if k != "requested_formats"}
            part.update(f)
            parts.append(name + ".f" + f["format_id"])
            self.dl(parts[-1], part)
        with open(name, "wb") as out:                # the merge
            for p in parts:
                with open(p, "rb") as f:
                    out.write(f.read())
                os.remove(p)

    def dl(self, name, info, subtitle=False, test=False):
        """yt-dlp's own transport: the whole file in one plain GET."""
        if os.path.exists(name):
            return True, False                       # "has already been downloaded"
        self.site.own.append(info["url"])
        r = requests.get(info["url"], headers=info.get("http_headers"),
                         cookies=self.cookiejar, timeout=30)
        r.raise_for_status()
        with open(name, "wb") as f:
            f.write(r.content)
        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "finished", "filename": name})
        return True, True

    def prepare_filename(self, info):
        return self.opts["outtmpl"] % info


@pytest.fixture
def site(media_server, make_payload, monkeypatch):
    s = _Site(media_server)
    s.data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    s.data480 = media_server.put("media/480.mp4", make_payload(SIZE // 2)[::-1])
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _YDL(s, opts)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return s


def _task(media_server, tmp_path, **kw):
    """A pasted video page the app sends to yt-dlp, named the way it names it."""
    t = T.DownloadTask(media_server.url("watch/8xGJx1"), str(tmp_path / "download.bin"),
                       **kw)
    t.use_ytdlp = True
    return t


def _ranged(server, since=0):
    """The engine's GETs: every one carries a Range."""
    return [e for e in server.log[since:] if e["method"] == "GET" and e["range"]]


def _requested(entries):
    total = 0
    for e in entries:
        a, _, b = e["range"][6:].partition("-")
        total += int(b) - int(a) + 1
    return total


def _start(t, segments=4):
    th = threading.Thread(target=Downloader(t, segments=segments).run, daemon=True)
    th.start()
    return th


def _wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _pause_midway(t, media_server, at=SIZE // 4):
    media_server.rate = 1024 * 1024
    th = _start(t)
    assert _wait_for(lambda: t.downloaded >= at), "never got going"
    t.request_pause()
    th.join(5)
    assert not th.is_alive()
    assert t.status == T.PAUSED, t.error
    media_server.rate = 0
    t.clear_pause()
    return t.downloaded


# ---- the case ---------------------------------------------------------------
def test_one_direct_file_comes_down_over_parallel_ranges(site, media_server, tmp_path):
    media_server.rate = 4 * 1024 * 1024          # so the ranges overlap in time
    t = _task(media_server, tmp_path)
    page = t.url
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert site.own == [], "yt-dlp fetched it itself"
    assert t.filename == TITLE + ".mp4"
    assert t.save_path == str(tmp_path / (TITLE + ".mp4"))
    assert open(t.save_path, "rb").read() == site.data
    assert t.url == page, "the task now points at the short-lived media link"
    starts = {e["range"] for e in _ranged(media_server)} - {"bytes=0-0"}
    assert len(starts) == 4, starts
    assert media_server.stats["max_active"] > 1
    assert [c["download"] for c in site.calls] == [True]
    assert not os.path.exists(tmp_path / "download.bin")


def test_the_media_link_gets_the_formats_headers_and_ytdlps_cookies(site, media_server,
                                                                    tmp_path):
    """The format's headers (the browser's UA and Referer come through them),
    the cookies yt-dlp holds for that link, and no compression - as yt-dlp's
    own download sends. The page's raw Cookie header is yt-dlp's to scope;
    it is never sent to the media host as it stands."""
    t = _task(media_server, tmp_path, headers={
        "Cookie": "SID=page", "Referer": "https://site.test/v/8xGJx1",
        "User-Agent": "Browser/1.0"})
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    gets = _ranged(media_server)
    assert gets
    for e in gets:
        h = {k.lower(): v for k, v in e["headers"].items()}
        assert h["user-agent"] == "Browser/1.0"
        assert h["referer"] == "https://site.test/v/8xGJx1"
        assert h["accept-encoding"] == "identity"
        assert h["cookie"] == "cdn=ok"


@pytest.mark.parametrize("fields", [
    {"protocol": "m3u8_native"},
    {"protocol": "http_dash_segments", "fragments": [{"url": "x"}]},
    {"downloader_options": {"http_chunk_size": 10 << 20}},
    {"is_live": True},
    {"request_data": b"a=1"},
    {"impersonate": "chrome"},
], ids=["hls", "dash", "chunked", "live", "post", "impersonate"])
def test_what_is_not_one_plain_file_stays_with_ytdlp(site, media_server, tmp_path, fields):
    site.fields = fields
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert len(site.own) == 1
    assert _ranged(media_server) == []


def test_a_merge_stays_with_ytdlp_part_by_part(site, media_server, tmp_path):
    """YouTube's separate video and audio: each part is a plain file too, but
    the pair is yt-dlp's to fetch and join."""
    site.merge = True
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert len(site.own) == 2
    assert _ranged(media_server) == []


def test_the_quality_picker_still_picks(site, media_server, tmp_path):
    t = _task(media_server, tmp_path, yt_format="bv*[height<=480]+ba/b[height<=480]")
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert "height<=480" in site.calls[0]["opts"]["format"]
    assert open(t.save_path, "rb").read() == site.data480
    assert site.own == []


# ---- pause, resume, restart -------------------------------------------------
def test_a_resume_extracts_again_and_continues_from_disk(site, media_server, tmp_path):
    t = _task(media_server, tmp_path)
    paused_at = _pause_midway(t, media_server)
    first_link = site.calls[0]
    media_server.tokens.clear()                 # the old link has expired
    since = len(media_server.log)

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == site.data
    assert len(site.calls) == 2, "the resume did not extract again"
    again = _ranged(media_server, since)
    assert again and all(e["query"] != first_link for e in again)
    assert _requested(again) <= SIZE - paused_at + 1, "started over"
    assert site.own == []


@pytest.mark.parametrize("change", ["etag", "size"])
def test_a_file_that_changed_meanwhile_starts_over(site, media_server, make_payload,
                                                   tmp_path, change):
    t = _task(media_server, tmp_path)
    _pause_midway(t, media_server)
    if change == "etag":
        new = media_server.put("media/clip.mp4", site.data[::-1], etag='"v2"')
    else:
        new = media_server.put("media/clip.mp4", make_payload(SIZE + 1234), etag='"v1"')
    since = len(media_server.log)

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == new
    assert _requested(_ranged(media_server, since)) >= len(new)


def test_after_a_restart_it_extracts_again_and_continues(site, media_server, tmp_path):
    """A saved task holds the page, not the link: a restored one goes back to
    yt-dlp, never fetches the page as if it were the file, and continues the
    bytes on disk."""
    t = _task(media_server, tmp_path)
    paused_at = _pause_midway(t, media_server)
    media_server.tokens.clear()
    saved = json.loads(json.dumps(t.to_dict()))
    assert "?t=" not in json.dumps(saved), "the short-lived link was saved"
    since = len(media_server.log)

    back = T.DownloadTask.from_dict(saved)
    Downloader(back, segments=4).run()

    assert back.status == T.COMPLETED, back.error
    assert open(back.save_path, "rb").read() == site.data
    assert not [e for e in media_server.log[since:] if e["path"].startswith("watch/")], \
        "the page was fetched as the file"
    assert _requested(_ranged(media_server, since)) <= SIZE - paused_at + 1


def test_a_link_that_expires_midway_keeps_the_bytes_and_says_resume(site, media_server,
                                                                    tmp_path):
    media_server.limit = 2                      # so later ranges start later
    t = _task(media_server, tmp_path)
    media_server.rate = 1024 * 1024
    th = _start(t)
    assert _wait_for(lambda: t.downloaded >= SIZE // 8)
    media_server.tokens.clear()                 # expired, mid-download
    th.join(30)

    assert t.status == T.ERROR
    assert "Resume" in t.error and "Refresh" not in t.error, t.error
    assert os.path.exists(utils.temp_download_path(t.id))
    kept = t.downloaded
    assert kept > 0

    media_server.rate = 0
    since = len(media_server.log)
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == site.data
    assert _requested(_ranged(media_server, since)) <= SIZE - kept + 1


@pytest.mark.parametrize("stop,status", [("request_pause", T.PAUSED),
                                         ("request_cancel", T.CANCELLED)])
def test_pause_and_cancel_answer_at_once(site, media_server, tmp_path, stop, status):
    media_server.rate = 256 * 1024
    t = _task(media_server, tmp_path)
    th = _start(t)
    assert _wait_for(lambda: t.downloaded > 0)

    getattr(t, stop)()
    t0 = time.monotonic()
    th.join(5)

    assert not th.is_alive()
    assert time.monotonic() - t0 < 1.0
    assert t.status == status
    assert site.own == [], "yt-dlp took over after the user stopped it"
    kept = os.path.exists(utils.temp_download_path(t.id))
    assert kept is (status == T.PAUSED)
    assert not os.path.exists(tmp_path / (TITLE + ".mp4"))


# ---- what the engine cannot start, yt-dlp still does ---------------------
@pytest.mark.parametrize("how", ["no_range", "forbids_range", "web_page"])
def test_a_link_the_engine_cannot_start_is_left_to_ytdlp(site, media_server, tmp_path,
                                                         how):
    if how == "no_range":
        media_server.no_range.add("media/clip.mp4")
    elif how == "forbids_range":
        media_server.forbid_range.add("media/clip.mp4")
    else:
        media_server.html.add("media/clip.mp4")
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert len(site.own) == 1, "yt-dlp was not given it"
    assert t.status == T.COMPLETED, t.error
    assert not os.path.exists(utils.temp_download_path(t.id))
    assert [e["range"] for e in _ranged(media_server)] == ["bytes=0-0"], \
        "the engine went past its one look"


def test_a_file_already_there_is_yt_dlps_to_report(site, media_server, tmp_path):
    existing = tmp_path / (TITLE + ".mp4")
    existing.write_bytes(b"mine")
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert existing.read_bytes() == b"mine"
    assert _ranged(media_server) == [] and site.own == []


def test_yt_dlps_own_partial_is_left_to_it(site, media_server, tmp_path):
    """A download paused before this change, or after a fallback, has
    yt-dlp's .part beside it; yt-dlp continues it."""
    (tmp_path / (TITLE + ".mp4.part")).write_bytes(site.data[:1000])
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert len(site.own) == 1
    assert _ranged(media_server) == []


def test_a_host_ruled_to_yt_dlp_is_fetched_by_it(site, media_server, tmp_path,
                                                 monkeypatch):
    """Settings > Network > per-host rules: "use yt-dlp" for the media host,
    or one connection, is the way to say "not in parallel" to a CDN."""
    monkeypatch.setattr(utils, "HOST_RULES", {"127.0.0.1": {"ytdlp": True}})
    t = _task(media_server, tmp_path)
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert len(site.own) == 1 and _ranged(media_server) == []


def test_one_connection_is_yt_dlps(site, media_server, tmp_path):
    t = _task(media_server, tmp_path)
    Downloader(t, segments=1).run()
    assert len(site.own) == 1 and _ranged(media_server) == []


# ---- filed like any other download ------------------------------------------
def test_the_finished_video_is_filed_under_video(site, media_server, tmp_path):
    from gui2.app import DownloadAppV2 as A
    base = str(tmp_path / "dl")
    os.makedirs(base)
    page = media_server.url("watch/8xGJx1")
    folder, sort_base = utils.place_download(base, page, "download.bin")
    t = T.DownloadTask(page, utils.unique_path(folder, "download.bin"))
    t.sort_base, t.use_ytdlp = sort_base, True
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error

    stub = types.SimpleNamespace(_extras={"categorize": True}, _save_state=lambda: None,
                                 _folder_category=staticmethod(A._folder_category))
    A._maybe_categorize(stub, t)

    assert t.save_path == os.path.join(base, "Video", TITLE + ".mp4")
    assert open(t.save_path, "rb").read() == site.data
    assert utils.category_of(t) == "Video"
    assert not os.path.exists(os.path.join(base, "Other"))


# ---- the real yt-dlp ---------------------------------------------------------
@pytest.fixture
def real_ytdlp():
    return pytest.importorskip("yt_dlp")


@pytest.fixture
def video_page():
    """A page with a <video>, as yt-dlp's generic extractor reads one. It sets
    a cookie on the way, as sites do."""
    pages = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = pages.get(self.path.split("?")[0], "").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "sess=abc; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def add(path, src):
        pages[path] = ("<!DOCTYPE html><html><head><title>%s</title></head><body>"
                       "<video controls><source src='%s' type='video/mp4'></video>"
                       "</body></html>" % (TITLE, src))
        return "http://127.0.0.1:%d%s" % (port, path)

    yield add
    httpd.shutdown()
    httpd.server_close()


def test_with_the_real_ytdlp_the_video_comes_down_in_ranges(real_ytdlp, video_page,
                                                            media_server, make_payload,
                                                            tmp_path):
    data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    page = video_page("/watch/1", media_server.url("media/clip.mp4"))
    with real_ytdlp.YoutubeDL({"quiet": True, "noplaylist": True,
                               "outtmpl": str(tmp_path / "%(title)s.%(ext)s")}) as ydl:
        named = ydl.prepare_filename(ydl.extract_info(page, download=False))
    assert os.path.basename(named).startswith(TITLE) and named.endswith(".mp4")
    t = T.DownloadTask(page, str(tmp_path / "download.bin"))
    t.use_ytdlp = True
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == named, "not the name yt-dlp gives it"
    assert open(t.save_path, "rb").read() == data
    starts = {e["range"] for e in _ranged(media_server)} - {"bytes=0-0"}
    assert len(starts) == 4, media_server.log


@pytest.mark.parametrize("media_host", ["127.0.0.1", "media.test"],
                         ids=["same-host", "other-host"])
def test_with_the_real_ytdlp_the_cdn_sees_what_ytdlp_itself_sends(
        real_ytdlp, video_page, media_server, make_payload, tmp_path, monkeypatch,
        media_host):
    """The same task twice: once with yt-dlp fetching (a host rule says so),
    once through the engine. Cookie, Referer and User-Agent at the media host
    must match - including which cookies yt-dlp holds back from another host."""
    real_gai = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: real_gai(
        "127.0.0.1" if host == "media.test" else host, *a, **k))
    media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    src = media_server.url("media/clip.mp4").replace("127.0.0.1", media_host)
    page = video_page("/watch/2", src)
    headers = {"Cookie": "pref=1", "Referer": "https://ref.test/watch/2",
               "User-Agent": "Browser/2.0"}

    def run(sub, rules):
        monkeypatch.setattr(utils, "HOST_RULES", rules)
        out = tmp_path / sub
        out.mkdir()
        t = T.DownloadTask(page, str(out / "download.bin"), headers=dict(headers))
        t.use_ytdlp = True
        since = len(media_server.log)
        Downloader(t, segments=4).run()
        assert t.status == T.COMPLETED, t.error
        names.append(os.path.basename(t.save_path))
        return [e for e in media_server.log[since:] if e["method"] == "GET"]

    names = []
    own = run("own", {media_host: {"ytdlp": True}})
    ours = run("ours", {})
    assert len(own) == 1 and len(ours) > 2
    assert names[0] == names[1], "the engine's file is named otherwise"

    def seen(e, name):
        h = {k.lower(): v for k, v in e["headers"].items()}
        v = h.get(name, "")
        return sorted(v.split("; ")) if name == "cookie" and v else v

    for name in ("cookie", "referer", "user-agent"):
        for e in ours:
            assert seen(e, name) == seen(own[0], name), name
    if media_host == "127.0.0.1":
        assert seen(own[0], "cookie") == ["pref=1", "sess=abc"]
    else:
        assert seen(own[0], "cookie") == ""
