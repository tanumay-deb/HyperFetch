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
PROXY_TORRENTS = True     # torrents (aria2) use PROXIES too; the users server turns
                          # this off, since its torrents run direct by design
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
APP_VERSION = "2.8.0"

# The brand line. The About page shows this; the site (docs/index.html), the
# README and the extension's welcome page say the same words by hand -
# tests/test_tagline.py keeps them in step. The extension's manifest
# description and the store listings still describe the extension.
TAGLINE = "Fetch everything. Download faster."

# Where each browser gets the extension, in the order they are offered. The
# app's "Get Extension" shows these; the site and the README repeat them by
# hand (tests/test_store_links.py). Chrome's listing also serves Brave.
EXTENSION_STORES = {
    "Chrome Web Store": "https://chromewebstore.google.com/detail/hyperfetch/finojjembpabfbincabngboedegokdlm",
    "Firefox Add-ons": "https://addons.mozilla.org/addon/hyperfetch/",
    "Edge Add-ons": "https://microsoftedge.microsoft.com/addons/detail/ebgbghogfnompbkhihmaohhdehkdkcjj",
}


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
            get_logger("utils").warning(
                "pair_token at %s is empty — minting a new one, so the browser "
                "extension will have to pair again", path)
            break                          # exists but empty -> mint below
        except FileNotFoundError:
            # Only silent case worth keeping quiet is a genuine first run, and
            # that is exactly what this distinguishes. Minting when the file was
            # there a moment ago unpairs the extension, and used to do it with
            # nothing written anywhere to say it had happened.
            if os.path.isdir(os.path.dirname(path)):
                get_logger("utils").warning(
                    "pair_token is missing from an existing %s — minting a new "
                    "one; the browser extension will have to pair again",
                    os.path.dirname(path))
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
        # Read it back. A write that reports success but leaves different bytes
        # on disk is the difference between "the extension pairs once" and "the
        # extension is unpaired again at every launch", and without this check
        # the process happily carries on with a token nothing else can see.
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read().strip() != tok:
                    get_logger("utils").error(
                        "pair_token was written but %s does not contain it — "
                        "this token dies with the process and the extension "
                        "will need to pair again on every launch", path)
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


def write_durably(path, text):
    """Replace ``path`` with ``text`` so that a power cut leaves the old file or
    the new one — never a file of the right size full of zeros.

    A temp file renamed over the original is atomic for the NAME, not the data:
    the rename reaches the disk's journal at once, while the bytes can wait in
    the write cache for seconds. Lose power in between and Windows keeps the
    rename and a file whose contents never arrived. That is how downloads.json
    and its .bak both became ~5,900 zero bytes on 8 September. fsync before the
    rename closes the gap.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _rotate_backup(path):
    """Copy the current ``path`` to ``<path>.bak`` — if it is worth keeping.

    Only a file that parses: rotating whatever was there is how a zeroed main
    file would overwrite the one good copy left. An empty container ("[]",
    "{}") is not rotated either, so clearing a list cannot empty its backup.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = f.read()
        json.loads(current)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logging.getLogger("hyperfetch.utils").warning(
            "not backing up %s: it does not hold valid JSON", path)
        return
    if len(current.strip()) <= 2:
        return
    try:
        write_durably(path + ".bak", current)
    except OSError:
        pass                              # never let a backup failure block a save


def save_json(path, data, keep_backup=False):
    """Write JSON durably (see write_durably). ``keep_backup`` rotates the
    previous contents to ``<path>.bak`` first — cheap insurance for files that
    are the only copy of something the user cares about (the download list),
    so a bad write or a wrongly-empty save is always recoverable."""
    try:
        text = json.dumps(data, indent=2)
        if keep_backup:
            _rotate_backup(path)
        write_durably(path, text)
    except (OSError, TypeError, ValueError):
        # never crash a save (a TypeError from a non-serializable value would
        # otherwise propagate mid-shutdown), and write_durably has already removed
        # its .tmp — but never fail SILENTLY either: a quietly-frozen state file
        # resurrects days-old task statuses on every restart.
        logging.getLogger("hyperfetch.utils").exception("save_json failed: %s", path)


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


