"""yt-dlp integration — download from media pages (YouTube, Vimeo, etc.) that a
plain HTTP byte-download can't handle.

A YtDlpDownloader is bound to one DownloadTask and drives the yt-dlp library with
progress hooks that update the task in place and honour pause/cancel (the hook
raises to abort). yt-dlp is imported lazily (heavy dependency) — it's declared to
PyInstaller in HyperFetch.spec for the frozen build.
"""
import os
import logging

import task as T

log = logging.getLogger("hyperfetch.ytdlp")

# media-page hosts where yt-dlp is the right engine (not a direct file URL)
_SITES = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv",
    "tiktok.com", "instagram.com", "facebook.com", "twitter.com", "x.com",
    "soundcloud.com", "reddit.com", "bilibili.com", "rumble.com", "ok.ru",
)


def is_ytdlp_url(url):
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")):
        return False
    return any(d in u for d in _SITES)


def available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


class _Abort(Exception):
    pass


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

        hdrs = getattr(self.t, "headers", {}) or {}
        http_headers = {k: v for k, v in hdrs.items()
                        if k.lower() in ("user-agent", "referer", "cookie")}

        opts = {
            "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
            "noplaylist": True,
            "progress_hooks": [hook],
            "quiet": True, "no_warnings": True, "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "retries": 5, "fragment_retries": 5,
            "continuedl": True,           # resume a paused/partial download
            "nopart": False,
        }
        if http_headers:
            opts["http_headers"] = http_headers

        import utils, shutil, re, sys
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
        if not utils.VERIFY_TLS:
            opts["nocheckcertificate"] = True
        if utils.PROXIES:
            opts["proxy"] = utils.PROXIES.get("https") or utils.PROXIES.get("http")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.t.url, download=True)
                try:
                    guess = ydl.prepare_filename(info)
                except Exception:
                    guess = ""
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
            if ("requested format is not available" in low or "ffmpeg" in low
                    or "merging" in low):
                self.t.error = ("This video has no combined audio+video stream — it needs "
                                "ffmpeg to merge them (bundled in the app installer; on a "
                                "source run, put ffmpeg on your PATH).")
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
