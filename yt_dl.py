"""yt-dlp integration — download from media pages (YouTube, Vimeo, etc.) that a
plain HTTP byte-download can't handle.

A YtDlpDownloader is bound to one DownloadTask and drives the yt-dlp library with
progress hooks that update the task in place and honour pause/cancel (the hook
raises to abort). yt-dlp is imported lazily (heavy dependency) — it's declared to
PyInstaller in HyperFetch.spec for the frozen build.
"""
import os
import time
import logging
import threading

import task as T

log = logging.getLogger("hyperfetch.ytdlp")

# media-page hosts where yt-dlp is the right engine (not a direct file URL)
_SITES = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv",
    "tiktok.com", "instagram.com", "facebook.com", "twitter.com", "x.com",
    "soundcloud.com", "reddit.com", "bilibili.com", "rumble.com", "ok.ru",
)

# How long can_extract waits for yt-dlp to find a video on a page before the
# page counts as having none. Extraction is a few requests; this only bounds a
# host that answers slowly or not at all.
EXTRACT_TIMEOUT = 60


def is_ytdlp_url(url):
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")):
        return False
    return any(d in u for d in _SITES)


def is_dash(url="", filename="", ctype=""):
    """A DASH manifest (``.mpd``) — an XML index, not the media.

    Byte-downloading one writes a few KB of XML named like a video, and the HLS
    engine can't read it either (different format). yt-dlp's generic extractor
    parses the manifest, fetches the segments and merges the separate video/audio
    adaptation sets with the bundled ffmpeg."""
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    c = (ctype or "").lower()
    return u.endswith(".mpd") or f.endswith(".mpd") or "dash+xml" in c


def _formats_unavailable(err):
    """True when extraction produced no usable video format.

    Distinct from a download that started and then broke: this is the site
    handing back nothing playable, which a retry with different request context
    can genuinely fix.
    """
    low = str(err).lower()
    return ("requested format is not available" in low
            or "only images are available" in low
            or "no video formats found" in low)


def available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


class _Abort(Exception):
    pass


class _YtLog:
    """Routes yt-dlp's own messages (deprecation notices, ERROR echoes) into our
    debug log instead of stdout/stderr, so they don't spam the app console."""
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): log.debug("yt-dlp: %s", m)
    def error(self, m): log.debug("yt-dlp: %s", m)


def _http_headers(t):
    """The browser context a task carries (UA/Referer/Cookie from the extension)."""
    hdrs = getattr(t, "headers", {}) or {}
    return {k: v for k, v in hdrs.items()
            if k.lower() in ("user-agent", "referer", "cookie")}


def _net_opts():
    """yt-dlp options for the global TLS + proxy settings."""
    import utils
    opts = {}
    if not utils.VERIFY_TLS:
        opts["nocheckcertificate"] = True
    if utils.PROXIES:
        opts["proxy"] = utils.PROXIES.get("https") or utils.PROXIES.get("http")
    return opts


def _has_media(info):
    """An extraction result with something in it for the engine to download."""
    if not info:
        return False
    if info.get("_type") in ("playlist", "multi_video"):
        return bool(info.get("entries"))
    return bool(info.get("formats") or info.get("url"))


def can_extract(t):
    """Whether yt-dlp finds a video at the task's URL: one extraction, nothing
    downloaded.

    For a link that answered with a web page where a file was expected. A video
    page on a site _SITES does not name looks exactly like that, and yt-dlp
    knows far more sites than the list. False when it finds nothing, is not
    installed or takes longer than EXTRACT_TIMEOUT - and when the task is
    paused or cancelled first, which the caller checks.

    Extraction cannot be interrupted, so it runs on a thread of its own while
    this polls pause/cancel in 0.2 s slices. A look the user walked away from
    finishes in the background and its answer is dropped.
    """
    if t.pause_requested or t.cancel_requested:
        return False
    opts = {
        "logger": _YtLog(),
        "quiet": True, "no_warnings": True,
        "noplaylist": True,
        # a playlist's first entry, unresolved, is enough to know
        "extract_flat": "in_playlist", "playlist_items": "1",
        "socket_timeout": 15,           # the downloader's own CONNECT_TIMEOUT
        **_net_opts(),
    }
    http_headers = _http_headers(t)
    if http_headers:
        opts["http_headers"] = http_headers
    found = []

    def look():
        try:
            import yt_dlp               # a first import is slow too; keep it here
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(t.url, download=False)
        except Exception as e:          # not installed, unsupported page, login wall, no formats
            log.debug("yt-dlp: no video at %s: %s", t.url, str(e)[:200])
            return
        found.append(_has_media(info))

    th = threading.Thread(target=look, daemon=True, name="ytdlp-look")
    th.start()
    deadline = time.monotonic() + EXTRACT_TIMEOUT
    while th.is_alive():
        if t.pause_requested or t.cancel_requested:
            return False
        if time.monotonic() >= deadline:
            log.info("yt-dlp took over %ss on %s - taken as no video",
                     EXTRACT_TIMEOUT, t.url)
            return False
        th.join(0.2)
    return bool(found and found[0])