def category_of(task):
    """The category a download is listed under - the one answer for the
    sidebar, search, the web client and history.

    A torrent the app filed sits in a category folder chosen from what it
    holds (torrent.sort_into_category), and that folder is the answer: its
    name is a folder name, with no extension and often no hint. Everything
    else goes by its name (category_for), as before - a media page waits in
    Other until it finishes, but is a video the moment its name says so. So
    does a torrent in a folder the user chose, which may be named anything.
    """
    import torrent
    name = getattr(task, "filename", "") or ""
    base = getattr(task, "sort_base", None)
    save = getattr(task, "save_path", "") or ""
    if base and save and torrent.is_torrent_task(getattr(task, "url", ""), name):
        folder = os.path.dirname(save)
        if (os.path.normcase(os.path.normpath(os.path.dirname(folder)))
                == os.path.normcase(os.path.normpath(base))):
            filed = os.path.basename(folder).lower()
            for cat in list(CATEGORIES) + ["Other"]:
                if cat.lower() == filed:
                    return cat
    return category_for(name)


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
    """The category folder under base_dir for a file, created if need be.

    The same answer category_for gives the list, so a download sits in the
    folder named after the category it is shown under. What cannot be
    classified goes to Other rather than staying in base_dir, which otherwise
    collects every type the table does not know."""
    cat_dir = os.path.join(base_dir, category_for(filename))
    os.makedirs(cat_dir, exist_ok=True)
    return cat_dir


def place_download(base_dir, url, filename, category="Auto", categorize=True):
    """Where a new download is saved and who chose: ``(folder, sort_base)``.

    sort_base goes onto the task (see DownloadTask.sort_base). With the
    category left on "Auto" and sorting on, the app chooses and sort_base is
    base_dir: a file goes to its category folder, Other when nobody can name
    its type; a .torrent says what it holds, so it is filed by that at once; a
    magnet says nothing yet and waits in base_dir until its metadata arrives
    (torrent.sort_into_category files it then, before it downloads).

    Otherwise the user chose - a category picked by hand, or sorting switched
    off - and sort_base is "", which means the download is never moved.
    """
    import torrent
    if category != "Auto":
        folder = os.path.join(base_dir, category)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = base_dir
        return folder, ""
    if not categorize:
        return base_dir, ""
    if not torrent.is_torrent_task(url, filename):
        return get_category_dir(base_dir, filename), base_dir
    local = torrent.local_torrent_path(url)
    if not (torrent.is_torrent(url, filename) and os.path.isfile(local)
            and torrent.torrent_top_name(local)):
        return base_dir, base_dir
    folder = os.path.join(base_dir, torrent.torrent_category(local))
    os.makedirs(folder, exist_ok=True)
    return folder, base_dir

import time
import threading

class RateLimiter:
    """A thread-safe Token Bucket rate limiter."""
    def __init__(self):
        self.limit_bps = 0
        self._share = 0             # see set_share
        self._lock = threading.Lock()
        self._tokens = 0.0
        self._last_check = time.monotonic()

    @property
    def rate(self):
        """Bytes a second let through right now."""
        return self._share or self.limit_bps

    def set_limit(self, bps):
        with self._lock:
            if bps == self.limit_bps:
                # Applied again, not changed. The window does that every
                # minute, and each time used to refill the bucket: a second's
                # worth, free.
                return
            self.limit_bps = bps
            self._share = 0
            self._tokens = float(bps)
            self._last_check = time.monotonic()

    def set_share(self, bps):
        """Let only `bps` of the limit through for now; 0, or all of it, gives
        the whole limit back. The rest is in use elsewhere: aria2 keeps the
        torrents' part of the download speed limit itself."""
        with self._lock:
            bps = int(bps or 0)
            if bps <= 0 or bps >= self.limit_bps:
                bps = 0
            self._share = bps
            self._tokens = min(self._tokens, float(self._share or self.limit_bps))

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
                rate = self._share or self.limit_bps
                capacity = float(rate)
                take = min(remaining, capacity)

                now = time.monotonic()
                elapsed = now - self._last_check
                self._last_check = now
                self._tokens = min(capacity, self._tokens + elapsed * rate)

                if self._tokens >= take:
                    self._tokens -= take
                    remaining -= take
                    continue

                # sleep in short slices so pause/limit changes stay responsive
                sleep_t = min((take - self._tokens) / rate, 0.5)
                self._lock.release()
                time.sleep(sleep_t)
                self._lock.acquire()

