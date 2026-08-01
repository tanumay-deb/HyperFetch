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
import signal
import logging
import threading
import subprocess
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
FILES_POLL = 2.0
# aria2 refuses to touch an existing payload that has no .aria2 control file: it
# cannot tell finished bytes from garbage, so it stops rather than truncate.
_CTL_MISSING = re.compile(r"control file.*does not exist", re.I)


def preference_opts():
    """aria2 options that come from user settings, shared by both engines.

    Kept in one place so the per-torrent subprocess and the shared daemon cannot
    drift apart — a setting that works on one engine and silently does nothing
    on the other is worse than not having it.
    """
    opts = []
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
    if getattr(utils, "TORRENT_PREVIEW", False):
        # head and tail first: media containers keep their index at one end or
        # the other, so this is what lets a partial file play and seek
        opts.append("--bt-prioritize-piece=head,tail")
    return opts


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
    if is_torrent(url) and os.path.isfile(url):
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


def parse_progress(line):
    """Return (downloaded_bytes, total_bytes) from an aria2c readout line,
    or None if the line has no progress figure."""
    m = _PROG_RE.search(line or "")
    if not m:
        return None
    done = _to_bytes(m.group(1), m.group(2))
    total = _to_bytes(m.group(3), m.group(4))
    return done, total


# CN = connected peers, SD = seeders in aria2's readout, e.g. "CN:51 SD:13"
_PEERS_RE = re.compile(r"CN:(\d+).*?SD:(\d+)", re.I)