def _ytdlp_version():
    """The bundled yt-dlp's version, for error messages. It is frozen into the
    build, so naming it is the difference between "something is broken" and
    "this build has aged out"."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return "unknown version"


class YtDlpDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask

    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.supports_range = False
        log.info("yt-dlp start: %s", self.t.url)
        try:
            import yt_dlp
        except ImportError:
            self.t.status = T.ERROR
            self.t.error = "yt-dlp not installed — run: pip install yt-dlp"
            return

        out_dir = os.path.dirname(self.t.save_path) or "."
        _pre_existing = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"cannot create folder: {e}"
            return

        final = {"path": ""}

        def hook(d):
            if self.t.cancel_requested or self.t.pause_requested:
                raise _Abort()
            st = d.get("status")
            if st == "downloading":
                self.t.downloaded = d.get("downloaded_bytes") or 0
                self.t.total_size = (d.get("total_bytes")
                                     or d.get("total_bytes_estimate") or 0)
            elif st == "finished":
                final["path"] = d.get("filename") or final["path"]

        http_headers = _http_headers(self.t)
        ytlog = _YtLog()

        opts = {
            "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
            "noplaylist": True,
            "progress_hooks": [hook],
            "logger": ytlog,
            "quiet": True, "no_warnings": True, "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "retries": 5, "fragment_retries": 5,
            "continuedl": True,           # resume a paused/partial download
            "nopart": False,
        }
        if http_headers:
            opts["http_headers"] = http_headers

        import shutil, re, sys
        # Locate ffmpeg (bundled with the app, or on PATH). With ffmpeg we can
        # merge separate video+audio streams -> real 1080p/4K, and videos that
        # only offer DASH (no combined stream) become downloadable. Without it we
        # are limited to single muxed streams (<=720p on YouTube).
        ffdir = None
        _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        _bundled = os.path.join(_base, "bin", "ffmpeg.exe")
        if os.path.exists(_bundled):
            ffdir = os.path.dirname(_bundled)
        else:
            _which = shutil.which("ffmpeg")
            if _which:
                ffdir = os.path.dirname(_which)
        if ffdir:
            opts["ffmpeg_location"] = ffdir

        # No JS runtime is bundled. yt-dlp warns that YouTube extraction without
        # one is deprecated, but measured against real videos it currently changes
        # nothing: the same 33 formats up to 2160p come back with and without a
        # runtime, because YouTube is not demanding the "n challenge" for anonymous
        # requests. Bundling one is not free either — deno is ~110 MB, and quickjs
        # is 2 MB but yt-dlp ships no solver lib for it, so it must fetch one at
        # solve time. Revisit when YouTube actually starts requiring it; the
        # cookie fallback below is what fixes the failure seen in practice.

        # Build a format string that never hard-fails with "requested format is
        # not available": prefer a height-capped merge when ffmpeg is present,
        # else a single muxed stream — always with a plain "b" fallback.
        req = (getattr(self.t, "yt_format", "") or "").strip()
        mh = re.search(r"height<=(\d+)", req)
        h = mh.group(1) if mh else None
        if req.startswith("ba"):                        # audio-only intent
            opts["format"] = "ba[ext=m4a]/ba/b"
        elif ffdir:
            opts["format"] = (f"bv*[height<={h}]+ba/b[height<={h}]/b" if h else "bv*+ba/b")
        else:
            opts["format"] = (f"b[height<={h}]/b" if h else "b")

        # respect the global TLS + proxy settings
        opts.update(_net_opts())

        def _attempt(o):
            """One yt-dlp run. Returns its guess at the output path."""
            with yt_dlp.YoutubeDL(o) as ydl:
                info = ydl.extract_info(self.t.url, download=True)
                try:
                    return ydl.prepare_filename(info)
                except Exception:
                    return ""

        try:
            try:
                guess = _attempt(opts)
            except _Abort:
                raise
            except Exception as e:
                # YouTube answers a cookie-bearing request with a player response
                # whose formats need the "n challenge" solved. Without a JS runtime
                # yt-dlp cannot solve it, every video format drops out ("Only images
                # are available") and extraction dies with "Requested format is not
                # available" — while the very same URL with no cookies serves normal
                # formats. That is why the browser's right-click download failed on a
                # video that New Download handled: only the extension sends cookies.
                # Cookies still matter for private/members-only videos, so try them
                # first and fall back rather than dropping them outright. The retry
                # also covers a plain transient extraction failure.
                if not (_formats_unavailable(e)
                        and any(k.lower() == "cookie" for k in http_headers)):
                    raise
                log.info("yt-dlp: no formats with cookies, retrying without them: %s",
                         str(e)[:120])
                retry = dict(opts)
                retry["http_headers"] = {k: v for k, v in http_headers.items()
                                         if k.lower() != "cookie"}
                guess = _attempt(retry)
            path = guess if (guess and os.path.exists(guess)) else final["path"]
            if not (path and os.path.exists(path)):
                path = self._newest(out_dir, _pre_existing)
            if path and os.path.exists(path):
                self.t.save_path = path
                self.t.filename = os.path.basename(path)
                try:
                    sz = os.path.getsize(path)
                    self.t.total_size = sz
                    self.t.downloaded = sz
                except OSError:
                    pass
            self.t.status = T.COMPLETED
            log.info("yt-dlp done: %s", self.t.filename)
        except _Abort:
            self.t.status = T.CANCELLED if self.t.cancel_requested else T.PAUSED
        except Exception as e:
            self.t.status = T.ERROR
            import re as _re
            msg = _re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()    # strip ANSI colour codes
            low = msg.lower()
            # A 403 on the media URLs almost always means this build's yt-dlp
            # has aged out. YouTube changes how it signs those URLs every few
            # weeks and an older copy simply stops working. The version is
            # frozen into the build, so the user cannot update it themselves
            # and the message has to say that rather than blame the network.
            #
            # Measured rather than assumed: the URL that failed here on a
            # three-month-old yt-dlp downloaded fine on a current one — with a
            # JavaScript runtime present and without. The "no JS runtime"
            # warning yt-dlp prints alongside this is a red herring for it.
            if ("403" in low or "unable to download video data" in low) and                     "youtu" in (self.t.url or "").lower():
                self.t.error = (
                    "YouTube refused the download (403). That usually means "
                    "HyperFetch's bundled yt-dlp (%s) has gone stale — YouTube "
                    "changes how it signs video links every few weeks. A newer "
                    "HyperFetch build is the fix." % _ytdlp_version())
            elif ffdir is None and ("requested format is not available" in low
                                    or "ffmpeg" in low or "merging" in low):
                self.t.error = ("This video has no combined audio+video stream — it needs "
                                "ffmpeg to merge them (bundled in the app installer; on a "
                                "source run, put ffmpeg on your PATH).")
            elif "requested format is not available" in low:
                # ffmpeg IS present, so merging is not the problem — the site
                # served no downloadable video stream at all. Blaming ffmpeg here
                # sent the user looking for a missing binary that was never missing.
                self.t.error = ("The site offered no downloadable video stream for this "
                                "one. It may be private, region-locked, or need a sign-in "
                                "the app doesn't have.")
            else:
                self.t.error = "yt-dlp: " + msg[:200]
            log.error("yt-dlp failed: %s — %s", self.t.url, str(e)[:200])

    @staticmethod
    def _newest(out_dir, pre_existing=None):
        """Newest non-partial file in out_dir that was NOT in pre_existing —
        prevents picking up unrelated files."""
        pre = pre_existing or set()
        newest = None
        try:
            for name in os.listdir(out_dir):
                if name.endswith((".part", ".ytdl", ".tmp")):
                    continue
                if name in pre:
                    continue
                p = os.path.join(out_dir, name)
                if not os.path.isfile(p):
                    continue
                mt = os.path.getmtime(p)
                if newest is None or mt > newest[0]:
                    newest = (mt, p)
        except OSError:
            return ""
        return newest[1] if newest else ""