global_limiter = RateLimiter()

# What the app's own connections leave for a torrent that is using less than
# its part: this much above what it is doing now, so it has room to speed up.
SHARE_HEADROOM = 1.2
# Never hand out 0: to the bucket and to aria2 alike, 0 means "no limit".
SHARE_FLOOR = 8 * 1024


def share_speed_limit(limit, n_http, n_torrent, torrent_rate=0):
    """Split the download speed limit between the two engines that keep it.

    -> (this app's own connections, aria2), bytes a second; 0 is "no limit".

    `global_limiter` paces every byte this app fetches itself. Torrents are
    fetched by aria2, which the bucket never sees, so aria2 is given a part of
    the limit to keep on its own (--max-overall-download-limit) and the bucket
    lets the rest through.

    Each side is owed a part by how many downloads it is running. This side
    also takes whatever the torrents are not using - a torrent with no seeders
    is still "running", and holding half the limit back for it would cap an
    ordinary download at half for nothing. It does not go the other way:
    aria2's part moves only when the mix of downloads does. Measured on aria2
    1.37.0, its limit is an average over about ten seconds, and each time the
    limit is RAISED it takes the difference at once (2 -> 6 MiB/s on a fast
    source: about 40 MiB in one second). A part that followed this side's
    speed up and down would be a burst every time it rose.
    """
    limit = int(limit or 0)
    if limit <= 0:
        return 0, 0
    if n_http <= 0 or n_torrent <= 0:
        return limit, limit
    owed_http = limit * n_http // (n_http + n_torrent)
    owed_torrent = limit - owed_http
    http = max(owed_http, limit - int(torrent_rate * SHARE_HEADROOM))
    return max(http, SHARE_FLOOR), max(owed_torrent, SHARE_FLOOR)


# ---------------------------------------------------------- a speed, as typed
_SPEED_UNITS = (("G", 1000 ** 3), ("M", 1000 ** 2), ("K", 1000))     # bits a second


def _speed_bits(text):
    """Bits a second from "5 Mb/s", "500 Kb/s", "12mbps" or a bare "25"; 0 for
    "Unlimited", nothing, or anything that is not a speed above zero.

    A bare number is megabits, like every entry in the Settings list. A capital
    B is bytes ("5 MB/s" is 40 Mb/s): the two differ by eight, and someone who
    types the capital means it.
    """
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg])?\s*([a-z/ ]*)", str(text or ""), re.I)
    if not m:
        return 0
    per = dict((k.lower(), v) for k, v in _SPEED_UNITS)[(m.group(2) or "m").lower()]
    bits = float(m.group(1)) * per
    if m.group(3).startswith("B"):
        bits *= 8
    return bits


def parse_speed(text):
    """Bytes a second for a download speed limit chosen or typed in Settings."""
    return int(_speed_bits(text) / 8)


def speed_text(text):
    """The same speed, written the way the Settings list writes it: "25" ->
    "25 Mb/s". What is saved, so it reads the same the next time."""
    bits = _speed_bits(text)
    if parse_speed(text) <= 0:
        return "Unlimited"
    unit, per = next(((u, p) for u, p in _SPEED_UNITS if bits >= p), _SPEED_UNITS[-1])
    return "%s %sb/s" % (("%.3f" % (bits / per)).rstrip("0").rstrip("."), unit)


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
