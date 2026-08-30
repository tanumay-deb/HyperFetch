"""BitTorrent / magnet support via an aria2c sidecar.

A `TorrentDownloader` is bound to one DownloadTask and drives a per-task
``aria2c`` subprocess (magnet, .torrent URL, or local .torrent). It mirrors the
HlsDownloader interface — `run()` updates the task fields in place and honours
``pause_requested`` / ``cancel_requested``:

  pause  -> terminate aria2c (it leaves a ``.aria2`` control file; relaunching
            with the same --dir resumes from the partial data)
  cancel -> terminate aria2c (the queue cleans up)

Why a sidecar and not libtorrent: aria2c is a single static binary (magnet +
.torrent + DHT), trivial to bundle in PyInstaller and reliable on Windows;
python-libtorrent wheels are flaky to install and painful to freeze.
"""
import os
import re
import sys
import time
import shutil
import hashlib
import logging
import threading
import urllib.parse

import task as T
import utils

log = logging.getLogger("hyperfetch.torrent")

POLL = 0.3            # seconds between pause/cancel checks
STOP_GRACE = 5        # seconds to wait after terminate before kill
# How long aria2 may sit at zero speed before giving up. Generous because the
# same timer covers the magnet metadata phase, where minutes of silence is
# normal on a sparsely-seeded torrent.
STALL_TIMEOUT = 1800  # 30 min
# Consecutive failed status polls before an RPC download gives up. A daemon that
# is merely busy (allocating a file, hash-checking) blocks RPC for seconds; the
# first miss used to pause the download outright.
RPC_RETRIES = 10
# How long the daemon may go unanswered before a download gives up on it.
# This is a DURATION, not a retry count: at POLL=0.3s ten retries came to about
# three seconds, and a hash check on a multi-GB torrent blocks aria2's RPC
# thread for minutes. Force Recheck therefore paused itself almost immediately
# and then errored, which is exactly what a "recheck does nothing" looks like.
RPC_RETRY_GRACE = 300.0
# aria2 states that mean "this entry is a finished record, not a download".
# "complete" is deliberately NOT here: re-attaching to a completed torrent
# correctly reports it complete, whereas clearing it would restart a finished
# download.
_DEAD_RPC = ("error", "removed")
# A torrent has to earn its queue slot. After this long with NO peers and NO new
# bytes, it hands the slot back so healthy torrents behind it can run: with a
# concurrency of 1, one dead swarm otherwise blocks the whole list forever.
STALL_YIELD = 180
# Delays before a yielded torrent is tried again; the last value repeats. Long
# enough that a genuinely dead swarm is not re-announced every minute.
STALL_BACKOFF = (120, 300, 900)
# How often to refresh per-file progress. getFiles returns the WHOLE file list,
# so calling it on every 0.3s poll would be wasteful on a torrent with hundreds
# of files; the drawer only redraws twice a second anyway.

# ---------------------------------------------------------- metadata prefetch
# A magnet carries no name, size or file list — those come from the swarm. Until
# then a queued torrent shows a placeholder and the user cannot tell one row
# from another. So resolve metadata WITHOUT downloading the payload, for tasks
# that are only queued or paused.
#
# Measured on a real library: engine-start to metadata resolved was 2s at best,
# 134s median, and one case ran 1h47m. aria2 never gives up on a metadata fetch
# by itself, so both a concurrency cap and a deadline are required — without the
# deadline a dead magnet holds a slot forever however large the cap is.
META_MAX = 4              # concurrent metadata fetches, daemon-wide
META_TIMEOUT = 60.0       # give up on one torrent after this, then move on
META_RETRY = 900.0        # and leave it alone for this long before retrying
META_SCAN = 3.0           # how often to look for new candidates

FILES_POLL = 2.0
# How often each torrent asks the daemon for its status.
#
# NOT the same thing as POLL. POLL is how often the loop checks the task's own
# pause/cancel flags, which is local and free, and it has to stay small to keep
# those responsive. tellStatus is an RPC round trip, and aria2 answers RPC from
# the SAME single thread that does metadata lookups and file allocation.
#
# At POLL the two were the same, so ten running torrents put ~33 tellStatus
# calls a second into that one thread. Adding five magnets on top — real work
# for the same thread — starved it: measured in a live log, all ten torrents
# timed out (15s each) in the same second and the whole list stalled, then
# recovered. Polling status ~3x less often removes the congestion without
# making pause or cancel any slower.
STATUS_POLL = 1.0
# aria2 refuses to touch an existing payload that has no .aria2 control file: it
# cannot tell finished bytes from garbage, so it stops rather than truncate.
_CTL_MISSING = re.compile(r"control file.*does not exist", re.I)


# aria2 keeps recruiting MORE peers only while a torrent's total speed is below
# this. It is a "try harder below this" threshold, NOT a cap — but its default
# is 50 KiB/s (~0.4 Mb/s), so on any modern connection aria2 stops looking for
# peers almost immediately and settles for whatever handful it already has.
# Verified against a live daemon: bt-request-peer-speed-limit was 51200.
PEER_SPEED_TARGET = "50M"


def allocation_opt():
    """aria2's --file-allocation for the current Pre-allocate setting.

    falloc, never prealloc. "prealloc" makes aria2 WRITE ZERO BYTES across the
    whole file, and aria2 is single-threaded — so a 7 GB torrent blocks every
    other transfer while it does it. Measured on a live daemon: RPC calls that
    normally answer in 1-60ms took 1.6s, 7.3s, 8.7s, 9.5s, and the reported
    download speed collapsed to near zero for the duration. That is the
    "10 Mb/s, then 0, then it climbs back" sawtooth.

    falloc asks the filesystem to reserve the space instead, which on NTFS
    (this is the MinGW build) is near-instant, per aria2's own documentation.
    On a legacy filesystem it degrades to prealloc's behaviour — no worse than
    what it replaces.
    """
    return "--file-allocation=" + ("falloc" if getattr(utils, "PREALLOCATE", False)
                                   else "none")


def preference_opts():
    """aria2 options that come from user settings, shared by both engines.

    Kept in one place so the per-torrent subprocess and the shared daemon cannot
    drift apart — a setting that works on one engine and silently does nothing
    on the other is worse than not having it.
    """
    opts = [f"--bt-request-peer-speed-limit={PEER_SPEED_TARGET}"]
    if not getattr(utils, "SEED_ENABLED", False):
        opts.append("--seed-time=0")          # stop the moment it completes
    else:
        ratio = float(getattr(utils, "SEED_RATIO", 0) or 0)
        minutes = float(getattr(utils, "SEED_MINUTES", 0) or 0)
        if ratio > 0:
            opts.append(f"--seed-ratio={ratio:g}")
        if minutes > 0:
            opts.append(f"--seed-time={minutes:g}")
        if ratio <= 0 and minutes <= 0:
            # aria2 reads --seed-ratio=0 as "seed forever"; make that a choice
            # the user has to opt into rather than something they land on by
            # clearing two boxes
            opts.append("--seed-ratio=1.0")
    up = int(getattr(utils, "MAX_UPLOAD_BPS", 0) or 0)
    if up > 0:
        # Applies whether or not seeding is on: a torrent uploads to its peers
        # while it downloads too, and that is the traffic most likely to choke
        # an asymmetric line.
        opts.append(f"--max-overall-upload-limit={up}")
    if getattr(utils, "TORRENT_PREVIEW", False):
        # head and tail first: media containers keep their index at one end or
        # the other, so this is what lets a partial file play and seek
        opts.append("--bt-prioritize-piece=head,tail")
    return opts


