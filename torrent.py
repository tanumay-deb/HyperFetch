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
import shutil
import threading
import subprocess
import urllib.parse

import task as T

POLL = 0.3            # seconds between pause/cancel checks
STOP_GRACE = 5        # seconds to wait after terminate before kill


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


class ARIA2_MISSING(RuntimeError):
    pass


class TorrentDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask
        self._proc = None

    def _build_cmd(self, exe, out_dir):
        src = self.t.url
        return [
            exe,
            "--dir", out_dir,
            "--seed-time=0",              # don't seed after completing
            "--bt-stop-timeout=300",     # give up if a swarm stalls for 5 min
            "--summary-interval=1",      # emit a progress readout each second
            "--console-log-level=warn",
            "--bt-save-metadata=true",
            "--continue=true",           # resume from .aria2 control file
            src,
        ]

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

        # reader thread keeps the latest readout line; the control loop below
        # parses it without blocking on readline (so pause/cancel stay snappy)
        last = {"line": ""}
        tail = []

        def reader():
            for ln in self._proc.stdout:
                last["line"] = ln
                if _PROG_RE.search(ln) is None:    # keep non-progress lines for errors
                    tail.append(ln.strip())
                    if len(tail) > 20:
                        tail.pop(0)

        rt = threading.Thread(target=reader, daemon=True)
        rt.start()

        while self._proc.poll() is None:
            if self.t.cancel_requested or self.t.pause_requested:
                self._stop()
                self.t.status = T.CANCELLED if self.t.cancel_requested else T.PAUSED
                return
            prog = parse_progress(last["line"])
            if prog:
                self.t.downloaded, self.t.total_size = prog
            try:
                self._proc.wait(timeout=POLL)
            except subprocess.TimeoutExpired:
                pass

        rt.join(timeout=1)
        # final progress sample
        prog = parse_progress(last["line"])
        if prog:
            self.t.downloaded, self.t.total_size = prog

        if self.t.cancel_requested:
            self.t.status = T.CANCELLED
            return
        if self.t.pause_requested:
            self.t.status = T.PAUSED
            return
        if self._proc.returncode == 0:
            if self.t.total_size:
                self.t.downloaded = self.t.total_size
            self.t.status = T.COMPLETED
        else:
            self.t.status = T.ERROR
            self.t.error = ("torrent failed: "
                            + (" | ".join(tail[-3:]) or f"aria2c exit {self._proc.returncode}"))

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
