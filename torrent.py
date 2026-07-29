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
    m = re.search(r"xt=urn:btih:([A-Za-z0-9]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


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

    def _build_cmd(self, exe, out_dir):
        src = self.t.url
        cmd = [
            exe,
            "--dir", out_dir,
            "--seed-time=0",              # don't seed after completing
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
            "--bt-tracker=" + ",".join(PUBLIC_TRACKERS),
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
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
        seen = {"top": ""}
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
            if self.t.cancel_requested or self.t.pause_requested:
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
        p = self._proc
        if not p:
            return
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