def explain_failure(msg, save_path=""):
    """Turn an aria2 failure into something the user can act on.

    aria2's disk errors name an internal piece index and nothing else — the
    card read "torrent failed: Write disk cache flush failure index=608", which
    does not say that the download folder had been moved to another drive. The
    folder is the thing to look at, so look at it and say what is actually
    wrong. Returns '' when there is nothing better to say than aria2's text.
    """
    low = (msg or "").lower()
    disk = ("disk cache flush" in low or "file write failure" in low
            or "no space" in low or "cannot write" in low
            or "disk full" in low)
    if not disk:
        return ""
    folder = os.path.dirname(save_path or "") or ""
    if not folder:
        return ""
    if not os.path.isdir(folder):
        return (f"The download folder is gone — {folder} no longer exists. "
                "It was moved, renamed, or its drive is disconnected. Put it "
                "back or re-add the torrent, then Resume.")
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    # Only call it full when the measurement agrees, or when there is no
    # measurement to contradict it. aria2 reports "disk full" for a quota or a
    # per-file limit too, and "is full (271 GB free)" helps nobody.
    if free is not None:
        if free < 512 * 1024 * 1024:
            return (f"The drive holding {folder} is full ({_mib(free)} free). "
                    "Free some space, then Resume.")
    elif "no space" in low or "disk full" in low:
        return (f"The drive holding {folder} is full. Free some space, then "
                "Resume.")
    # Nothing specific found, so keep aria2's own words rather than replacing a
    # vague message with a differently vague one — it is the only clue left.
    return (f"Could not write to {folder} — {msg}. Check the folder still "
            "exists and is writable, then Resume.")


def _mib(n):
    """Short human size for engine-side error text (gui.theme is GUI-only)."""
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{int(n)} B"
# Default BitTorrent listen port when the user has not chosen one. It used to be
# left unset, which meant aria2 picked from its own range and UPnP had no single
# port to forward — so we only ever made OUTBOUND connections and never accepted
# peers. A fixed default gives UPnP something concrete to map.
DEFAULT_LISTEN_PORT = 51413

# well-known public trackers appended to every magnet so peers are found via
# trackers + DHT + PEX + LPD (a hash-only magnet otherwise leans on DHT alone)
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.stealth.si:80/announce",
]

# DHT bootstrap nodes. aria2 ships NO built-in entry point: with a cold routing
# table and no --dht-entry-point, DHT never bootstraps at all, so a magnet can
# only find peers through the trackers above — which is how a torrent ends up
# sitting at "CN:0 SD:0" forever while it downloads fine in other clients.
# More than one on purpose: a single bootstrap node is a single point of
# failure, and if it is unreachable DHT never joins at all — which is exactly
# the "CN:0 SD:0 forever" symptom these were added to fix.
DHT_ENTRY_POINTS = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
]
DHT_ENTRY_POINTS6 = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]


def is_magnet(url):
    return (url or "").strip().lower().startswith("magnet:")


def is_torrent(url="", filename=""):
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    return u.endswith(".torrent") or f.endswith(".torrent")


def local_torrent_path(url):
    """A filesystem path for a .torrent that may be given as a file:// URI.

    Browsers and drag-and-drop hand over "file:///C:/Users/.../x.torrent", and
    os.path.isfile() is False for that string even though the file is sitting
    right there. The task then looked like its .torrent had vanished, and before
    that it fell through to addUri() and got aria2's "No URI to download."
    """
    u = (url or "").strip()
    if not u.lower().startswith("file:"):
        return u
    try:
        parts = urllib.parse.urlsplit(u)
        path = urllib.parse.unquote(parts.path or "")
    except ValueError:
        return u
    # a UNC path arrives as file://server/share/x -> \\server\share\x
    if parts.netloc:
        return os.path.normpath(f"//{parts.netloc}{path}")
    # "/C:/Users/..." -> "C:/Users/..."
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return os.path.normpath(path)


def is_torrent_task(url="", filename=""):
    return is_magnet(url) or is_torrent(url, filename)


def magnet_name(url):
    """Display name from a magnet's dn= param, or '' if absent. dn= is a URL
    query value, so '+' means space (unquote_plus, not plain unquote)."""
    m = re.search(r"[?&]dn=([^&]+)", url or "", re.I)
    return urllib.parse.unquote_plus(m.group(1)) if m else ""


def magnet_infohash(url):
    """The 40-hex btih from a magnet, lowercased; '' if absent."""
    try:
        pairs = urllib.parse.parse_qsl(urllib.parse.urlsplit(url or "").query,
                                       keep_blank_values=True)
    except ValueError:
        pairs = []
    for key, value in pairs:
        if key.lower() != "xt":
            continue
        m = re.fullmatch(r"urn:btih:([A-Za-z0-9]+)", value, re.I)
        if m:
            return m.group(1).lower()
    m = re.search(r"xt=urn:btih:([A-Za-z0-9]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def magnet_trackers(url):
    """Return unique tracker URLs from a magnet URI, preserving their order."""
    if not is_magnet(url):
        return []
    try:
        query = urllib.parse.urlsplit(url).query
        values = urllib.parse.parse_qsl(query, keep_blank_values=True)
    except ValueError:
        return []
    out = []
    seen = set()
    for key, value in values:
        if key.lower() != "tr":
            continue
        for part in value.split(","):
            tracker = part.strip()
            marker = tracker.lower()
            if tracker and marker not in seen:
                seen.add(marker)
                out.append(tracker)
    return out


def merge_magnet_trackers(url, trackers):
    """Add new tracker URLs to a magnet URI without changing its identity.

    Returns ``(updated_url, added_trackers)``. Tracker URLs are treated as
    case-insensitive for de-duplication, matching URI scheme/host behaviour.
    """
    if not is_magnet(url):
        return url, []
    try:
        parts = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return url, []

    known = {tracker.lower() for tracker in magnet_trackers(url)}
    added = []
    for value in trackers:
        tracker = str(value or "").strip()
        marker = tracker.lower()
        if tracker and marker not in known:
            known.add(marker)
            added.append(tracker)
            pairs.append(("tr", tracker))
    if not added:
        return url, []
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(pairs), parts.fragment)), added


