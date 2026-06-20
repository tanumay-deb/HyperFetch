"""Shared helpers: download dir, filename derivation, unique paths, app data,
plus security primitives (pairing token, TLS-verify flag, sensitive-header strip)."""
import os
import re
import json
import secrets
import urllib.parse

# Global TLS-verification flag. Default ON (secure). The GUI may flip it from
# the saved setting; downloader/hls/probe read it for every request.
VERIFY_TLS = True

# Request headers that must NEVER be written to disk (account-level secrets).
SENSITIVE_HEADERS = {"cookie", "authorization", "proxy-authorization"}


def app_data_dir():
    """Per-user folder for settings + persisted download state."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "HyperFetch")
    if not os.path.isdir(d):
        # one-time migration from the pre-rebrand folder name
        legacy = os.path.join(base, "SmartDownloadManager")
        if os.path.isdir(legacy):
            try:
                os.rename(legacy, d)
            except OSError:
                return legacy            # old data still in use; keep using it
    os.makedirs(d, exist_ok=True)
    return d


def get_or_create_token():
    """Stable per-install pairing secret the extension must present on /download.
    Generated once, stored 0600 in the app-data dir."""
    path = os.path.join(app_data_dir(), "pair_token")
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(tok)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return tok


def strip_sensitive(headers):
    """Drop cookies/auth before persisting a task to downloads.json."""
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS}


def safe_filename(name):
    """Filename guaranteed to contain no path separators or traversal — the
    last line of defense before joining to a download directory."""
    name = os.path.basename(name or "")
    name = name.replace("\\", "_").replace("/", "_")
    name = sanitize(name)
    if name in ("", ".", ".."):
        name = "download"
    return name


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def default_download_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(d, exist_ok=True)
    return d


def sanitize(name):
    name = urllib.parse.unquote(name or "").strip()
    name = name.split("?")[0].split("#")[0]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip(". ") or ""


def filename_from_url(url, suggested=None):
    """Best-effort filename from a browser-suggested name or the URL path."""
    name = sanitize(suggested) if suggested else ""
    if not name:
        path = urllib.parse.urlparse(url).path
        name = sanitize(os.path.basename(path))
    if not name:
        name = "download"
    if "." not in name:
        name += ".bin"
    return name


def unique_path(directory, filename):
    """Avoid clobbering existing files: foo.zip -> foo (1).zip, foo (2).zip ...
    Always confines the result to `directory` (defends against traversal)."""
    filename = safe_filename(filename)
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({i}){ext}")
        i += 1
    return candidate


CATEGORIES = {
    "Video": {".mp4", ".mkv", ".webm", ".avi", ".mov", ".ts", ".m3u8", ".m4v", ".flv"},
    "Music": {".mp3", ".m4a", ".flac", ".wav", ".ogg"},
    "Compressed": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Programs": {".exe", ".msi", ".dmg", ".pkg", ".appimage", ".apk", ".deb", ".rpm"},
    "Documents": {".pdf", ".docx", ".xlsx", ".pptx", ".epub", ".txt"}
}

_VIDEO_KEYWORDS = ("1080p", "720p", "2160p", "480p", "4k", "x264", "x265",
                   "h264", "h265", "hevc", "web-dl", "webrip", "bluray", "blu-ray",
                   "bdrip", "brrip", "hdrip", "dvdrip", "xvid", "hdtv")
_AUDIO_KEYWORDS = ("flac", "320kbps", "mp3", "discography")


def category_for(filename):
    """Category for a file. Extension match first (matches CATEGORIES keys);
    for extension-less names (torrent/release titles like
    'Show.S01E01.1080p.WEB.x264') fall back to release-keyword detection so a
    task lands in ONE stable category instead of drifting between 'Other' and a
    real category as its name changes during a torrent's lifecycle."""
    name = (filename or "").lower()
    ext = os.path.splitext(name)[1]
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    if any(k in name for k in _VIDEO_KEYWORDS):
        return "Video"
    if any(k in name for k in _AUDIO_KEYWORDS):
        return "Music"
    return "Other"


def get_category_dir(base_dir, filename):
    """Return the base_dir + category subfolder based on file extension."""
    if not filename:
        return base_dir
    ext = os.path.splitext(filename)[1].lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            cat_dir = os.path.join(base_dir, cat)
            os.makedirs(cat_dir, exist_ok=True)
            return cat_dir
    return base_dir

import time
import threading

class RateLimiter:
    """A thread-safe Token Bucket rate limiter."""
    def __init__(self):
        self.limit_bps = 0
        self._lock = threading.Lock()
        self._tokens = 0.0
        self._last_check = time.monotonic()

    def set_limit(self, bps):
        with self._lock:
            self.limit_bps = bps
            self._tokens = float(bps)
            self._last_check = time.monotonic()

    def wait(self, amount):
        if self.limit_bps <= 0:
            return

        # Consume in bucket-capacity pieces: if amount > capacity (e.g. a 64 KiB
        # chunk against a 50 KB/s limit) the tokens could otherwise NEVER reach
        # `amount` and this would spin forever.
        remaining = float(amount)
        with self._lock:
            while remaining > 0:
                if self.limit_bps <= 0:
                    return
                capacity = float(self.limit_bps)
                take = min(remaining, capacity)

                now = time.monotonic()
                elapsed = now - self._last_check
                self._last_check = now
                self._tokens = min(capacity, self._tokens + elapsed * self.limit_bps)

                if self._tokens >= take:
                    self._tokens -= take
                    remaining -= take
                    continue

                # sleep in short slices so pause/limit changes stay responsive
                sleep_t = min((take - self._tokens) / self.limit_bps, 0.5)
                self._lock.release()
                time.sleep(sleep_t)
                self._lock.acquire()

global_limiter = RateLimiter()
