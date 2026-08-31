"""Shared helpers: download dir, filename derivation, unique paths, app data,
plus security primitives (pairing token, TLS-verify flag, sensitive-header strip)."""
import os
import re
import json
import logging
import secrets
import tempfile
import urllib.parse

# Default request headers shared by the HTTP downloader, HLS grabber, and probe.
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (HyperFetch)"}


def temp_download_path(task_id):
    """The in-progress ``.hfdownload`` temp file for a task (one place defines the
    convention; downloader + HLS both use it)."""
    return os.path.join(tempfile.gettempdir(), f"{task_id}.hfdownload")


def finalize_download(temp_path, dest_path):
    """Atomically move a finished temp file to its destination, cross-volume safe.

    The temp lives in %TEMP% (often a different volume than the destination), so
    we stage into a sibling ``.hfmove`` in the DEST dir first (a plain
    ``shutil.move`` there is a same-dir rename) then ``os.replace`` it onto the
    final path (atomic same-volume swap). On failure the staging file is cleaned
    up and the OSError re-raised, so the caller can set the task error and leave
    the temp in place for a retry. Shared by the byte downloader and the HLS
    grabber."""
    import shutil
    staged = dest_path + ".hfmove"
    try:
        shutil.move(temp_path, staged)
        os.replace(staged, dest_path)
    except OSError:
        try:
            if os.path.exists(staged):
                os.remove(staged)
        except OSError:
            pass
        raise

# Global TLS-verification flag. Default ON (secure). The GUI may flip it from
# the saved setting; downloader/hls/probe read it for every request.
VERIFY_TLS = True

# Network / advanced settings wired from the GUI (Settings). Defaults = inactive.
PROXIES = None            # requests proxies: None=auto/env, {}=force-direct, {...}=custom
MAX_CONNECTIONS = 0       # global ceiling on per-download segments (0 = unlimited)
LISTEN_PORT = 0           # torrent listen port (0 = aria2 default)
DISK_CACHE = True         # aria2 --disk-cache on/off
PREALLOCATE = False       # aria2 file pre-allocation
HASH_CHECK = False        # verify SHA-256 against a <url>.sha256 sidecar on finish
SPEED_IN_BYTES = False    # speed readout units: False = bits (Kb/s), True = bytes (KB/s)
# Torrent engine: False = one aria2c subprocess per task (legacy, stable),
# True = one shared aria2 daemon driven over JSON-RPC. The daemon gives every
# torrent one warm DHT table and one forwardable listen port; the legacy engine
# gives each its own cold table and they collide on the port. Default off until
# the RPC path has proven itself on real downloads.
# (the shared aria2 daemon is now the only torrent engine; the old
#  per-torrent-process engine and its TORRENT_RPC switch were removed)
# The queue's own concurrency limit, mirrored here so the aria2 daemon can size
# its internal queue to match. aria2 must never be the narrower of the two, or
# it silently holds downloads the app has already decided to run.
MAX_CONCURRENT_DOWNLOADS = 3
# Breathing room left on the volume after a download: filling a disk to the last
# byte breaks far more than the download.
DISK_SLACK = 64 << 20
# Seeding. Off by default (the app has always stopped dead on completion), but
# a ratio-zero client is unwelcome on private trackers and gives nothing back to
# the swarms it takes from.
SEED_ENABLED = False
SEED_RATIO = 1.0          # share ratio to stop at (0 = no ratio limit)
SEED_MINUTES = 0          # also stop after this long (0 = no time limit)
# Fetch the start and end of each file first, so a part-downloaded video plays
# and seeks instead of having to finish first.
TORRENT_PREVIEW = False
# Upload ceiling in bytes/sec across all torrents (0 = unlimited). Matters most
# once seeding is on: home connections are asymmetric, so an uncapped upload
# saturates the upstream and takes the DOWNLOAD down with it — TCP ACKs for the
# incoming data have to fit in the same starved pipe.
MAX_UPLOAD_BPS = 0


def disk_shortfall(path, needed):
    """How many bytes ``path``'s volume is short of holding ``needed``.

    0 means it fits — or that the volume could not be queried, in which case the
    write is left to find out for itself rather than blocking on a guess.
    """
    import shutil
    if needed <= 0:
        return 0
    probe = path if os.path.isdir(path) else (os.path.dirname(path) or ".")
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return 0
    return max(0, needed + DISK_SLACK - free)