def _info_span(data):
    """Byte offsets (start, end) of a .torrent's top-level ``info`` value.

    The infohash is the SHA-1 of those RAW bytes. Re-encoding the parsed dict
    would be easier, but bencode's sorted-key rule is a spec requirement rather
    than a guarantee about files in the wild — a torrent that breaks it would
    re-encode to different bytes and hash to something no other client agrees
    with, silently, and only for the files where it matters.
    """
    def skip(i):
        c = data[i:i + 1]
        if c == b"i":
            return data.index(b"e", i) + 1
        if c in (b"l", b"d"):
            i += 1
            while data[i:i + 1] != b"e":
                i = skip(i)
            return i + 1
        j = data.index(b":", i)
        return j + 1 + int(data[i:j])

    if data[:1] != b"d":
        return -1, -1
    i = 1
    while data[i:i + 1] != b"e":
        key_end = skip(i)
        key = data[i:key_end].split(b":", 1)[-1]
        val_end = skip(key_end)
        if key == b"info":
            return key_end, val_end
        i = val_end
    return -1, -1


def torrent_infohash(path):
    """The 40-hex infohash of a .torrent file; '' if it cannot be read."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    try:
        start, end = _info_span(data)
    except (ValueError, IndexError, TypeError):
        return ""
    if start < 0:
        return ""
    return hashlib.sha1(data[start:end]).hexdigest()


def infohash_for(url, filename=""):
    """Infohash for a magnet URL or a .torrent file path; '' for anything else.

    Two tasks with the same infohash are the same torrent no matter how they
    were added — a magnet and the .torrent it resolves to included.
    """
    ih = magnet_infohash(url or "")
    if ih:
        return ih
    if is_torrent(url or "", filename or ""):
        local = local_torrent_path(url or "")
        if os.path.isfile(local):
            return torrent_infohash(local)
    return ""


def _bdecode(data):
    """Minimal bencode decoder — enough to read a .torrent's info dict.
    Returns the decoded object; raises ValueError on malformed input (callers
    treat that as 'no file list available' rather than an error)."""
    def parse(i):
        c = data[i:i + 1]
        if c == b"i":                                    # i<int>e
            j = data.index(b"e", i)
            return int(data[i + 1:j]), j + 1
        if c == b"l":                                    # l<items>e
            out, i = [], i + 1
            while data[i:i + 1] != b"e":
                v, i = parse(i)
                out.append(v)
            return out, i + 1
        if c == b"d":                                    # d<key><val>...e
            out, i = {}, i + 1
            while data[i:i + 1] != b"e":
                k, i = parse(i)
                v, i = parse(i)
                out[k] = v
            return out, i + 1
        j = data.index(b":", i)                          # <len>:<bytes>
        n = int(data[i:j])
        return data[j + 1:j + 1 + n], j + 1 + n
    try:
        return parse(0)[0]
    except (ValueError, IndexError, TypeError) as e:
        raise ValueError("malformed bencode") from e


def _file_rows(files):
    """Normalise an ``aria2.getFiles`` reply into the task's file_progress.

    aria2 sends every number as a string and marks skipped files with
    ``selected: "false"``. A magnet still resolving its metadata reports a
    single ``[METADATA]`` pseudo-file, which is not part of the payload and must
    not be shown as one.
    """
    rows = []
    for f in files or []:
        path = f.get("path") or ""
        if "[METADATA]" in path.upper() or "[MEMORY]" in path.upper():
            continue
        try:
            length = int(f.get("length") or 0)
            completed = int(f.get("completedLength") or 0)
            index = int(f.get("index") or 0)
        except (TypeError, ValueError):
            continue
        rows.append({
            "index": index,
            "path": path,
            "length": length,
            # aria2 can report a completedLength above the file length on the
            # piece that straddles a file boundary; clamp so the UI cannot show
            # 103% or a bar past its end.
            "completed": max(0, min(completed, length)) if length else completed,
            "selected": str(f.get("selected", "true")).lower() != "false",
        })
    return rows


def parse_torrent_files(path):
    """[(relative path, size_bytes)] described by a .torrent file.
    Returns [] if the file is missing or unreadable — the file list is a
    display nicety and must never break a download."""
    try:
        with open(path, "rb") as f:
            info = _bdecode(f.read())[b"info"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    dec = lambda b: b.decode("utf-8", "replace")
    try:
        if b"files" in info:                             # multi-file torrent
            out = []
            for f in info[b"files"]:
                parts = [dec(p) for p in f[b"path"]]
                out.append(("/".join(parts), int(f[b"length"])))
            return out
        return [(dec(info[b"name"]), int(info.get(b"length", 0)))]   # single file
    except (KeyError, TypeError, ValueError):
        return []


def listen_port():
    """The BitTorrent listen port actually in use: the user's setting, else the
    default. UPnP maps this, and aria2 binds it."""
    return int(getattr(utils, "LISTEN_PORT", 0) or DEFAULT_LISTEN_PORT)


def dht_dir():
    """Directory for aria2's DHT routing tables. aria2 defaults to
    ~/.cache/aria2 but does NOT create it, so it failed to save the table on
    every run and each start re-bootstrapped the DHT from scratch."""
    d = os.path.join(utils.app_data_dir(), "dht")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def metadata_dir():
    """Where saved torrent metadata is kept, out of the user's download folder.
    aria2 can only write it into --dir (there is no separate metadata path
    option), so it is moved here once a run ends."""
    d = os.path.join(utils.app_data_dir(), "torrents")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def archive_metadata(task, out_dir):
    """Move aria2's <infohash>.torrent out of the download folder into app data.
    Keeps the Files tab working while leaving no junk next to the payload.
    Best-effort: failures are ignored, the file simply stays put."""
    ih = magnet_infohash(task.url or "")
    if not ih:
        return ""
    src = os.path.join(out_dir, ih + ".torrent")
    if not os.path.isfile(src):
        return ""
    dest = os.path.join(metadata_dir(), ih + ".torrent")
    try:
        os.replace(src, dest)          # same volume in practice (%APPDATA%)
        return dest
    except OSError:
        try:
            shutil.copy2(src, dest)    # cross-volume fallback
            os.remove(src)
            return dest
        except OSError:
            return ""


def cleanup_artifacts(task):
    """Remove the aria2 leftovers for a task the user cancelled or deleted: the
    <name>.aria2 control file and our saved metadata copy. Only ever touches
    files belonging to THIS task — a blind sweep could delete the control file
    of a torrent another client (or another of our tasks) is still resuming."""
    save = getattr(task, "save_path", "") or ""
    removed = []
    if save:
        ctl = save + ".aria2"
        if os.path.isfile(ctl):
            try:
                os.remove(ctl)
                removed.append(ctl)
            except OSError:
                pass
    ih = magnet_infohash(task.url or "")
    if ih:
        for d in (metadata_dir(), os.path.dirname(save) or "."):
            p = os.path.join(d, ih + ".torrent")
            if os.path.isfile(p):
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError:
                    pass
    return removed


def metadata_torrent_path(task, *dirs):
    """Locate the .torrent describing this task: a local .torrent input as-is,
    else the copy aria2 saves as <infohash>.torrent (--bt-save-metadata) once a
    magnet's metadata arrives. '' when nothing is on disk yet."""
    url = task.url or ""
    if is_torrent(url) and os.path.isfile(local_torrent_path(url)):
        return url
    ih = magnet_infohash(url)
    if not ih:
        return ""
    # the archived copy first: that is where metadata lives once a run has
    # ended; `dirs` covers a download still in flight (aria2 writes into --dir)
    for d in (metadata_dir(),) + tuple(dirs):
        if not d:
            continue
        cand = os.path.join(d, ih + ".torrent")
        if os.path.isfile(cand):
            return cand
    return ""


