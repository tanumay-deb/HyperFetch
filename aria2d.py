"""Shared aria2 daemon + JSON-RPC client (Phase 1 of the RPC migration).

Why a daemon at all: the legacy engine spawns one aria2c process PER torrent.
Each gets its own cold DHT routing table, and they all try to bind the same
BitTorrent listen port — measured: three concurrent torrents, only two
listeners, so the third ran outbound-only with a starved peer set. One daemon
means one warm DHT table, one listen port that UPnP can actually forward, and
status by JSON instead of regex-parsing stdout.

Lifecycle rules, each learned from the Phase 0 spike:

* **Attach before spawning.** A daemon from a previous run may still be alive;
  a second process can talk to it fine (verified), so we reuse it rather than
  starting a rival that would fight for the same ports.
* **Orphans are real.** aria2 survives its parent being killed (verified), so
  the pid/port/secret are written to disk and a stale-but-alive daemon that no
  longer answers RPC is killed rather than left running forever.
* **Loopback + secret only.** ``--rpc-listen-all=false`` binds 127.0.0.1, and a
  per-run random ``--rpc-secret`` is required on every call (a wrong token is
  refused — verified). Same posture as the app's own Flask server.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import utils

log = utils.get_logger("aria2d")

RPC_HOST = "127.0.0.1"
START_TIMEOUT = 20.0        # seconds to wait for a fresh daemon to answer
CALL_TIMEOUT = 15.0
# aria2 serves RPC from the same thread that allocates files and hash-checks, so
# a big torrent can stall it for seconds. A short probe reads a BUSY daemon as a
# dead one, and killing it mid-download is destructive (see _attach).
PROBE_TIMEOUT = 10.0
LIVENESS_TTL = 2.0          # how long one successful probe is trusted for
DEAD_STRIKES = 3            # consecutive missed probes before declaring death
# ...and it must have been quiet for at least this long as well. Strikes alone
# are not enough: a hash check blocks aria2's RPC thread for MINUTES, so three
# missed probes can mean "verifying a 40 GB torrent", and killing it there is
# both wrong and destructive.
DEAD_GRACE = 300.0
# Headroom over the app's own limit. aria2 must never be the narrower of the
# two: the app decides what runs, and anything aria2 holds back sits in its
# queue looking stalled with no explanation anywhere in the UI. This used to be
# a flat 12 while the queue spinbox went to 16, so asking for more than 12
# quietly did nothing.
CONCURRENCY_HEADROOM = 4
MIN_CONCURRENT = 8


def max_concurrent():
    """How many downloads the daemon may run at once."""
    return max(MIN_CONCURRENT,
               int(getattr(utils, "MAX_CONCURRENT_DOWNLOADS", 0) or 0)
               + CONCURRENCY_HEADROOM)


class Aria2Error(RuntimeError):
    """An RPC call returned an error, or the daemon is unreachable."""


def _state_path():
    return os.path.join(utils.app_data_dir(), "aria2d.json")


def _free_port():
    s = socket.socket()
    try:
        s.bind((RPC_HOST, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _pid_alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return str(pid) in out
    except Exception:
        return False


def _our_aria2_pids():
    """PIDs of running aria2c processes started from OUR bundled binary.

    Matching on the executable path is the whole point: the user may have their
    own aria2 installed and running for something else, and that must never be
    touched.
    """
    exe = _aria2c_path()
    if not exe or sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='aria2c.exe'\" | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    want = os.path.normcase(os.path.abspath(exe))
    pids = []
    for line in (out or "").splitlines():
        pid, _, path = line.partition("|")
        path = path.strip()
        if not path:
            continue
        try:
            if os.path.normcase(os.path.abspath(path)) == want:
                pids.append(int(pid.strip()))
        except (ValueError, OSError):
            continue
    return pids


def _kill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(int(pid))],
                       capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


class Aria2Daemon:
    """One aria2 process shared by every torrent task."""

    def __init__(self):
        self._lock = threading.RLock()
        self.port = 0
        self.secret = ""
        self.pid = 0
        self._proc = None
        self._last_ok = 0.0     # monotonic stamp of the last successful probe
        self._strikes = 0       # consecutive missed probes
        self._strike_since = 0.0

    # ------------------------------------------------------------- transport
    def _post(self, port, secret, method, params, timeout=CALL_TIMEOUT):
        body = json.dumps({"jsonrpc": "2.0", "id": "hf", "method": method,
                           "params": [f"token:{secret}"] + list(params or [])}).encode()
        req = urllib.request.Request(f"http://{RPC_HOST}:{port}/jsonrpc", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            # aria2 answers 4xx with the real JSON-RPC error in the BODY, but
            # urllib raises before anyone reads it — so "GID x is not found" and
            # "Unauthorized" both reached the log as the useless "HTTP Error
            # 400: Bad Request", which reads like a transport fault rather than
            # the state problem it actually is.
            try:
                detail = (json.loads(e.read().decode() or "{}")
                          .get("error", {}).get("message"))
            except Exception:
                detail = None
            raise Aria2Error(detail or f"HTTP {e.code}") from None
        if "error" in payload:
            raise Aria2Error(payload["error"])
        return payload.get("result")

    def call(self, method, *params, timeout=CALL_TIMEOUT):
        """One RPC call against the running daemon (starting it if needed)."""
        self.ensure()
        return self._post(self.port, self.secret, method, params, timeout)

    def alive(self):
        if not (self.port and self.secret):
            return False
        # Every torrent thread calls ensure() on every RPC. Probing the daemon
        # each time is pure load amplification on the very thing we are asking
        # "are you overloaded?", so a fresh success is trusted briefly.
        if time.monotonic() - self._last_ok < LIVENESS_TTL:
            return True
        try:
            self._post(self.port, self.secret, "aria2.getGlobalStat", [],
                       timeout=PROBE_TIMEOUT)
        except Exception:
            return False
        self._last_ok = time.monotonic()
        self._strikes = 0
        return True

    # ------------------------------------------------------------- lifecycle
    def _load_state(self):
        try:
            with open(_state_path(), encoding="utf-8") as f:
                d = json.load(f)
            return int(d.get("pid", 0)), int(d.get("port", 0)), str(d.get("secret", ""))
        except Exception:
            return 0, 0, ""

    def _save_state(self, retries=3, delay=0.2):
        """Record the daemon we just started.

        This used to swallow OSError silently, and a failed write is not a small
        thing: aria2d.json is the ONLY way a later run finds this daemon. If it
        keeps pointing at a dead one, the app cannot attach, cannot shut it
        down, and cannot even reap it — which is exactly the stale record and
        orphaned aria2c seen in the wild.
        """
        payload = {"pid": self.pid, "port": self.port, "secret": self.secret}
        for attempt in range(retries):
            try:
                with open(_state_path(), "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                return True
            except OSError as e:
                if attempt == retries - 1:
                    log.error("could not record the aria2 daemon (%s) — a later "
                              "run will not find pid %s and may leave it "
                              "running", e, self.pid)
                    return False
                time.sleep(delay)
        return False

    def _attach(self):
        """Reuse a daemon left by an earlier run. Returns True on success.

        A recorded process that is alive but silent is only replaced after
        DEAD_STRIKES consecutive misses. Replacing it on the FIRST miss was
        actively destructive: a daemon busy allocating or hash-checking a large
        torrent stops answering for seconds, so healthy engines were killed
        mid-download. Worse, _kill is a hard kill — aria2 never flushed its
        .aria2 control files, so the payloads it left behind could not be
        resumed at all ("exists, but a control file(*.aria2) does not exist"),
        and every running task died with "Torrent engine stopped".
        """
        pid, port, secret = self._load_state()
        if not (port and secret):
            return False
        try:
            self._post(port, secret, "aria2.getGlobalStat", [],
                       timeout=PROBE_TIMEOUT)
        except Exception as e:
            if not _pid_alive(pid):
                self._strikes = 0
                return False
            self._strikes += 1
            if self._strikes == 1:
                self._strike_since = time.monotonic()
            quiet = time.monotonic() - self._strike_since
            if self._strikes < DEAD_STRIKES or quiet < DEAD_GRACE:
                log.warning("aria2 daemon pid %s did not answer (%s) — strike "
                            "%d/%d, quiet for %.0fs of %.0fs",
                            pid, e, self._strikes, DEAD_STRIKES, quiet, DEAD_GRACE)
                # Busy, not dead. Tell the caller to retry rather than letting
                # it spawn a rival daemon that would fight for the same ports.
                raise Aria2Error(f"aria2 daemon busy: {e}")
            log.warning("aria2 daemon pid %s silent for %.0fs over %d probes — "
                        "replacing it", pid, quiet, self._strikes)
            self._strikes = 0
            _kill(pid)
            return False
        self.pid, self.port, self.secret = pid, port, secret
        self._last_ok = time.monotonic()
        self._strikes = 0
        log.info("attached to existing aria2 daemon pid=%s port=%s", pid, port)
        return True

    def _spawn(self):
        exe = _aria2c_path()
        if not exe:
            raise Aria2Error("aria2c not found")
        self.port = _free_port()
        self.secret = secrets.token_urlsafe(24)
        cmd = [exe] + self._options()
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pid = self._proc.pid
        # Do NOT use alive() here. It trusts a probe from up to LIVENESS_TTL ago,
        # and that probe belonged to the PREVIOUS daemon — so a fresh spawn could
        # be declared up without anything ever contacting it, and recorded before
        # it was listening. Probe this port directly.
        self._last_ok = 0.0
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            try:
                self._post(self.port, self.secret, "aria2.getGlobalStat", [],
                           timeout=3)
            except Exception:
                if self._proc.poll() is not None:
                    raise Aria2Error(
                        f"aria2 daemon exited immediately (code {self._proc.returncode})")
                time.sleep(0.2)
                continue
            self._last_ok = time.monotonic()
            self._save_state()
            log.info("started aria2 daemon pid=%s port=%s", self.pid, self.port)
            return True
        _kill(self.pid)
        raise Aria2Error("aria2 daemon did not answer RPC in time")

    def _options(self):
        """Daemon-wide options. The per-download ones (dir, pause) are passed
        with each add call instead."""
        import torrent
        opts = [
            "--enable-rpc",
            f"--rpc-listen-port={self.port}",
            f"--rpc-secret={self.secret}",
            "--rpc-listen-all=false",          # loopback only
            "--console-log-level=error",
            "--auto-save-interval=30",
            # The APP owns the task list: every torrent is (re-)added through
            # addUri when its task runs, and --continue picks up from the
            # .aria2 control file. Reloading a session on top of that both
            # duplicated those downloads and, far worse, filled every
            # concurrency slot with dead entries from previous runs — measured
            # 5 active / 22 waiting / 75 stopped, so nothing new ever started
            # and magnets sat in "waiting" forever.
            f"--max-concurrent-downloads={max_concurrent()}",
            *torrent.preference_opts(),        # seeding + preview, from Settings
            f"--bt-stop-timeout={torrent.STALL_TIMEOUT}",
            "--bt-save-metadata=true",
            "--bt-load-saved-metadata=true",
            "--continue=true",
            # peer discovery — one daemon means ONE warm routing table and ONE
            # listen port, instead of a cold table per torrent all fighting for
            # the same port (the legacy engine's core weakness)
            "--enable-dht=true",
            "--enable-dht6=true",
            "--enable-peer-exchange=true",
            "--bt-enable-lpd=true",
            "--bt-max-peers=0",
            "--bt-tracker=" + ",".join(torrent.PUBLIC_TRACKERS),
            f"--dht-file-path={os.path.join(torrent.dht_dir(), 'dht.dat')}",
            f"--dht-file-path6={os.path.join(torrent.dht_dir(), 'dht6.dat')}",
            f"--listen-port={torrent.listen_port()}",
            f"--dht-listen-port={torrent.listen_port()}",
        ]
        for host, port in torrent.DHT_ENTRY_POINTS:
            opts.append(f"--dht-entry-point={host}:{port}")
        for host, port in torrent.DHT_ENTRY_POINTS6:
            opts.append(f"--dht-entry-point6={host}:{port}")
        if not utils.DISK_CACHE:
            opts.append("--disk-cache=0")
        opts.append(torrent.allocation_opt())
        if utils.PROXIES:
            purl = utils.PROXIES.get("https") or utils.PROXIES.get("http")
            if purl:
                opts.append(f"--all-proxy={purl}")
        return opts

    def ensure(self):
        """Guarantee a reachable daemon. Cheap when one is already running."""
        with self._lock:
            if self.alive():
                return True
            if self._attach():
                self._purge()
                self._reap_others()
                return True
            ok = self._spawn()
            self._purge()
            self._reap_others()
            return ok

    def _reap_others(self):
        """Kill aria2 daemons of ours that nothing is tracking any more.

        _attach only knows the daemon named in aria2d.json. The moment that
        record is overwritten, any earlier daemon becomes both unreachable and
        unkillable by the app: it keeps running, holding a BitTorrent listen
        port and its memory, until the machine reboots. Found one in the wild
        doing exactly that.

        Only runs when we have just attached or spawned — never on the common
        already-alive path, which every RPC call goes through.
        """
        for pid in _our_aria2_pids():
            if pid and pid != self.pid:
                log.info("reaping orphaned aria2 daemon pid=%s", pid)
                _kill(pid)

    def _purge(self):
        """Drop finished/errored results. They are only history to aria2, but
        they accumulate across runs and make tellStatus noisy."""
        try:
            self._post(self.port, self.secret, "aria2.purgeDownloadResult", [], timeout=5)
        except Exception:
            pass

    def apply_concurrency(self):
        """Push the current limit to a daemon that is already running, so
        changing the setting takes effect without restarting the app."""
        if not (self.port and self.secret):
            return
        try:
            self._post(self.port, self.secret, "aria2.changeGlobalOption",
                       [{"max-concurrent-downloads": str(max_concurrent())}],
                       timeout=3)
        except Exception as e:
            log.debug("could not update daemon concurrency: %s", e)

    def stop_recorded(self):
        """Stop whatever daemon aria2d.json points at, even from a fresh object.

        shutdown() only worked if THIS instance was already connected, so a new
        process (or a new app run) could not stop the daemon a previous run had
        left behind — it would just attach to it, inheriting its old options.
        """
        with self._lock:
            if not self.alive():
                pid, port, secret = self._load_state()
                if not (port and secret):
                    return
                self.pid, self.port, self.secret = pid, port, secret
            self.shutdown()

    def shutdown(self, wait=5.0, force=True):
        """Stop the daemon we own. Saves the session first so in-flight
        downloads resume next launch.

        ``wait`` is how long to watch for the process to actually go, and
        ``force`` whether to hard-kill it if it does not. The app's exit path
        passes force=False deliberately: a hard kill is what stops aria2
        flushing its .aria2 control files, and a payload without one cannot be
        resumed at all. A daemon that is slow to close is still closing — and
        if it really is wedged, the next launch's _attach finds it, reuses it if
        it answers, and replaces it if it does not. Nothing is leaked either
        way, so there is no reason to destroy state on the way out.
        """
        with self._lock:
            if not (self.port and self.secret):
                return
            # forceShutdown, not shutdown: the graceful one contacts every
            # tracker to unregister first, and against dead or slow public
            # trackers it simply does not return — measured, a daemon told to
            # shut down was still running minutes later. forceShutdown skips
            # that but is still aria2's OWN exit path, so it flushes the .aria2
            # control files (verified: exits in ~4s, control file written).
            # That is the whole difference from TerminateProcess, which does not
            # flush and leaves payloads that cannot be resumed.
            #
            # No saveSession here: the daemon runs without --save-session on
            # purpose (the app owns the task list), so the call fails with
            # "Filename is not given." Resume comes from the .aria2 control
            # files, which aria2 writes every --auto-save-interval seconds and
            # again on the way out.
            asked = True
            try:
                self._post(self.port, self.secret, "aria2.forceShutdown", [],
                           timeout=3)
            except Exception as e:
                log.warning("aria2 daemon did not accept forceShutdown: %s", e)
                asked = False
            if not asked and force:
                _kill(self.pid)
            if wait <= 0 and not force:
                # Nothing below depends on knowing whether it has gone yet, and
                # _pid_alive shells out to tasklist — pure latency on the path
                # between clicking the cross and the process ending. aria2 has
                # been told to save and close; leave aria2d.json for the next
                # run to reuse or clean up.
                return
            gone = not _pid_alive(self.pid)
            deadline = time.monotonic() + max(0.0, wait)
            while not gone and time.monotonic() < deadline:
                time.sleep(0.25)
                gone = not _pid_alive(self.pid)
            if not gone and force:
                _kill(self.pid)
                gone = True
            if not gone:
                # still on its way out: keep aria2d.json so the next run can
                # find it rather than spawning a rival for the same ports
                return
            try:
                os.remove(_state_path())
            except OSError:
                pass
            self.pid = self.port = 0
            self.secret = ""
            self._last_ok = 0.0


def _aria2c_path():
    import torrent
    return torrent.aria2c_path()


# process-wide singleton — every torrent task shares this one daemon
DAEMON = Aria2Daemon()