BADGE_CORNER = "top-right"  # extension's on-page download-button corner (served via /ping)

# Per-host download rules (Settings -> Network). {host: {"segments": int,
# "ytdlp": bool}} — override the segment count or force the yt-dlp engine for
# specific hosts (some servers throttle or reject many parallel connections).
HOST_RULES = {}


def host_rule(url):
    """The per-host rule for a URL's host (matches the exact host or a parent
    domain, e.g. a rule for 'example.com' also covers 'cdn.example.com'); {} if
    none."""
    try:
        host = urllib.parse.urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    except Exception:
        return {}
    if not host:
        return {}
    for h, rule in (HOST_RULES or {}).items():
        h = (h or "").lower().lstrip(".")
        if h and (host == h or host.endswith("." + h)):
            return rule or {}
    return {}

# Auto-capture allowlist (Settings -> Browser Integration). When the extension's
# browser-download capture forwards an auto-captured file, the server only accepts
# it if its extension is in this list. Empty list = capture everything. The
# extension itself no longer holds the list — the app is the single source of truth.
DEFAULT_CAPTURE_EXTS = ["zip", "rar", "7z", "iso", "tar", "gz", "exe", "msi",
                        "deb", "jar", "apk", "bin", "mp3", "aac", "pdf", "mp4",
                        "3gp", "avi", "mkv", "wav", "mpeg", "srt"]
CAPTURE_EXTS = list(DEFAULT_CAPTURE_EXTS)


def capture_allowed(name):
    """True if an auto-captured download named/URL'd `name` may be accepted.
    Empty CAPTURE_EXTS means accept everything."""
    if not CAPTURE_EXTS:
        return True
    base = (name or "").split("?")[0].split("#")[0].rstrip("/")
    base = base.rsplit("/", 1)[-1]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    return ext in CAPTURE_EXTS

# Request headers that must NEVER be written to disk (account-level secrets).
SENSITIVE_HEADERS = {"cookie", "authorization", "proxy-authorization"}


# The app version, kept here rather than in gui.theme so the headless server
# can report it without importing PySide6.
APP_VERSION = "2.4.0"


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


def get_or_create_token(retries=6, delay=0.2):
    """Stable per-install pairing secret the extension must present on /download.
    Generated once, stored 0600 in the app-data dir.

    A FAILED READ MUST NOT MINT A NEW TOKEN. This used to treat any OSError as
    "no token yet" and generate a fresh one, which silently unpairs the browser
    extension: it presents the token it stored, the app answers 401, and the
    user is told to pair by hand. A brief lock on the file is enough to trigger
    it — an antivirus scan right after an installer replaces the app is exactly
    that, which is why pairing broke on update while the file itself was never
    modified. Same shape as the bug that let a locked downloads.json read as an
    empty download list.

    So: retry a genuinely locked file, and only mint when the file is really
    absent or empty.
    """
    path = os.path.join(app_data_dir(), "pair_token")
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                return tok
            break                          # exists but empty -> mint below
        except FileNotFoundError:
            break                          # genuinely absent -> mint below
        except OSError as e:
            if attempt == retries - 1:
                # Still unreadable, and it EXISTS. Minting now would unpair the
                # extension, so say so loudly rather than doing it quietly.
                log = get_logger("utils")
                log.error("pair_token exists but could not be read (%s) — "
                          "issuing a temporary token; the extension will need "
                          "to re-pair", e)
                break
            time.sleep(delay)

    tok = secrets.token_urlsafe(24)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(tok)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:
        # Not persisted: this token dies with the process, so the next launch
        # hands out a different one and the extension has to re-pair every time.
        get_logger("utils").error(
            "could not save pair_token (%s) — pairing will not survive a "
            "restart", e)
    return tok


def strip_sensitive(headers):
    """Drop cookies/auth before persisting a task to downloads.json."""
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS}