def list_files(task):
    """Files inside a torrent task, as [(relative path, size)]. Reads the saved
    metadata so the list is available while downloading and after a restart —
    not just once the payload exists on disk. [] for non-torrents or before a
    magnet's metadata has been fetched."""
    if not is_torrent_task(task.url, task.filename):
        return []
    save = getattr(task, "save_path", "") or ""
    # aria2 saves the metadata in its --dir, which is the PARENT of the payload
    # folder. save_path is that folder once the download finishes and the
    # placeholder file's directory before then, so try both.
    parent = os.path.dirname(save) or "."
    dirs = [parent, os.path.dirname(parent)] if os.path.isdir(save) else [parent]
    path = metadata_torrent_path(task, *dirs)
    return parse_torrent_files(path) if path else []


def aria2c_path():
    """Locate aria2c: bundled bin/ (frozen build) first, then PATH. None if absent."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "bin", "aria2c.exe"),
                 os.path.join(base, "bin", "aria2c")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("aria2c")


# ---- aria2c progress line parsing -----------------------------------------
# e.g. "[#7d6f3a 12MiB/100MiB(12%) CN:5 SD:2 DL:2.0MiB ETA:44s]"
_UNITS = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
          "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
_PROG_RE = re.compile(
    r"([\d.]+)\s*([KMGT]?i?B)\s*/\s*([\d.]+)\s*([KMGT]?i?B)\s*\((\d+)%\)", re.I)


def _to_bytes(num, unit):
    try:
        return int(float(num) * _UNITS.get(unit.upper(), 1))
    except (ValueError, TypeError):
        return 0


def apply_metadata(task, torrent_path):
    """Fill in a task's name/size/file list from a .torrent on disk.

    Returns True when something was learned. Never raises: this is a display
    nicety and must not be able to break a download.
    """
    try:
        rows = parse_torrent_files(torrent_path)
    except Exception:
        return False
    if not rows:
        return False
    total = sum(sz for _, sz in rows)
    top = ""
    first = rows[0][0].replace("\\", "/")
    if len(rows) > 1 or "/" in first:
        top = first.split("/")[0]
    else:
        top = first
    if top and (not task.filename or task.filename.lower() in _PLACEHOLDER_FILENAMES):
        task.filename = top
    if total and not task.total_size:
        task.total_size = total
    task.file_progress = _file_rows_from_meta(rows)
    task.meta_failed = False
    return True


_PLACEHOLDER_FILENAMES = ("download.bin", "magnet_.bin", "torrent", "magnet.bin", "")


def _file_rows_from_meta(rows):
    """Files tab rows for a torrent that has not started: names and sizes are
    known, progress is not."""
    return [{"path": rel, "length": sz, "completed": 0, "selected": True}
            for rel, sz in rows]


class MetadataPrefetcher:
    """Resolves magnet metadata for tasks that are only queued or paused.

    Runs one background thread. It adds each magnet to the shared daemon with
    bt-metadata-only, so aria2 fetches the .torrent and nothing else, then reads
    the saved file and drops the entry. No queue slot is used and no payload is
    written.
    """

    def __init__(self, tasks_fn):
        self._tasks_fn = tasks_fn
        self._stop = threading.Event()
        self._thread = None
        self._active = {}                  # task id -> (gid, started_at)

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="metadata-prefetch")
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---- internals ----
    def _candidates(self):
        now = time.time()
        out = []
        for t in list(self._tasks_fn() or []):
            if getattr(t, "id", None) in self._active:
                continue
            if t.status not in (T.QUEUED, T.PAUSED, T.SCHEDULED):
                continue
            if not is_torrent_task(t.url, t.filename):
                continue
            if t.total_size:                       # already known
                continue
            if now < float(getattr(t, "meta_retry_after", 0) or 0):
                continue
            out.append(t)
        return out

    def _loop(self):
        while not self._stop.wait(META_SCAN):
            try:
                self._tick()
            except Exception as e:
                log.debug("metadata prefetch tick failed: %s", e)

    def _tick(self):
        self._reap()
        free = META_MAX - len(self._active)
        if free <= 0:
            return
        for t in self._candidates()[:free]:
            self._begin(t)

    def _begin(self, t):
        # a .torrent already on disk needs no swarm at all
        if is_torrent(t.url):
            local = local_torrent_path(t.url)
            if local and os.path.isfile(local) and apply_metadata(t, local):
                log.info("metadata read from the .torrent file: %s", t.filename)
                return
        ih = magnet_infohash(t.url or "") or getattr(t, "infohash", "")
        if ih:
            cached = os.path.join(metadata_dir(), ih + ".torrent")
            if os.path.isfile(cached) and apply_metadata(t, cached):
                log.info("metadata already saved for %s", t.filename)
                return
        try:
            import aria2d
            d = aria2d.DAEMON
            if not d.ensure():
                return
            gid = d.call("aria2.addUri", [t.url], {
                "dir": metadata_dir(),
                "bt-metadata-only": "true",
                "bt-save-metadata": "true",
            })
        except Exception as e:
            log.debug("metadata prefetch could not start for %s: %s", t.filename, e)
            return
        t.meta_fetching = True
        self._active[t.id] = (gid, time.time())
        log.info("fetching metadata for %s", t.filename or t.url[:60])

    def _reap(self):
        if not self._active:
            return
        try:
            import aria2d
            d = aria2d.DAEMON
        except Exception:
            return
        now = time.time()
        for t in list(self._tasks_fn() or []):
            entry = self._active.get(getattr(t, "id", None))
            if not entry:
                continue
            gid, started = entry
            done = False
            try:
                st = d.call("aria2.tellStatus", gid) or {}
            except Exception:
                st = {}
            ih = (st.get("infoHash") or magnet_infohash(t.url or "") or "").lower()
            if ih:
                saved = os.path.join(metadata_dir(), ih + ".torrent")
                if os.path.isfile(saved) and apply_metadata(t, saved):
                    log.info("metadata resolved: %s", t.filename)
                    done = True
            if not done and now - started >= META_TIMEOUT:
                # Say so on the card rather than leaving it looking busy, and
                # get out of the way so the next torrent can try.
                t.meta_failed = True
                t.meta_retry_after = now + META_RETRY
                log.info("gave up fetching metadata for %s after %.0fs",
                         t.filename or t.url[:60], META_TIMEOUT)
                done = True
            if done:
                t.meta_fetching = False
                self._active.pop(t.id, None)
                try:
                    self._drop(d, gid)
                except Exception:
                    pass

    @staticmethod
    def _drop(d, gid):
        for method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
            try:
                d.call(method, gid)
            except Exception:
                pass



class TorrentDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask
        self._gid = None          # aria2 download id, when driven over RPC
        self._stall_since = None  # when this torrent last earned its slot
        self._stall_bytes = -1

    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.supports_range = False
        log.info("torrent start: %s", self.t.filename or self.t.url[:80])

        if not aria2c_path():
            self.t.status = T.ERROR
            self.t.error = ("aria2c not found — bundle bin/aria2c.exe or install "
                            "aria2 to download torrents/magnets.")
            return
        try:
            return self._run_rpc()
        except Exception as e:
            # There is no second engine to fall back to any more, so say what
            # happened rather than failing blankly — and stay resumable: the
            # bytes and the .aria2 control file are still on disk.
            if self.t.cancel_requested or self.t.pause_requested:
                return
            self.t.status = T.PAUSED
            self.t.error = f"Torrent engine unavailable ({e}) — Resume to retry"
            log.warning("torrent engine unavailable for %s: %s",
                        self.t.filename, e)

    # ------------------------------------------------------------- RPC engine
    def _run_rpc(self):
        """Drive the download through the shared aria2 daemon.

        Behaviour is deliberately identical to the subprocess engine — same
        status transitions, same name/save_path resolution, same artifact
        cleanup — so the two are interchangeable while the RPC path proves
        itself. What differs is invisible to the task: one warm DHT table and
        one forwardable listen port shared by every torrent, and status read as
        JSON instead of scraped from stdout.
        """
        import aria2d

        out_dir = os.path.dirname(self.t.save_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        self._started = time.time()

        # display name, same rules as the subprocess path
        if is_magnet(self.t.url):
            self.t.filename = magnet_name(self.t.url) or "torrent"
        elif (not self.t.filename or is_magnet(self.t.filename)
              or self.t.filename.endswith((".torrent", ".bin"))):
            self.t.filename = "torrent"

        d = aria2d.DAEMON
        d.ensure()                                   # raises -> caller falls back
        opts = {"dir": out_dir}
        picked = (getattr(self.t, "selected_files", "") or "").strip()
        if picked:
            # Re-apply the user's file choice on every start. Without it, a
            # paused torrent came back with everything selected again — aria2
            # only ever knew about the selection through a live changeOption.
            opts["select-file"] = picked
        if self._take_recheck():
            opts["check-integrity"] = "true"
            # The daemon almost certainly still holds this torrent (pausing only
            # pauses it), and re-attaching to that registration would carry the
            # OLD options — so the recheck would quietly not happen, which is
            # exactly what "Force Recheck does nothing" looks like.
            self._rpc_drop_existing(d)
        # A recheck must NOT re-attach: we just dropped the registration on
        # purpose, and re-attaching would silently hand back the old one with
        # its old options, so check-integrity would never apply. (force_recheck
        # is already cleared by _take_recheck, so the opts are the signal.)
        gid = self._rpc_add(d, opts, reattach="check-integrity" not in opts)
        self._gid = gid
        self.t.gid = gid

        top = ""
        try:
            top = self._poll_rpc(d, gid, out_dir)
            if (self.t.status == T.ERROR
                    and _CTL_MISSING.search(self.t.error or "")
                    and not (self.t.cancel_requested or self.t.pause_requested)):
                # The payload is on disk but its .aria2 control file is gone —
                # typically because aria2 was hard-killed before it could flush
                # one. aria2 then refuses the torrent outright and the download
                # is dead for good. check-integrity re-hashes what is already
                # there and rebuilds the control file, so it resumes from the
                # verified bytes. Confirmed offline on a real multi-file
                # torrent: the plain add fails with exactly this message, while
                # the same add with check-integrity is accepted and counts the
                # intact pieces, leaving the files untruncated.
                log.info("control file missing for %s — retrying with an "
                         "integrity check", self.t.filename)
                self.t.error = ""
                self.t.status = T.DOWNLOADING
                self._rpc_drop_existing(d)      # same reason as the recheck path
                gid = self._rpc_add(d, dict(opts, **{"check-integrity": "true"}),
                                    reattach=False)
                self._gid = gid
                self.t.gid = gid
                top = self._poll_rpc(d, gid, out_dir)
        finally:
            self._gid = None
            self.t.gid = None
        return top

    def _take_recheck(self):
        """True once if Force Recheck was asked for, clearing the request.

        Consumed rather than read so a recheck happens exactly once: leaving the
        flag set would re-hash the whole payload on every pause/resume from then
        on, which on a 50 GB torrent is not a small mistake.
        """
        if not getattr(self.t, "force_recheck", False):
            return False
        self.t.force_recheck = False
        self.t.log_event("Verifying downloaded data")
        log.info("force recheck: %s", self.t.filename)
        return True

    def _note_stall(self, peers, done):
        """True once this torrent has gone STALL_YIELD seconds earning nothing.

        Earning nothing means no peers AND no new bytes. Peers-but-slow is NOT
        stalled: a torrent trickling from a single seeder is alive, and taking
        its slot away would be worse than letting it finish. Only a swarm that
        is not answering at all gives way.
        """
        now = time.time()
        if peers > 0 or done != self._stall_bytes:
            if peers > 0 and done != self._stall_bytes:
                self.t.stall_count = 0        # it recovered; forgive the record
            self._stall_bytes = done
            self._stall_since = now
            return False
        if self._stall_since is None:
            self._stall_since = now
            return False
        return (now - self._stall_since) >= STALL_YIELD

    def _yield_slot(self, d, gid, out_dir):
        """Hand the queue slot back, to be retried after a growing delay."""
        n = int(getattr(self.t, "stall_count", 0) or 0)
        delay = STALL_BACKOFF[min(n, len(STALL_BACKOFF) - 1)]
        self.t.stall_count = n + 1
        self.t.retry_after = time.time() + delay
        self.t._stall_yield = True            # queue_manager re-queues on this
        if d is not None and gid:
            self._rpc_remove(d, gid, force=True)
        archive_metadata(self.t, out_dir)
        self.t.status = T.QUEUED
        self.t.error = ""
        log.info("stalled (no peers, no progress): %s — slot released, retry in %ds",
                 self.t.filename, delay)

    def _disk_guard(self, total, out_dir):
        """True if the volume cannot hold what is left of this torrent.

        A magnet has no size until its metadata arrives, so this cannot be a
        pre-flight check — it runs the moment the total becomes known. Only the
        REMAINING bytes matter: a resumed torrent has already paid for what is
        on disk. Without it a torrent larger than the free space preallocates,
        runs for hours and dies on ENOSPC with nothing to show.
        """
        short = utils.disk_shortfall(out_dir, max(0, total - self.t.downloaded))
        if not short:
            return False
        self.t.status = T.ERROR
        self.t.error = (
            f"Not enough disk space — {_mib(short)} more needed on this drive. "
            "Free some space, or pick another folder in Settings, then Resume.")
        log.warning("insufficient disk space for %s: short by %s",
                    self.t.filename, _mib(short))
        return True

    def _rpc_add(self, d, opts, reattach=True):
        """Hand the torrent/magnet to the daemon and return its gid.

        The daemon outlives both the task and the app, so the torrent may
        already be in it — pausing only calls aria2.pause, which leaves it
        registered, and a restart reattaches to the same daemon. Adding it again
        then fails with "InfoHash ... is already registered" and the download
        goes to Error, which is the opposite of what resuming should do.
        """
        # Look BEFORE adding. aria2 does not reject a duplicate magnet at add
        # time — verified against a live daemon: addUri succeeds, hands back a
        # NEW gid, and that download then sits in status=error with "InfoHash
        # ... is already registered" in errorMessage. Catching an exception here
        # therefore never fires, and the dead duplicate is what the task ends up
        # polling, which is how Resume and Force Recheck both landed in Error.
        existing = self._rpc_find_existing(d) if reattach else ""
        if existing and not self._entry_dir_matches(d, existing, opts):
            # The payload has moved since this entry was registered. aria2's
            # --dir is fixed at add time, so re-attaching would keep writing to
            # the OLD folder — and if that folder is gone the download dies with
            # "Write disk cache flush failure", which says nothing about the
            # move that caused it. Drop it and add again at the new location.
            self._rpc_remove(d, existing, force=True)
            existing = ""
        if existing:
            log.info("torrent already in the daemon, re-attaching: %s",
                     self.t.filename)
            try:
                d.call("aria2.unpause", existing)
            except Exception:
                pass                      # already running is fine
            return existing
        try:
            return self._rpc_add_new(d, opts)
        except Exception as e:
            if "already registered" not in str(e).lower():
                raise
            gid = self._rpc_find_existing(d)
            if not gid:
                raise
            try:
                d.call("aria2.unpause", gid)
            except Exception:
                pass
            return gid

    def _rpc_add_new(self, d, opts):
        import base64
        if is_torrent(self.t.url):
            src = self._torrent_file()
            if not src:
                # a plain error the caller can show; aria2's own wording for
                # this ("No URI to download.") explains nothing to a user
                raise FileNotFoundError(
                    f"the .torrent file is no longer at {self.t.url}")
            with open(src, "rb") as f:
                return d.call("aria2.addTorrent",
                              base64.b64encode(f.read()).decode(), [], opts)
        return d.call("aria2.addUri", [self.t.url], opts)

    def _torrent_file(self):
        """A readable .torrent for this task, keeping our own copy.

        The user's original file moves, gets cleaned up, or sits on a drive that
        is not mounted — and then the add fell through to addUri() with a file
        PATH, which aria2 rejects as "No URI to download." The task ended up
        named just "torrent" and unable to start ever again.

        So the first time it is readable we take a copy into app data, keyed by
        infohash, and fall back to that copy afterwards.
        """
        src = local_torrent_path(self.t.url or "")
        if os.path.isfile(src):
            ih = getattr(self.t, "infohash", "") or torrent_infohash(src)
            if ih:
                self.t.infohash = ih
                keep = os.path.join(metadata_dir(), ih + ".torrent")
                if not os.path.isfile(keep):
                    try:
                        os.makedirs(metadata_dir(), exist_ok=True)
                        shutil.copy2(src, keep)
                    except OSError as e:
                        log.debug("could not keep a copy of %s: %s", src, e)
            return src
        ih = getattr(self.t, "infohash", "")
        if ih:
            keep = os.path.join(metadata_dir(), ih + ".torrent")
            if os.path.isfile(keep):
                log.info("original .torrent is gone; using the archived copy "
                         "for %s", self.t.filename)
                return keep
        return ""

    def _entry_dir_matches(self, d, gid, opts):
        """True when the daemon entry writes where this task now saves.

        Unknown counts as a match: a daemon that will not answer is not
        evidence that the folder changed, and dropping a healthy registration
        on that basis would restart a download for no reason.
        """
        want = ((opts or {}).get("dir") or "").strip()
        if not want:
            return True
        try:
            have = str((d.call("aria2.tellStatus", gid) or {}).get("dir") or "")
        except Exception:
            return True
        if not have:
            return True
        same = (os.path.normcase(os.path.abspath(have))
                == os.path.normcase(os.path.abspath(want)))
        if not same:
            log.info("daemon entry for %s still writes to %s but the task now "
                     "saves to %s — re-adding rather than re-attaching",
                     self.t.filename, have, want)
        return same

    def _rpc_drop_existing(self, d):
        """Unregister this torrent from the daemon so the next add is a real
        add, with the options we are about to pass, rather than a re-attach to
        whatever it was started with."""
        gid = self._rpc_find_existing(d, live_only=False)
        if gid:
            self._rpc_remove(d, gid, force=True)

    def _rpc_find_existing(self, d, live_only=True):
        """The gid the daemon already holds for this torrent, or ''.

        tellStopped returns download RESULTS — the finished, removed and errored
        entries aria2 keeps only for reporting. Re-attaching to one of those is
        never right: aria2.unpause does nothing to a dead result, so the task
        polled it, read status=error straight back, and failed in under a
        second. Once a torrent had hit --bt-stop-timeout, every Force Start
        re-attached to the corpse of the previous attempt and failed instantly,
        forever. Measured in a real log: 14 failures at 0 minutes elapsed, the
        same handful of titles over and over.

        So a dead result is cleared instead of re-attached, which also stops the
        fresh add being refused as "InfoHash ... is already registered".
        Pass live_only=False when the caller wants to drop ANY registration.
        """
        want = infohash_for(self.t.url, self.t.filename)
        if not want:
            return ""
        dead = []
        for method, params in (("aria2.tellActive", ()),
                               ("aria2.tellWaiting", (0, 500)),
                               ("aria2.tellStopped", (0, 500))):
            try:
                rows = d.call(method, *params) or []
            except Exception:
                continue
            for r in rows:
                if str(r.get("infoHash") or "").lower() != want:
                    continue
                gid = r.get("gid") or ""
                if not gid:
                    continue
                if live_only and str(r.get("status") or "").lower() in _DEAD_RPC:
                    dead.append(gid)
                    continue
                return gid
        for gid in dead:
            log.info("clearing a dead daemon entry for %s so it can start fresh",
                     self.t.filename)
            try:
                d.call("aria2.removeDownloadResult", gid)
            except Exception:
                pass
        return ""

    def _poll_rpc(self, d, gid, out_dir):
        """Mirror aria2's state onto the task until it reaches a terminal one."""
        cur = gid
        top = ""
        fails = 0
        fail_since = 0.0
        last_files = 0.0
        checked_disk = False
        # Spread the first status call across the interval. Torrents added
        # together otherwise poll in lockstep for the rest of their lives and
        # keep hitting the daemon as one burst instead of a trickle.
        last_status = 0.0
        first_delay = (sum(ord(c) for c in str(self.t.id)) % 100) / 100.0 * STATUS_POLL
        while True:
            if self.t.cancel_requested:
                self._rpc_remove(d, cur, force=True)
                cleanup_artifacts(self.t)
                self.t.status = T.CANCELLED
                return top
            if self.t.pause_requested:
                try:
                    d.call("aria2.pause", cur)
                except Exception:
                    pass
                archive_metadata(self.t, out_dir)
                self.t.status = T.PAUSED
                return top

            # Pause/cancel above are local flags and stay on the fast POLL beat;
            # everything below is an RPC round trip, so it runs on STATUS_POLL.
            now = time.time()
            if now - last_status < (STATUS_POLL if last_status else first_delay):
                time.sleep(POLL)
                continue
            last_status = now

            try:
                st = d.call("aria2.tellStatus", cur)
                fails = 0
            except Exception as e:
                # We no longer know how the swarm looks, so stop presenting the
                # last reading as if it were current. A busy daemon left these
                # standing for minutes: the sidebar showed "8 peers" while the
                # daemon actually reported 1, because every task kept summing a
                # count from its last successful poll.
                self.t.tor_conns = 0
                self.t.tor_seeds = 0
                self.t.tor_upload = 0
                if "is not found" in str(e):
                    # The daemon was replaced under us, so our gid died with it.
                    # Nothing is wrong with the download itself — the bytes and
                    # control file are on disk — so stay resumable and say so
                    # accurately instead of blaming the engine for stopping.
                    self.t.status = T.PAUSED
                    self.t.error = "Torrent engine restarted — Resume to continue"
                    log.warning("gid %s lost for %s (daemon replaced)",
                                cur, self.t.filename)
                    return top
                if not fails:
                    fail_since = time.time()
                fails += 1
                waited = time.time() - fail_since
                if waited < RPC_RETRY_GRACE:
                    # Busy, not dead. Back off instead of hammering a daemon
                    # that is already blocked on a hash check.
                    log.debug("tellStatus retry %d (%.0fs of %.0fs) for %s: %s",
                              fails, waited, RPC_RETRY_GRACE, self.t.filename, e)
                    time.sleep(min(2.0, POLL * fails))
                    continue
                # daemon vanished mid-download: leave the task resumable rather
                # than failing it — the bytes and control file are still on disk
                self.t.status = T.PAUSED
                self.t.error = "Torrent engine stopped — Resume to continue"
                log.warning("tellStatus failed for %s: %s", self.t.filename, e)
                return top

            # a magnet's metadata download spawns the real payload as followedBy
            follow = st.get("followedBy") or []
            if follow:
                cur = follow[0]
                # Publish the gid we are ACTUALLY polling. The task kept the
                # original metadata gid, so the drawer's Connections tab asked
                # aria2 for the peers of the metadata download — which has none,
                # and the tab sat on "Connecting…" for the whole download.
                self._gid = self.t.gid = cur
                continue

            # Identify the payload FIRST. A magnet starts as a download of the
            # .torrent itself, reported as a "[METADATA]" pseudo-file whose
            # completedLength and totalLength are the metadata's own few KB.
            # Reading progress from that is how a torrent that had downloaded
            # nothing reported itself complete: the two were briefly equal, the
            # seeding branch fired, and because that branch continues, the code
            # that identifies the real payload never ran again.
            files = st.get("files") or []
            first = (files[0].get("path") or "") if files else ""
            meta_stage = bool(first) and ("[METADATA]" in first.upper()
                                          or "[MEMORY]" in first.upper())
            if not top and files and not meta_stage:
                entry = self._top_entry(first, out_dir)
                if entry:
                    top = entry
                    self.t.filename = entry
                    # aria2 drops <infohash>.torrent into --dir the moment the
                    # metadata resolves. Move it out NOW rather than at the end
                    # of the download, so it is not sitting next to the payload
                    # for the hours in between.
                    archive_metadata(self.t, out_dir)

            total = 0
            if not meta_stage:
                self.t.downloaded = int(st.get("completedLength") or 0)
                total = int(st.get("totalLength") or 0)
                if total:
                    self.t.total_size = total
                    if not checked_disk:
                        checked_disk = True
                        if self._disk_guard(total, out_dir):
                            self._rpc_remove(d, cur, force=True)
                            return top
            self.t.tor_conns = int(st.get("connections") or 0)
            self.t.tor_seeds = int(st.get("numSeeders") or 0)
            self.t.tor_upload = int(st.get("uploadSpeed") or 0)

            # Hash checking. aria2 reports it separately from download progress,
            # and it can take minutes on a large torrent — with nothing shown, a
            # Force Recheck is indistinguishable from a hang.
            verified = int(st.get("verifiedLength") or 0)
            pending = str(st.get("verifyIntegrityPending") or "").lower() == "true"
            was = self.t.verifying
            self.t.verifying = bool(verified or pending)
            self.t.verified_pct = (int(verified * 100 / total)
                                   if (verified and total) else 0)
            self.t.verified_bytes = verified
            if self.t.verifying and not was:
                self.t.log_event("Verifying downloaded data")
                log.info("verifying: %s", self.t.filename)
            elif was and not self.t.verifying:
                self.t.log_event("Verification finished")
                log.info("verification finished: %s (%s of %s present)",
                         self.t.filename, _mib(self.t.downloaded), _mib(total))

            # Per-file progress. The Files tab used to derive this from the size
            # of each file on disk, which is not progress at all: aria2
            # preallocates, so every file read 100% the moment the download
            # started, and even without preallocation BitTorrent fetches pieces
            # out of order, so a file reaches full size when its LAST piece
            # lands. Only aria2 knows how much of each file is really there.
            #
            # Refreshed BEFORE the seeding branch below, which continues: with
            # it the other way round the per-file numbers froze at whatever they
            # were the instant the torrent completed, so a finished, seeding
            # torrent showed a file stuck at 98% and a "Downloading 1" tile.
            now = time.time()
            # On demand. getFiles is only ever consumed by the drawer's Files tab
            # and the preview action, so polling it every FILES_POLL for every
            # torrent spent a third of all torrent RPC on a panel that is usually
            # closed. Fetch once when the list is first known (so the tab is not
            # blank on opening) and then only while someone is watching.
            watching = bool(getattr(self.t, "files_watched", False))
            need_first = bool(total) and not self.t.file_progress
            if (watching or need_first) and now - last_files >= FILES_POLL:
                last_files = now
                try:
                    self.t.file_progress = _file_rows(d.call("aria2.getFiles", cur))
                except Exception as e:
                    log.debug("getFiles failed for %s: %s", self.t.filename, e)

            # Seeding: the payload is fully downloaded but aria2 keeps the
            # torrent "active" while it shares. Without recognising that, the
            # task sat at 100% in the Active list looking hung — and the stall
            # check below would eventually decide a quiet swarm meant it should
            # give up its slot.
            if (not meta_stage and total and self.t.downloaded >= total
                    and st.get("status") == "active"):
                if not self.t.seeding:
                    self.t.seeding = True
                    self.t.status = T.COMPLETED
                    self.t.log_event("Seeding")
                    log.info("seeding: %s", self.t.filename)
                    # Downloading is done, so stop occupying a download slot.
                    # This loop keeps running (pause, remove and the live upload
                    # figure all need it) but the queue is free to start what is
                    # waiting behind us. Without this, torrents that finish with
                    # no peers seed forever at 0 B/s and block the queue — which
                    # is what a Force Recheck on five finished torrents did.
                    release = getattr(self.t, "_release_slot", None)
                    if release:
                        try:
                            release()
                        except Exception as e:
                            log.debug("could not release the slot for %s: %s",
                                      self.t.filename, e)
                time.sleep(POLL)
                continue

            status = st.get("status")
            if status == "complete" and meta_stage:
                # The METADATA download finished, not the torrent. followedBy
                # normally appears in the same reply and is handled above, but
                # if it lags by a poll we must not report the payload done.
                time.sleep(POLL)
                continue
            if status == "complete":
                self.t.seeding = False
                self.t.tor_upload = 0
                if self.t.total_size:
                    self.t.downloaded = self.t.total_size
                archive_metadata(self.t, out_dir)
                self._resolve_save_path(out_dir, top)     # before COMPLETED
                self.t.status = T.COMPLETED
                log.info("torrent done: %s", self.t.filename)
                return top
            if status in ("error", "removed"):
                msg = st.get("errorMessage") or ""
                if "already registered" in msg.lower():
                    # This gid is the dead duplicate aria2 creates rather than
                    # refusing the add. The real download is elsewhere in the
                    # daemon — switch to it instead of failing the task.
                    real = self._rpc_find_existing(d)
                    if real and real != cur:
                        log.info("duplicate entry for %s — following the one "
                                 "already running", self.t.filename)
                        self._rpc_remove(d, cur, force=True)
                        cur = real
                        self._gid = self.t.gid = cur      # keep the task in step
                        try:
                            d.call("aria2.unpause", cur)
                        except Exception:
                            pass
                        continue
                archive_metadata(self.t, out_dir)
                if not top and self.t.downloaded == 0:
                    # never reached the payload and never saw a peer: a cold
                    # swarm, not a broken torrent — stay resumable
                    self.t.status = T.PAUSED
                    self.t.error = ("No peers found yet — the swarm may be offline "
                                    "or still waking up. Resume to try again.")
                else:
                    self.t.status = T.ERROR
                    if msg:
                        self.t.error = (explain_failure(msg, self.t.save_path)
                                        or f"torrent failed: {msg}")
                    else:
                        # aria2 leaves errorMessage empty when it stops a torrent
                        # itself, which --bt-stop-timeout does after STALL_TIMEOUT
                        # seconds of no progress. A bare "torrent failed" told the
                        # user nothing; measured against a real log every one of
                        # these had run for almost exactly 30 minutes.
                        mins = int(STALL_TIMEOUT // 60)
                        code = str(st.get("errorCode") or "")
                        self.t.error = (
                            f"No data received for {mins} minutes — the swarm "
                            "has no active seeders right now. Resume to try again."
                            + (f" (aria2 code {code})" if code and code != "0" else ""))
                log.info("torrent ended (%s): %s%s", status, self.t.filename,
                         f" errorCode={st.get('errorCode')}" if not msg else "")
                return top

            if self._note_stall(self.t.tor_conns, self.t.downloaded):
                self._yield_slot(d, cur, out_dir)
                return top

            time.sleep(POLL)

    @staticmethod
    def _rpc_remove(d, gid, force=False):
        for method in (("aria2.forceRemove" if force else "aria2.remove"),
                       "aria2.removeDownloadResult"):
            try:
                d.call(method, gid)
            except Exception:
                pass

    @staticmethod
    def _top_entry(path, out_dir):
        """Top-level entry name under out_dir from an aria2 'FILE:' line value
        like ' C:/dir/TorrentName/sub/file.ext (12 more)'. '' if unresolved."""
        raw = path or ""
        multi = bool(re.search(r"\(\d+\s*more\)\s*$", raw, flags=re.I))
        path = re.sub(r"\s*\(\d+\s*more\)\s*$", "", raw, flags=re.I)
        path = path.strip().strip('"')
        if not path:
            return ""
        # Unresolvable means unknown, NOT "use the file's own name". aria2
        # reports a path on the drive it was added against, so after the payload
        # is moved to another drive relpath raises and the basename is one
        # EPISODE of a season pack — which then replaced the torrent's real name
        # on the card. Returning '' leaves the existing name alone.
        try:
            rel = os.path.relpath(path, out_dir)
        except ValueError:                         # different drive, etc.
            return ""
        first = rel.replace("\\", "/").split("/")[0]
        if first in ("", ".", ".."):
            return ""
        # "(N more)" is aria2 telling us this torrent has several files, so a
        # bare filename cannot be the top-level entry.
        if multi and first == os.path.basename(path.rstrip("/\\")):
            return ""
        return first

    def _resolve_save_path(self, out_dir, top):
        """Point self.t.save_path at the real downloaded entry. Prefer the name
        captured from aria2's FILE: output; else fall back to the newest entry
        TOUCHED DURING THIS RUN (so we don't grab an unrelated, pre-existing
        file in a shared download folder). Leaves save_path unchanged if nothing
        qualifies — the dialogs then fall back to opening the folder."""
        if top and os.path.exists(os.path.join(out_dir, top)):
            self.t.save_path = os.path.join(out_dir, top)
            self.t.filename = top
            return
        started = getattr(self, "_started", 0)
        newest = None
        try:
            for name in os.listdir(out_dir):
                if name.endswith((".aria2", ".torrent", ".hfdownload", ".tmp")) or ".part" in name:
                    continue
                p = os.path.join(out_dir, name)
                mt = os.path.getmtime(p)
                if mt + 2 < started:               # existed before this run began
                    continue
                if newest is None or mt > newest[0]:
                    newest = (mt, name)
        except OSError:
            newest = None
        if newest:
            self.t.save_path = os.path.join(out_dir, newest[1])
            # note: don't rename the task here — the newest-entry heuristic can pick
            # an unrelated file; keep the reliable magnet dn / FILE-line name

