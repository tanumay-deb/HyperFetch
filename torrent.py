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
import threading
import subprocess
import urllib.parse

import task as T
import utils

POLL = 0.3            # seconds between pause/cancel checks
STOP_GRACE = 5        # seconds to wait after terminate before kill

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


def is_magnet(url):
    return (url or "").strip().lower().startswith("magnet:")


def is_torrent(url="", filename=""):
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    return u.endswith(".torrent") or f.endswith(".torrent")


def is_torrent_task(url="", filename=""):
    return is_magnet(url) or is_torrent(url, filename)


def magnet_name(url):
    """Display name from a magnet's dn= param, or '' if absent."""
    m = re.search(r"[?&]dn=([^&]+)", url or "", re.I)
    return urllib.parse.unquote(m.group(1)) if m else ""


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
            "--bt-stop-timeout=300",     # give up if a swarm stalls for 5 min
            "--summary-interval=1",      # emit a progress readout each second
            "--console-log-level=warn",
            "--bt-save-metadata=true",
            "--continue=true",           # resume from .aria2 control file
            # peer discovery — match what desktop torrent clients do so a bare
            # magnet finds peers via more than DHT alone (the usual reason a
            # magnet "works in qBittorrent but stalls here").
            "--enable-dht=true",
            "--enable-peer-exchange=true",
            "--bt-enable-lpd=true",       # local peer discovery
            "--bt-max-peers=0",           # unlimited peers
            "--bt-tracker=" + ",".join(PUBLIC_TRACKERS),
        ]
        # --- settings wired from the GUI (Settings -> Network / Advanced) ---
        if utils.LISTEN_PORT:
            cmd += [f"--listen-port={utils.LISTEN_PORT}",
                    f"--dht-listen-port={utils.LISTEN_PORT}"]
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
                    # be repointed at the actual download (not the placeholder)
                    if not seen["top"]:
                        top = self._top_entry(s[5:], out_dir)
                        if top:
                            seen["top"] = top
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
            self.t.status = T.CANCELLED
            return
        if self.t.pause_requested:
            self.t.status = T.PAUSED
            return

        # aria2 can exit non-zero even when the payload finished (seeding
        # interrupted, a non-fatal per-file error). Trust the bytes: if it's all
        # there, it's complete.
        complete = (self._proc.returncode == 0
                    or (self.t.total_size and self.t.downloaded >= self.t.total_size))
        if complete:
            self.t.status = T.COMPLETED        # set first: stops a stray reader write
            if self.t.total_size:
                self.t.downloaded = self.t.total_size
            # repoint save_path at the real on-disk entry aria2 created (a folder
            # for multi-file torrents, a file for single) so Properties and
            # "Open File" work — the placeholder download.bin never existed.
            self._resolve_save_path(out_dir, seen["top"])
        else:
            self.t.status = T.ERROR
            msg = " | ".join(tail[-3:])
            self.t.error = "torrent failed" + (f": {msg}" if msg
                                               else f" (aria2 exit {self._proc.returncode})")

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