def parse_peers(line):
    """Return (connected_peers, seeders) from an aria2c readout line, or None."""
    m = _PEERS_RE.search(line or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


class ARIA2_MISSING(RuntimeError):
    pass


class TorrentDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask
        self._proc = None
        self._gid = None          # aria2 download id, when driven over RPC
        self._stall_since = None  # when this torrent last earned its slot
        self._stall_bytes = -1

    def _build_cmd(self, exe, out_dir):
        src = self.t.url
        all_trackers = list(PUBLIC_TRACKERS)
        if is_magnet(src):
            all_trackers.extend([t for t in magnet_trackers(src) if t not in all_trackers])
        
        cmd = [
            exe,
            "--dir", out_dir,
            *preference_opts(),           # seeding + preview, from Settings
            # Stall timeout. Only meaningful once the payload is transferring:
            # during the metadata phase a magnet legitimately sits at 0 B/s for
            # minutes while DHT is queried, and a short timeout there is what
            # turned slow-but-fine magnets into "failed".
            f"--bt-stop-timeout={STALL_TIMEOUT}",
            "--summary-interval=1",      # emit a progress readout each second
            "--console-log-level=warn",
            "--bt-save-metadata=true",
            # reuse metadata we already saved: a re-added or resumed magnet then
            # skips the metadata fetch entirely instead of re-querying the swarm
            "--bt-load-saved-metadata=true",
            "--continue=true",           # resume from .aria2 control file
            # peer discovery — match what desktop torrent clients do so a bare
            # magnet finds peers via more than DHT alone (the usual reason a
            # magnet "works in qBittorrent but stalls here").
            "--enable-dht=true",
            "--enable-dht6=true",
            "--enable-peer-exchange=true",
            "--bt-enable-lpd=true",       # local peer discovery
            "--bt-max-peers=0",           # unlimited peers
            "--bt-tracker=" + ",".join(all_trackers),
            # Persist the DHT routing table so the next run starts with a warm
            # table instead of bootstrapping from nothing. aria2's default path
            # is ~/.cache/aria2/dht.dat, whose directory it does NOT create —
            # it threw "Failed to save DHT routing table" on every run.
            f"--dht-file-path={os.path.join(dht_dir(), 'dht.dat')}",
            f"--dht-file-path6={os.path.join(dht_dir(), 'dht6.dat')}",
        ]
        # Bootstrap nodes — without these a cold routing table can never join
        # the DHT, leaving magnets entirely dependent on the trackers above.
        for host, port in DHT_ENTRY_POINTS:
            cmd.append(f"--dht-entry-point={host}:{port}")
        for host, port in DHT_ENTRY_POINTS6:
            cmd.append(f"--dht-entry-point6={host}:{port}")
        # --- settings wired from the GUI (Settings -> Network / Advanced) ---
        # Always bind an explicit port (user's or the default) so it matches the
        # one UPnP forwards; leaving it unset meant inbound peers had nowhere to
        # land.
        port = listen_port()
        cmd += [f"--listen-port={port}", f"--dht-listen-port={port}"]
        if not utils.DISK_CACHE:
            cmd.append("--disk-cache=0")
        cmd.append("--file-allocation=" + ("prealloc" if utils.PREALLOCATE else "none"))
        if utils.PROXIES:
            purl = utils.PROXIES.get("https") or utils.PROXIES.get("http")
            if purl:
                cmd.append(f"--all-proxy={purl}")
        cmd.append(src)
        return cmd

    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.supports_range = False
        log.info("torrent start: %s", self.t.filename or self.t.url[:80])

        exe = aria2c_path()
        if not exe:
            self.t.status = T.ERROR
            self.t.error = ("aria2c not found — bundle bin/aria2c.exe or install "
                            "aria2 to download torrents/magnets.")
            return

        # Shared-daemon engine, when enabled. Falls back to the per-task
        # subprocess below if the daemon cannot be reached, so a broken daemon
        # degrades to the old behaviour instead of failing the download.
        if getattr(utils, "TORRENT_RPC", False):
            try:
                return self._run_rpc()
            except Exception as e:
                log.warning("RPC engine unavailable (%s) — falling back to the "
                            "per-task subprocess engine", e)
                if self.t.cancel_requested or self.t.pause_requested:
                    return

        out_dir = os.path.dirname(self.t.save_path) or "."
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"cannot create folder: {e}"
            return

        # derive a sane display name: magnet dn= wins; else a junk default
        # (the raw magnet string, "*.bin", "*.torrent") becomes "torrent".
        if is_magnet(self.t.url):
            self.t.filename = magnet_name(self.t.url) or "torrent"
        elif (not self.t.filename or is_magnet(self.t.filename)
              or self.t.filename.endswith((".torrent", ".bin"))):
            self.t.filename = "torrent"

        self._started = time.time()           # used by the save_path fallback
        try:
            self._proc = subprocess.Popen(
                self._build_cmd(exe, out_dir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                # Its own process group, so _stop can send it a CTRL_BREAK
                # without the signal also reaching HyperFetch itself.
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"failed to start aria2c: {e}"
            return

        # The reader parses each readout line AS IT ARRIVES and writes progress
        # straight onto the task. aria2's --summary-interval prints a 3-line
        # block per tick (the "[#.. X/Y(%)..]" progress line, then a "FILE:"
        # line, then a "----" separator), so the old "keep only the latest line
        # and sample it from the control loop" approach almost always sampled
        # the FILE:/separator line and missed the progress line entirely —
        # leaving the task pinned at 0% until completion. Parse in place instead.
        tail = []
        seen = {"top": "", "disk": False}
        self._disk_abort = False
        # epoch tying this reader to this run: downloader.py builds a fresh
        # TorrentDownloader on every resume against the SAME task, so an old
        # reader draining a dying aria2 could clobber the new run's progress.
        # Each run bumps the task's epoch; a reader only writes while it's still
        # the current one AND the task is still downloading.
        gen = getattr(self.t, "_tor_gen", 0) + 1
        self.t._tor_gen = gen
        # aria2 always prints this footer/legend on a non-zero exit — it is NOT
        # a real error, so drop it from the message we surface.
        FOOTER = ("(OK):", "aria2 will resume", "If there are any errors",
                  "See '-l'", "Download Results", "Status Legend", "===", "gid ")

        def reader():
            for ln in self._proc.stdout:
                if self.t._tor_gen != gen:         # a newer run owns the task now
                    return
                prog = parse_progress(ln)
                if prog is not None:
                    if self.t.status == T.DOWNLOADING:
                        done, total = prog
                        # accept once the real payload is known (a FILE: line was
                        # seen) or the size is clearly payload-sized — this skips
                        # the magnet METADATA flash (a few KB at 100%) without
                        # pinning genuinely small torrents at 0%.
                        if seen["top"] or total >= 1_000_000:
                            self.t.downloaded, self.t.total_size = done, total
                            if not seen["disk"]:
                                seen["disk"] = True
                                if self._disk_guard(total, out_dir):
                                    # _disk_guard has set the status/error; the
                                    # control loop below tears aria2 down
                                    self._disk_abort = True
                                    return
                        peers = parse_peers(ln)
                        if peers:
                            self.t.tor_conns, self.t.tor_seeds = peers
                    continue
                s = ln.strip()
                if s.startswith("FILE:"):
                    # capture the torrent's real top-level entry so save_path can
                    # be repointed at the actual download (not the placeholder).
                    # A magnet's FIRST download is the metadata, whose FILE: line
                    # is the "[MEMORY][METADATA]<name>" pseudo-entry — not a real
                    # path. Skip it (without consuming seen["top"]) so the real
                    # payload FILE: line that follows is the one captured.
                    val = s[5:].strip()
                    if "[METADATA]" in val.upper() or "[MEMORY]" in val.upper():
                        continue
                    if not seen["top"]:
                        top = self._top_entry(val, out_dir)
                        if top:
                            seen["top"] = top
                            self.t.filename = top     # real torrent name (matches the on-disk entry)
                    continue
                if s and not any(k in s for k in FOOTER):   # keep real errors only
                    tail.append(s)
                    if len(tail) > 20:
                        tail.pop(0)

        rt = threading.Thread(target=reader, daemon=True)
        rt.start()

        while self._proc.poll() is None:
            if self.t.cancel_requested or self.t.pause_requested or self._disk_abort:
                self._stop()
                break
            try:
                self._proc.wait(timeout=POLL)
            except subprocess.TimeoutExpired:
                pass

        # stop this run's reader before finalizing so a late buffered line can't
        # overwrite the final progress / completion state.
        if self.t._tor_gen == gen:
            self.t._tor_gen = gen + 1
        rt.join(timeout=2)

        if self._disk_abort:
            # status and error already say why; keep the control file so the
            # partial payload resumes once space has been freed
            archive_metadata(self.t, out_dir)
            return
        if self.t.cancel_requested:
            # cancelled for good: take the control file and metadata with it
            # instead of leaving them next to the half-downloaded payload
            cleanup_artifacts(self.t)
            self.t.status = T.CANCELLED
            return
        if self.t.pause_requested:
            # a pause KEEPS the .aria2 control file — it is what lets aria2
            # resume from the partial data — but the metadata can still move
            archive_metadata(self.t, out_dir)
            self.t.status = T.PAUSED
            return

        # aria2 can exit non-zero even when the payload finished (seeding
        # interrupted, a non-fatal per-file error). Trust the bytes: if it's all
        # there, it's complete.
        complete = (self._proc.returncode == 0
                    or (self.t.total_size and self.t.downloaded >= self.t.total_size))
        # aria2 removes its own .aria2 control file on success; the saved
        # metadata is ours, so move it out of the user's download folder on
        # every terminal outcome (the Files tab reads it from there).
        archive_metadata(self.t, out_dir)
        if complete:
            if self.t.total_size:
                self.t.downloaded = self.t.total_size
            # repoint save_path at the real on-disk entry aria2 created (a folder
            # for multi-file torrents, a file for single) so Properties and
            # "Open File" work — the placeholder download.bin never existed.
            # Resolve BEFORE flipping to COMPLETED: the GUI's completion tick
            # categorizes by save_path, and must never see COMPLETED with the
            # placeholder path. (The reader is already fenced off by the _tor_gen
            # bump above, so status order no longer guards against stray writes.)
            self._resolve_save_path(out_dir, seen["top"])
            self.t.status = T.COMPLETED
            log.info("torrent done: %s", self.t.filename)
        elif not seen["top"] and self.t.downloaded == 0:
            # Never got past the metadata phase and never saw a peer: the swarm
            # is cold right now, not broken. Calling that a red "failed" was
            # wrong — it is exactly the case that starts working later, so end
            # PAUSED and resumable instead. (Resume re-launches aria2, which now
            # reuses the saved metadata and the warm DHT table.)
            self.t.status = T.PAUSED
            self.t.error = ("No peers found yet — the swarm may be offline or "
                            "still waking up. Resume to try again.")
            log.info("torrent found no peers: %s (peers=%d seeds=%d)",
                     self.t.filename, self.t.tor_conns, self.t.tor_seeds)
        else:
            self.t.status = T.ERROR
            msg = " | ".join(tail[-3:])
            self.t.error = "torrent failed" + (f": {msg}" if msg
                                               else f" (aria2 exit {self._proc.returncode})")
            log.warning("torrent failed: %s — %s", self.t.filename, self.t.error)

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
        gid = self._rpc_add(d, opts)
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
                gid = self._rpc_add(d, dict(opts, **{"check-integrity": "true"}))
                self._gid = gid
                self.t.gid = gid
                top = self._poll_rpc(d, gid, out_dir)
        finally:
            self._gid = None
            self.t.gid = None
        return top

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

    def _rpc_add(self, d, opts):
        """Hand the torrent/magnet to the daemon and return its gid."""
        import base64
        if is_torrent(self.t.url) and os.path.isfile(self.t.url):
            with open(self.t.url, "rb") as f:
                return d.call("aria2.addTorrent",
                              base64.b64encode(f.read()).decode(), [], opts)
        return d.call("aria2.addUri", [self.t.url], opts)

    def _poll_rpc(self, d, gid, out_dir):
        """Mirror aria2's state onto the task until it reaches a terminal one."""
        cur = gid
        top = ""
        fails = 0
        last_files = 0.0
        checked_disk = False
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

            try:
                st = d.call("aria2.tellStatus", cur)
                fails = 0
            except Exception as e:
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
                fails += 1
                if fails < RPC_RETRIES:
                    # busy daemon, not a dead one: keep polling
                    log.debug("tellStatus retry %d/%d for %s: %s",
                              fails, RPC_RETRIES, self.t.filename, e)
                    time.sleep(POLL)
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
                continue

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

            # Per-file progress. The Files tab used to derive this from the size
            # of each file on disk, which is not progress at all: aria2
            # preallocates, so every file read 100% the moment the download
            # started, and even without preallocation BitTorrent fetches pieces
            # out of order, so a file reaches full size when its LAST piece
            # lands. Only aria2 knows how much of each file is really there.
            now = time.time()
            if now - last_files >= FILES_POLL:
                last_files = now
                try:
                    self.t.file_progress = _file_rows(d.call("aria2.getFiles", cur))
                except Exception as e:
                    log.debug("getFiles failed for %s: %s", self.t.filename, e)

            files = st.get("files") or []
            if not top and files:
                p = files[0].get("path") or ""
                if "[METADATA]" not in p.upper() and "[MEMORY]" not in p.upper():
                    entry = self._top_entry(p, out_dir)
                    if entry:
                        top = entry
                        self.t.filename = entry

            status = st.get("status")
            if status == "complete":
                if self.t.total_size:
                    self.t.downloaded = self.t.total_size
                archive_metadata(self.t, out_dir)
                self._resolve_save_path(out_dir, top)     # before COMPLETED
                self.t.status = T.COMPLETED
                log.info("torrent done: %s", self.t.filename)
                return top
            if status in ("error", "removed"):
                archive_metadata(self.t, out_dir)
                msg = st.get("errorMessage") or ""
                if not top and self.t.downloaded == 0:
                    # never reached the payload and never saw a peer: a cold
                    # swarm, not a broken torrent — stay resumable
                    self.t.status = T.PAUSED
                    self.t.error = ("No peers found yet — the swarm may be offline "
                                    "or still waking up. Resume to try again.")
                else:
                    self.t.status = T.ERROR
                    self.t.error = "torrent failed" + (f": {msg}" if msg else "")
                log.info("torrent ended (%s): %s", status, self.t.filename)
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
        path = re.sub(r"\s*\(\d+\s*more\)\s*$", "", path or "", flags=re.I)
        path = path.strip().strip('"')
        if not path:
            return ""
        try:
            rel = os.path.relpath(path, out_dir)
        except ValueError:                         # different drive, etc.
            return os.path.basename(path.rstrip("/\\"))
        first = rel.replace("\\", "/").split("/")[0]
        if first in ("", ".", ".."):
            return os.path.basename(path.rstrip("/\\"))
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

    def _stop(self):
        """Ask aria2 to stop, and only force it if it will not.

        Popen.terminate() on Windows is TerminateProcess — the process dies
        where it stands with no chance to write anything out. Measured against a
        real magnet: after a terminate() the DHT routing table was simply gone,
        while the same run stopped with a CTRL_BREAK saved it. A cold routing
        table every launch is the difference between finding peers in seconds
        and bootstrapping the swarm from nothing, which is exactly the "works in
        qBittorrent but stalls here" complaint.
        """
        p = self._proc
        if not p:
            return
        brk = getattr(signal, "CTRL_BREAK_EVENT", None)
        if brk is not None:
            try:
                p.send_signal(brk)
                p.wait(timeout=STOP_GRACE)
                return
            except subprocess.TimeoutExpired:
                pass          # ignored the polite request; escalate below
            except (OSError, ValueError):
                pass          # not a console process / already gone
        try:
            p.terminate()
            p.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass
        except OSError:
            pass