# Windows refuses these as filenames even WITH an extension ("con.txt" fails).
_WIN_RESERVED = {"con", "prn", "aux", "nul",
                 *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name, max_len=200):
    """Filename guaranteed safe to create on disk — no path separators/traversal,
    no Windows reserved device name, and capped in length (Windows MAX_PATH
    headroom). The last line of defense before joining to a download directory."""
    name = os.path.basename(name or "")
    name = name.replace("\\", "_").replace("/", "_")
    name = _safe_chars(name)         # keep '#' and the extension; do NOT URL-split
    if name in ("", ".", ".."):
        name = "download"
    # reserved device name (CON, NUL, COM1, …), with or without an extension
    if name.split(".", 1)[0].lower() in _WIN_RESERVED:
        name = "_" + name
    # cap length, preserving the extension
    if len(name) > max_len:
        base, ext = os.path.splitext(name)
        ext = ext[:24]                       # guard an absurdly long "extension"
        name = (base[:max(1, max_len - len(ext))] or "download") + ext
    return name


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data, keep_backup=False):
    """Atomically write JSON. ``keep_backup`` rotates the previous contents to
    ``<path>.bak`` first — cheap insurance for files that are the only copy of
    something the user cares about (the download list), so a bad write or a
    wrongly-empty save is always recoverable."""
    tmp = path + ".tmp"
    try:
        if keep_backup and os.path.isfile(path) and os.path.getsize(path) > 2:
            try:
                import shutil
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass                      # never let backup failure block a save
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        # never crash a save (a TypeError from a non-serializable value would
        # otherwise propagate mid-shutdown) and never leave the .tmp sidecar —
        # but never fail SILENTLY either: a quietly-frozen state file resurrects
        # days-old task statuses on every restart.
        logging.getLogger("hyperfetch.utils").exception("save_json failed: %s", path)
        try:
            os.remove(tmp)
        except OSError:
            pass


def default_download_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_chars(name):
    """Replace characters illegal in a Windows filename with '_'. Keeps legal
    ones like '#' — unlike sanitize(), which URL-splits on '?'/'#'."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "")
    return name.strip(". ")


def sanitize(name):
    # URL-oriented: decode %-escapes and drop any query/fragment, THEN make the
    # remaining chars file-safe. Only for names derived from a URL — a real
    # filename/title (e.g. a video title with '#') must use _safe_chars instead,
    # or everything after the '#' (including the extension) would be lost.
    name = urllib.parse.unquote(name or "").strip()
    name = name.split("?")[0].split("#")[0]
    return _safe_chars(name) or ""


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
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
               ".heic", ".heif", ".tiff", ".ico", ".avif"},
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


def user_download_dir(base_dir, username):
    """Where one site account's downloads live: ``base_dir/<username>``.

    The username was already validated when the account was created, but this
    is the last point before it becomes a real path, so it is checked again
    rather than trusted. A name that does not survive the check is refused, not
    rewritten into some neighbouring folder: quietly relocating one account's
    files into another account's directory is the failure worth preventing.

    Returns base_dir unchanged for the empty owner, which is the desktop app
    and everything queued before accounts existed.
    """
    name = (username or "").strip()
    if not name:
        return base_dir
    if name != safe_filename(name) or name in (".", ".."):
        raise ValueError("unsafe username for a folder: %r" % username)
    d = os.path.join(base_dir, name)
    # Belt and braces: the join must not have escaped base_dir.
    if os.path.realpath(d) != os.path.realpath(os.path.join(base_dir, name)):
        raise ValueError("username escaped the download folder: %r" % username)
    root = os.path.realpath(base_dir)
    if not os.path.realpath(d).startswith(root + os.sep):
        raise ValueError("username escaped the download folder: %r" % username)
    os.makedirs(d, exist_ok=True)
    return d


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


# ----------------------------------------------------------------- debug logging
def get_logger(engine):
    """A per-engine child logger ('hyperfetch.<engine>'). Its records inherit the
    'hyperfetch' handler/level set by setup_logging, and the engine name shows up
    in each line — so a log mixes downloader/hls/queue/server lines legibly."""
    return logging.getLogger("hyperfetch." + engine)


def setup_logging(debug=False):
    """Configure the 'hyperfetch' logger tree. Warnings/errors are always written
    to %APPDATA%\\HyperFetch\\hyperfetch.log (created lazily, so a clean run leaves
    no file); the Debug-logging toggle additionally records verbose DEBUG output.
    Per-engine context comes from child loggers (hyperfetch.downloader, .hls,
    .queue, .server, …). Idempotent — safe to call on startup and on toggle."""
    logger = logging.getLogger("hyperfetch")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    logger.propagate = False
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    try:
        # delay=True: the file is created only when something is actually logged
        fh = logging.FileHandler(os.path.join(app_data_dir(), "hyperfetch.log"),
                                 encoding="utf-8", delay=True)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger
