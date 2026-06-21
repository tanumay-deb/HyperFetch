"""yt-dlp integration — download from media pages (YouTube, Vimeo, etc.) that a
plain HTTP byte-download can't handle.

A YtDlpDownloader is bound to one DownloadTask and drives the yt-dlp library with
progress hooks that update the task in place and honour pause/cancel (the hook
raises to abort). yt-dlp is imported lazily (heavy dependency) — it's declared to
PyInstaller in HyperFetch.spec for the frozen build.
"""
import os

import task as T

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
        try:
            import yt_dlp
        except ImportError:
            self.t.status = T.ERROR
            self.t.error = "yt-dlp not installed — run: pip install yt-dlp"
            return

        out_dir = os.path.dirname(self.t.save_path) or "."
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
                        if k.lower() in ("user-agent", "referer")}

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
        # respect the global TLS + proxy settings
        import utils
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
                path = self._newest(out_dir)
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
        except _Abort:
            self.t.status = T.CANCELLED if self.t.cancel_requested else T.PAUSED
        except Exception as e:
            self.t.status = T.ERROR
            self.t.error = "yt-dlp: " + str(e)[:200]

    @staticmethod
    def _newest(out_dir):
        """Newest non-partial file in out_dir — fallback when the final path
        can't be resolved from yt-dlp's info."""
        newest = None
        try:
            for name in os.listdir(out_dir):
                if name.endswith((".part", ".ytdl", ".tmp")):
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
