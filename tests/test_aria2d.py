"""Shared aria2 daemon + the RPC torrent engine.

No real aria2 process and no network: the daemon's RPC transport is stubbed so
the lifecycle rules and the status mapping can be asserted directly.
"""
import io
import json
import os

import pytest

import aria2d
import task as T
import torrent
import utils


# --------------------------------------------------------------- lifecycle
def test_attach_reuses_a_reachable_daemon(tmp_path, monkeypatch):
    """A daemon left by an earlier run must be reused, not raced with a rival
    that would fight it for the BitTorrent port."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "aria2d.json").write_text(json.dumps(
        {"pid": 4321, "port": 6800, "secret": "s"}), encoding="utf-8")

    d = aria2d.Aria2Daemon()
    monkeypatch.setattr(d, "_post", lambda *a, **k: {"ok": 1})   # answers RPC
    spawned = {"n": 0}
    monkeypatch.setattr(d, "_spawn", lambda: spawned.__setitem__("n", 1))

    assert d.ensure() is True
    assert (d.pid, d.port, d.secret) == (4321, 6800, "s")
    assert spawned["n"] == 0                       # nothing new started


def _silent_daemon(tmp_path, monkeypatch, pid=9999):
    """A recorded daemon whose process is alive but never answers RPC."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "aria2d.json").write_text(json.dumps(
        {"pid": pid, "port": 6800, "secret": "s"}), encoding="utf-8")
    d = aria2d.Aria2Daemon()

    def dead(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(d, "_post", dead)
    monkeypatch.setattr(aria2d, "_pid_alive", lambda p: p == pid)
    killed = []
    monkeypatch.setattr(aria2d, "_kill", killed.append)
    return d, killed


def test_unreachable_but_live_daemon_is_killed(tmp_path, monkeypatch):
    """The orphan case proven in the spike: aria2 outlives its parent. One that
    is alive but no longer answering must eventually be killed, not left
    holding ports — but only after it has really stopped answering."""
    d, killed = _silent_daemon(tmp_path, monkeypatch)
    # A kill now needs BOTH the strikes and a long quiet spell, so that a hash
    # check (which blocks aria2's RPC thread for minutes) is not mistaken for a
    # dead daemon. Collapse the grace for the test.
    monkeypatch.setattr(aria2d, "DEAD_GRACE", 0.0)
    for _ in range(aria2d.DEAD_STRIKES - 1):
        with pytest.raises(aria2d.Aria2Error):
            d._attach()
    assert d._attach() is False
    assert killed == [9999]


def test_a_busy_daemon_survives_a_single_missed_probe(tmp_path, monkeypatch):
    """The regression that cost real downloads: aria2 blocks its RPC thread
    while allocating or hash-checking, so one missed probe means BUSY. Killing
    on the first miss took out healthy engines mid-download — and being a hard
    kill, it left payloads with no .aria2 control file, which aria2 then refuses
    to resume at all."""
    d, killed = _silent_daemon(tmp_path, monkeypatch)
    with pytest.raises(aria2d.Aria2Error):
        d._attach()
    assert killed == []                    # still running, still downloading


def test_one_good_probe_clears_accumulated_strikes(tmp_path, monkeypatch):
    """Strikes must be consecutive, or a daemon that stutters once an hour is
    eventually executed for it."""
    d, killed = _silent_daemon(tmp_path, monkeypatch)
    with pytest.raises(aria2d.Aria2Error):
        d._attach()
    monkeypatch.setattr(d, "_post", lambda *a, **k: {"ok": 1})   # answers again
    assert d._attach() is True
    assert d._strikes == 0 and killed == []


def test_http_error_body_carries_the_real_aria2_message(tmp_path, monkeypatch):
    """aria2 puts the useful error in the BODY of a 4xx. urllib raises before
    anyone reads it, so "GID x is not found" used to reach the log as the
    meaningless "HTTP Error 400: Bad Request"."""
    d = aria2d.Aria2Daemon()

    def boom(*a, **k):
        raise aria2d.urllib.error.HTTPError(
            "u", 400, "Bad Request", {},
            io.BytesIO(json.dumps(
                {"error": {"message": "GID abc is not found"}}).encode()))

    monkeypatch.setattr(aria2d.urllib.request, "urlopen", boom)
    with pytest.raises(aria2d.Aria2Error) as e:
        d._post(1, "s", "aria2.tellStatus", ["abc"])
    assert "GID abc is not found" in str(e.value)


def test_dead_pid_is_not_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "aria2d.json").write_text(json.dumps(
        {"pid": 1234, "port": 6800, "secret": "s"}), encoding="utf-8")
    d = aria2d.Aria2Daemon()
    monkeypatch.setattr(d, "_post", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(aria2d, "_pid_alive", lambda pid: False)
    killed = []
    monkeypatch.setattr(aria2d, "_kill", killed.append)
    assert d._attach() is False
    assert killed == []


def test_missing_state_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    assert aria2d.Aria2Daemon()._attach() is False


def test_rpc_error_becomes_aria2error(tmp_path, monkeypatch):
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 1, "s"

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"error": {"message": "bad"}}).encode()

    monkeypatch.setattr(aria2d.urllib.request, "urlopen", lambda *a, **k: _R())
    with pytest.raises(aria2d.Aria2Error):
        d._post(1, "s", "aria2.tellStatus", [])


# --------------------------------------------------------------- RPC engine
class _FakeDaemon:
    """Replays a scripted sequence of tellStatus replies."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        self.calls.append((method, params))
        if method == "aria2.tellStatus":
            return self.states.pop(0) if self.states else {"status": "complete"}
        if method in ("aria2.addUri", "aria2.addTorrent"):
            return "gid1"
        return {}


def _task(tmp_path, url="magnet:?xt=urn:btih:abc&dn=Movie"):
    return T.DownloadTask(url, str(tmp_path / "download.bin"))


def _drive(tmp_path, monkeypatch, states, task=None):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0)
    fake = _FakeDaemon(states)
    monkeypatch.setattr(aria2d, "DAEMON", fake)
    t = task or _task(tmp_path)
    torrent.TorrentDownloader(t)._run_rpc()
    return t, fake


def test_rpc_maps_progress_and_completes(tmp_path, monkeypatch):
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    states = [
        {"status": "active", "completedLength": "500", "totalLength": "1000",
         "connections": "7", "numSeeders": "3",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "connections": "0", "numSeeders": "0", "files": [{"path": payload}]},
    ]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.COMPLETED
    assert t.downloaded == t.total_size == 1000
    assert t.filename == "Movie.mkv"


def test_rpc_follows_magnet_metadata_to_the_payload(tmp_path, monkeypatch):
    """A magnet's first gid is the metadata download; the real payload arrives
    as followedBy and must not be mistaken for the file list."""
    payload = str(tmp_path / "Real.Name.mkv")
    open(payload, "w").close()
    states = [
        {"status": "active", "followedBy": ["gid2"],
         "files": [{"path": "[METADATA]abc"}]},
        {"status": "complete", "completedLength": "10", "totalLength": "10",
         "files": [{"path": payload}]},
    ]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.COMPLETED
    assert t.filename == "Real.Name.mkv"
    assert "[METADATA]" not in t.filename


def test_rpc_cold_swarm_stays_resumable(tmp_path, monkeypatch):
    """Same rule as the subprocess engine: never saw a peer or a payload means
    a cold swarm, not a failure."""
    states = [{"status": "error", "completedLength": "0", "totalLength": "0",
               "errorMessage": "no peers", "files": [{"path": "[METADATA]x"}]}]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.PAUSED
    assert "No peers found yet" in t.error


def test_rpc_real_failure_errors(tmp_path, monkeypatch):
    payload = str(tmp_path / "Half.mkv")
    open(payload, "w").close()
    states = [
        {"status": "active", "completedLength": "50", "totalLength": "100",
         "files": [{"path": payload}]},
        {"status": "error", "completedLength": "50", "totalLength": "100",
         "errorMessage": "disk full", "files": [{"path": payload}]},
    ]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.ERROR
    assert "disk full" in t.error


def test_rpc_pause_and_cancel(tmp_path, monkeypatch):
    t = _task(tmp_path)
    t.request_pause()
    t2, fake = _drive(tmp_path, monkeypatch, [{"status": "active"}], task=t)
    assert t2.status == T.PAUSED
    assert any(c[0] == "aria2.pause" for c in fake.calls)

    t = _task(tmp_path)
    t.request_cancel()
    t3, fake3 = _drive(tmp_path, monkeypatch, [{"status": "active"}], task=t)
    assert t3.status == T.CANCELLED
    assert any(c[0] == "aria2.forceRemove" for c in fake3.calls)


def test_rpc_daemon_loss_leaves_task_resumable(tmp_path, monkeypatch):
    """If the daemon dies mid-download the bytes are still on disk, so the task
    must come back as Paused rather than Error."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0)

    class _Dying(_FakeDaemon):
        def call(self, method, *params, **kw):
            if method == "aria2.tellStatus":
                raise OSError("daemon gone")
            return super().call(method, *params, **kw)

    monkeypatch.setattr(aria2d, "DAEMON", _Dying([]))
    t = _task(tmp_path)
    torrent.TorrentDownloader(t)._run_rpc()
    assert t.status == T.PAUSED
    assert "Resume" in t.error


def test_engine_falls_back_when_daemon_unavailable(tmp_path, monkeypatch):
    """A broken daemon must degrade to the legacy per-task subprocess engine,
    not fail the download."""
    monkeypatch.setattr(utils, "TORRENT_RPC", True)
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")

    class _Broken:
        def ensure(self):
            raise aria2d.Aria2Error("no daemon")

    monkeypatch.setattr(aria2d, "DAEMON", _Broken())

    used = {"subprocess": False}

    class _P:
        stdout = iter(())
        returncode = 0
        def poll(self): return 0
        def wait(self, timeout=None): return 0
        def terminate(self): pass
        def kill(self): pass

    def fake_popen(*a, **k):
        used["subprocess"] = True
        return _P()

    monkeypatch.setattr(torrent.subprocess, "Popen", fake_popen)
    t = _task(tmp_path)
    torrent.TorrentDownloader(t).run()
    assert used["subprocess"] is True          # legacy engine took over


def test_rpc_disabled_by_default():
    """The daemon engine ships off until it has proven itself on real work."""
    assert utils.TORRENT_RPC is False


# ------------------------------------------------- resilience of the RPC poll
class _FlakyDaemon(_FakeDaemon):
    """Fails the first `misses` tellStatus calls, then behaves normally."""

    def __init__(self, states, misses, error="timed out"):
        super().__init__(states)
        self.misses = misses
        self.error = error

    def call(self, method, *params, **kw):
        if method == "aria2.tellStatus" and self.misses > 0:
            self.misses -= 1
            raise aria2d.Aria2Error(self.error)
        return super().call(method, *params, **kw)


def _drive_with(tmp_path, monkeypatch, daemon, task=None):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0)
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    t = task or _task(tmp_path)
    torrent.TorrentDownloader(t)._run_rpc()
    return t


def test_a_busy_daemon_does_not_pause_a_healthy_download(tmp_path, monkeypatch):
    """A few missed polls mean the daemon is busy, not gone. Giving up on the
    first one paused downloads that were fine."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FlakyDaemon(
        [{"status": "complete", "completedLength": "1000", "totalLength": "1000",
          "files": [{"path": payload}]}],
        misses=torrent.RPC_RETRIES - 1)
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED


def test_a_daemon_that_stays_silent_still_pauses_the_task(tmp_path, monkeypatch):
    """Retrying must not become retrying forever — a genuinely dead engine has
    to surface, and resumably."""
    # the budget is a DURATION now, not a retry count — collapse it so the test
    # does not have to sit through the real grace period
    monkeypatch.setattr(torrent, "RPC_RETRY_GRACE", 0.0)
    daemon = _FlakyDaemon([], misses=torrent.RPC_RETRIES + 5)
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.PAUSED
    assert "stopped" in t.error


def test_a_lost_gid_reports_a_restart_not_a_failure(tmp_path, monkeypatch):
    """When the daemon is replaced, the old gid dies with it. The download
    itself is intact, so say the engine restarted rather than blaming it for
    stopping — and never fail the task, whose bytes are still on disk."""
    daemon = _FlakyDaemon([], misses=1, error="GID abc is not found")
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.PAUSED
    assert "restarted" in t.error


def test_a_missing_control_file_is_repaired_with_an_integrity_check(tmp_path, monkeypatch):
    """A hard-killed aria2 leaves payloads with no .aria2 control file, which
    aria2 then refuses outright — the download was dead for good. Re-adding it
    with check-integrity rebuilds the control file from the bytes on disk."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    ctl_err = ("File /d/Movie exists, but a control file(*.aria2) does not "
               "exist. Download was canceled in order to prevent your file "
               "from being truncated to 0.")
    daemon = _FakeDaemon([
        {"status": "error", "errorMessage": ctl_err, "completedLength": "500",
         "totalLength": "1000", "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    adds = [p for m, p in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert len(adds) == 2                                   # it retried
    assert adds[1][-1].get("check-integrity") == "true"     # ...with a recheck
    assert t.status == T.COMPLETED
    assert t.error == ""


def test_an_ordinary_torrent_error_is_not_retried(tmp_path, monkeypatch):
    """Only the control-file case is recoverable; everything else must still
    fail, or a broken torrent loops forever."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "error", "errorMessage": "tracker returned 410 Gone",
         "completedLength": "500", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    adds = [p for m, p in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert len(adds) == 1
    assert t.status == T.ERROR


# ------------------------------------------------------- per-file progress
def test_file_rows_normalises_aria2s_reply():
    """aria2 sends every number as a string and flags skipped files with
    selected:"false"."""
    rows = torrent._file_rows([
        {"index": "1", "path": "/d/T/a.mkv", "length": "1000",
         "completedLength": "250", "selected": "true"},
        {"index": "2", "path": "/d/T/b.srt", "length": "100",
         "completedLength": "0", "selected": "false"},
    ])
    assert rows[0] == {"index": 1, "path": "/d/T/a.mkv", "length": 1000,
                       "completed": 250, "selected": True}
    assert rows[1]["selected"] is False


def test_file_rows_drops_the_metadata_pseudo_file():
    """A magnet still fetching metadata reports one [METADATA] entry that is not
    part of the payload — showing it as a file would be a lie."""
    rows = torrent._file_rows([
        {"index": "1", "path": "[METADATA]abcdef", "length": "0",
         "completedLength": "0", "selected": "true"},
    ])
    assert rows == []


def test_file_rows_clamps_a_straddling_piece():
    """The piece spanning a file boundary can push completedLength past the
    file length; the UI must never render 103%."""
    rows = torrent._file_rows([
        {"index": "1", "path": "/d/a.bin", "length": "1000",
         "completedLength": "1030", "selected": "true"},
    ])
    assert rows[0]["completed"] == 1000


def test_file_rows_survives_junk():
    rows = torrent._file_rows([{"index": "x", "path": "/d/a", "length": "n/a"}])
    assert rows == []
    assert torrent._file_rows(None) == []


def test_poll_records_per_file_progress(tmp_path, monkeypatch):
    """The Files tab used to compute progress from the file's size on disk,
    which reads 100% instantly because aria2 preallocates. Only getFiles knows
    how much of each file is really present."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()

    class _WithFiles(_FakeDaemon):
        def call(self, method, *params, **kw):
            if method == "aria2.getFiles":
                self.calls.append((method, params))
                return [{"index": "1", "path": payload, "length": "1000",
                         "completedLength": "400", "selected": "true"}]
            return super().call(method, *params, **kw)

    daemon = _WithFiles([
        {"status": "active", "completedLength": "400", "totalLength": "1000",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    monkeypatch.setattr(torrent, "FILES_POLL", 0)      # no throttle in the test
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.file_progress[0]["completed"] == 400
    assert t.file_progress[0]["length"] == 1000


# ------------------------------------------------------------ exit behaviour
def test_exit_shutdown_never_hard_kills(tmp_path, monkeypatch):
    """The app's close path passes force=False on purpose: a hard kill stops
    aria2 flushing its .aria2 control files, and a payload without one cannot be
    resumed at all. A slow-closing daemon is still closing."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 4242, 6800, "s"
    monkeypatch.setattr(d, "_post", lambda *a, **k: {})
    monkeypatch.setattr(aria2d, "_pid_alive", lambda p: True)   # never exits
    killed = []
    monkeypatch.setattr(aria2d, "_kill", killed.append)
    monkeypatch.setattr(aria2d.time, "sleep", lambda *_: None)

    d.shutdown(wait=0.5, force=False)
    assert killed == []


def test_a_daemon_that_outlives_us_keeps_its_state_file(tmp_path, monkeypatch):
    """If we did not confirm it died, aria2d.json must survive — otherwise the
    next launch spawns a rival for the same BitTorrent port instead of finding
    the one already there."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    state = tmp_path / "aria2d.json"
    state.write_text(json.dumps({"pid": 4242, "port": 6800, "secret": "s"}),
                     encoding="utf-8")
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 4242, 6800, "s"
    monkeypatch.setattr(d, "_post", lambda *a, **k: {})
    monkeypatch.setattr(aria2d, "_pid_alive", lambda p: True)
    monkeypatch.setattr(aria2d.time, "sleep", lambda *_: None)

    d.shutdown(wait=0.5, force=False)
    assert state.is_file()


def test_a_daemon_that_exits_is_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    state = tmp_path / "aria2d.json"
    state.write_text(json.dumps({"pid": 4242, "port": 6800, "secret": "s"}),
                     encoding="utf-8")
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 4242, 6800, "s"
    sent = []
    monkeypatch.setattr(d, "_post",
                        lambda p, s, m, *a, **k: sent.append(m) or {})
    monkeypatch.setattr(aria2d, "_pid_alive", lambda p: False)   # exited

    d.shutdown(wait=0.5, force=False)
    assert sent == ["aria2.forceShutdown"]
    assert not state.exists()
    assert d.port == 0


def test_shutdown_does_not_probe_a_busy_daemon_first(tmp_path, monkeypatch):
    """shutdown() used to call alive() first, whose probe timeout is 10s — so
    closing the app could block on the very daemon it was trying to stop."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 4242, 6800, "s"
    calls = []
    monkeypatch.setattr(d, "_post",
                        lambda p, s, m, *a, **k: calls.append((m, k.get("timeout"))) or {})
    monkeypatch.setattr(aria2d, "_pid_alive", lambda p: False)

    d.shutdown(wait=0, force=False)
    assert all(m != "aria2.getGlobalStat" for m, _ in calls)
    assert all((tmo or 0) <= 3 for _, tmo in calls)


def test_exit_uses_force_shutdown_not_the_graceful_one(tmp_path, monkeypatch):
    """aria2.shutdown unregisters from every tracker first, and against dead
    public trackers it does not return — a daemon told to shut down was still
    running minutes later. forceShutdown skips the tracker round-trip but is
    still aria2's own exit, so control files are still flushed.

    saveSession must NOT be called: the daemon runs without --save-session, so
    it fails with "Filename is not given." — and when that failure aborted the
    rest of the sequence, forceShutdown was never sent and the daemon leaked."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 4242, 6800, "s"
    sent = []
    monkeypatch.setattr(d, "_post", lambda p, s, m, *a, **k: sent.append(m) or {})

    d.shutdown(wait=0, force=False)
    assert sent == ["aria2.forceShutdown"]


# ------------------------------------------------------------- concurrency
def test_daemon_queue_is_wider_than_the_users_limit(monkeypatch):
    """aria2 must never be the narrower of the two. It was a flat 12 while the
    queue spinbox went to 16, so asking for more than 12 quietly did nothing —
    the extras sat in aria2's queue looking stalled, unexplained anywhere."""
    monkeypatch.setattr(utils, "MAX_CONCURRENT_DOWNLOADS", 16, raising=False)
    assert aria2d.max_concurrent() > 16


def test_daemon_queue_has_a_floor(monkeypatch):
    """A queue of 1 still wants headroom: metadata fetches and the payload that
    follows them both occupy slots."""
    monkeypatch.setattr(utils, "MAX_CONCURRENT_DOWNLOADS", 1, raising=False)
    assert aria2d.max_concurrent() >= aria2d.MIN_CONCURRENT


def test_spawn_options_carry_the_current_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "MAX_CONCURRENT_DOWNLOADS", 9, raising=False)
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    opts = " ".join(d._options())
    assert f"--max-concurrent-downloads={aria2d.max_concurrent()}" in opts


def test_raising_the_limit_reaches_a_running_daemon(monkeypatch):
    """Changing the setting must not need an app restart."""
    monkeypatch.setattr(utils, "MAX_CONCURRENT_DOWNLOADS", 12, raising=False)
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    sent = []
    monkeypatch.setattr(d, "_post",
                        lambda p, s, m, params, **k: sent.append((m, params)) or {})
    d.apply_concurrency()
    method, params = sent[0]
    assert method == "aria2.changeGlobalOption"
    assert params[0]["max-concurrent-downloads"] == str(aria2d.max_concurrent())


# ------------------------------------------- re-attaching to a live daemon
class _RegisteredDaemon(_FakeDaemon):
    """Refuses a duplicate add the way aria2 does, and lists the existing one."""

    def __init__(self, states, infohash, gid="oldgid"):
        super().__init__(states)
        self.infohash = infohash
        self.gid = gid
        self.unpaused = []

    def call(self, method, *params, **kw):
        if method in ("aria2.addUri", "aria2.addTorrent"):
            self.calls.append((method, params))
            raise aria2d.Aria2Error(
                f"InfoHash {self.infohash} is already registered.")
        if method == "aria2.tellActive":
            return [{"gid": self.gid, "infoHash": self.infohash}]
        if method in ("aria2.tellWaiting", "aria2.tellStopped"):
            return []
        if method == "aria2.unpause":
            self.unpaused.append(params[0])
            return params[0]
        return super().call(method, *params, **kw)


def test_a_torrent_already_in_the_daemon_is_reattached(tmp_path, monkeypatch):
    """Pausing only calls aria2.pause, so the torrent stays registered; the
    daemon also outlives the app. Re-adding then failed with "InfoHash ... is
    already registered" and the download went to Error — the opposite of what
    Resume should do."""
    ih = "e6a095070aa5918e336f189b3e1d5f23103b7ff0"
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _RegisteredDaemon(
        [{"status": "complete", "completedLength": "1000", "totalLength": "1000",
          "files": [{"path": payload}]}], ih)
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}&dn=Movie",
                       str(tmp_path / "download.bin"))
    _drive_with(tmp_path, monkeypatch, daemon, task=t)
    assert daemon.unpaused == ["oldgid"], "did not re-attach to the existing gid"
    assert t.status == T.COMPLETED


def test_an_unrelated_add_failure_still_fails(tmp_path, monkeypatch):
    """Only the duplicate case is recoverable; a real error must surface."""
    class _Broken(_FakeDaemon):
        def call(self, method, *params, **kw):
            if method in ("aria2.addUri", "aria2.addTorrent"):
                raise aria2d.Aria2Error("Could not parse the magnet")
            return super().call(method, *params, **kw)

    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "d.bin"))
    with pytest.raises(aria2d.Aria2Error):
        monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(torrent, "POLL", 0)
        monkeypatch.setattr(aria2d, "DAEMON", _Broken([]))
        torrent.TorrentDownloader(t)._run_rpc()


def test_a_long_hash_check_is_not_mistaken_for_a_dead_daemon(tmp_path, monkeypatch):
    """Strikes alone were enough to kill: three missed 10s probes is 30s, and a
    hash check on a large torrent blocks RPC for far longer than that. Killing
    there is both wrong and destructive."""
    d, killed = _silent_daemon(tmp_path, monkeypatch)
    for _ in range(aria2d.DEAD_STRIKES + 5):
        with pytest.raises(aria2d.Aria2Error):
            d._attach()
    assert killed == [], "killed a daemon that was merely busy"


def test_a_hash_check_length_stall_does_not_pause_the_download(tmp_path, monkeypatch):
    """Force Recheck makes aria2 hash-check, which blocks its RPC thread for
    minutes. Ten retries at POLL=0.3s was about three seconds, so the recheck
    paused itself almost immediately and then errored."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FlakyDaemon(
        [{"status": "complete", "completedLength": "1000", "totalLength": "1000",
          "files": [{"path": payload}]}],
        misses=200)                       # far more than any retry COUNT
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED, f"gave up on a busy daemon: {t.error}"


class _DuplicateDaemon(_FakeDaemon):
    """aria2's REAL duplicate behaviour, verified against a live daemon.

    It does not reject the add. It accepts it, hands back a NEW gid, and that
    download then sits in status=error with "InfoHash ... is already
    registered". Modelling this as a raised exception — which the first version
    of this test did — meant the code was written against behaviour aria2 never
    exhibits, and the fix did nothing in practice.
    """

    def __init__(self, states, infohash, live_gid="livegid", hide_existing=False):
        super().__init__(states)
        self.infohash = infohash
        self.live_gid = live_gid
        self.hide_existing = hide_existing
        self.unpaused = []
        self.removed = []

    def call(self, method, *params, **kw):
        if method in ("aria2.addUri", "aria2.addTorrent"):
            self.calls.append((method, params))
            return "dupgid"
        if method == "aria2.tellActive":
            return [] if self.hide_existing else [
                {"gid": self.live_gid, "infoHash": self.infohash}]
        if method in ("aria2.tellWaiting", "aria2.tellStopped"):
            return []
        if method == "aria2.unpause":
            self.unpaused.append(params[0])
            return params[0]
        if method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
            self.removed.append(params[0])
            return params[0]
        return super().call(method, *params, **kw)


def test_a_duplicate_is_detected_before_adding(tmp_path, monkeypatch):
    """Checking first is the only way to avoid creating the dead entry at all."""
    ih = "d3aed7d116b132950acaf5ecbd0329544a204678"
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _DuplicateDaemon(
        [{"status": "complete", "completedLength": "1000", "totalLength": "1000",
          "files": [{"path": payload}]}], ih)
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}&dn=Movie",
                       str(tmp_path / "download.bin"))
    _drive_with(tmp_path, monkeypatch, daemon, task=t)

    adds = [m for m, _ in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert not adds, "added a duplicate instead of re-attaching"
    assert daemon.unpaused == ["livegid"]
    assert t.status == T.COMPLETED


def test_a_duplicate_that_slips_through_is_followed_not_failed(tmp_path, monkeypatch):
    """If the add happens anyway (a race), the resulting gid errors with
    "already registered" — the task must follow the real download instead of
    reporting failure."""
    ih = "d3aed7d116b132950acaf5ecbd0329544a204678"
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()

    class _Racy(_DuplicateDaemon):
        def __init__(self, states, infohash):
            super().__init__(states, infohash, hide_existing=True)
            self.seen = 0

        def call(self, method, *params, **kw):
            if method == "aria2.tellActive":
                # nothing to find at add time; the real one appears afterwards
                self.seen += 1
                return [] if self.seen <= 1 else [
                    {"gid": self.live_gid, "infoHash": self.infohash}]
            return super().call(method, *params, **kw)

    daemon = _Racy([
        {"status": "error",
         "errorMessage": f"InfoHash {ih} is already registered.",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ], ih)
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}&dn=Movie",
                       str(tmp_path / "download.bin"))
    _drive_with(tmp_path, monkeypatch, daemon, task=t)

    assert "dupgid" in daemon.removed, "left the dead duplicate in the daemon"
    assert t.status == T.COMPLETED, f"failed instead of following: {t.error}"


# ------------------------------------------------------------ orphan reaping
def test_only_our_own_aria2_binary_is_reaped(monkeypatch):
    """The user may have their own aria2 running for something else. Matching
    on the executable path is what keeps this from touching it."""
    ours = os.path.join("C:" + os.sep, "App", "bin", "aria2c.exe")
    monkeypatch.setattr(aria2d, "_aria2c_path", lambda: ours)
    monkeypatch.setattr(aria2d.sys, "platform", "win32")

    # raw strings: in a normal literal "\a" is BELL and "\b" is BACKSPACE, which
    # silently mangles every Windows path written into a test like this
    out = (r"111|C:\App\bin\aria2c.exe" "\n"
           r"222|C:\Program Files\aria2\aria2c.exe" "\n"
           r"333|c:\app\BIN\aria2c.exe" "\n")      # same path, different case

    class _R:
        stdout = out

    monkeypatch.setattr(aria2d.subprocess, "run", lambda *a, **k: _R())
    assert sorted(aria2d._our_aria2_pids()) == [111, 333]


def test_reaping_spares_the_daemon_we_are_using(monkeypatch):
    d = aria2d.Aria2Daemon()
    d.pid = 111
    monkeypatch.setattr(aria2d, "_our_aria2_pids", lambda: [111, 222, 333])
    killed = []
    monkeypatch.setattr(aria2d, "_kill", killed.append)
    d._reap_others()
    assert killed == [222, 333], "killed the daemon it was about to use"


def test_reaping_never_runs_on_the_hot_path(monkeypatch, tmp_path):
    """ensure() is called on EVERY rpc; listing processes there would shell out
    to PowerShell constantly."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d.port, d.secret, d.pid = 6800, "s", 111
    monkeypatch.setattr(d, "_post", lambda *a, **k: {"ok": 1})   # already alive
    calls = []
    monkeypatch.setattr(aria2d, "_our_aria2_pids", lambda: calls.append(1) or [])
    assert d.ensure() is True
    assert calls == []


def test_a_non_windows_host_reaps_nothing(monkeypatch):
    monkeypatch.setattr(aria2d.sys, "platform", "linux")
    monkeypatch.setattr(aria2d, "_aria2c_path", lambda: "/usr/bin/aria2c")
    assert aria2d._our_aria2_pids() == []


# ------------------------------------------- the metadata phase is not the payload
def test_metadata_progress_is_never_taken_for_the_payload(tmp_path, monkeypatch):
    """A magnet starts as a download of the .torrent itself, reported as a
    [METADATA] pseudo-file whose completedLength == totalLength (a few KB) while
    the status is still "active". Reading that as progress marked torrents
    COMPLETE that had downloaded nothing — and because the seeding branch
    continues, the code that finds the real payload never ran again."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        # metadata fetched: sizes equal, status active, no followedBy yet
        {"status": "active", "completedLength": "31000", "totalLength": "31000",
         "files": [{"path": "[METADATA]d3aed7d1"}]},
        {"status": "active", "completedLength": "31000", "totalLength": "31000",
         "files": [{"path": "[METADATA]d3aed7d1"}]},
        # payload appears and is genuinely incomplete
        {"status": "active", "completedLength": "0", "totalLength": "7000000000",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "7000000000",
         "totalLength": "7000000000", "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.total_size == 7000000000, "took the metadata's size as the payload's"
    assert t.status == T.COMPLETED


def test_a_metadata_download_reporting_complete_is_not_the_torrent(tmp_path, monkeypatch):
    """followedBy usually arrives in the same reply, but if it lags a poll the
    payload must not be declared done."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "31000", "totalLength": "31000",
         "files": [{"path": "[METADATA]abc"}]},
        {"status": "active", "completedLength": "10", "totalLength": "1000",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.total_size == 1000
    assert t.status == T.COMPLETED


def test_seeding_is_not_declared_during_the_metadata_phase(tmp_path, monkeypatch):
    """The exact false positive seen in the wild: 'seeding: <name>' logged for a
    torrent that had downloaded nothing."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": "31000", "totalLength": "31000",
         "files": [{"path": "[METADATA]abc"}]},
        {"status": "active", "completedLength": "0", "totalLength": "5000",
         "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "5000", "totalLength": "5000",
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED
    assert t.downloaded == 5000


def test_a_genuinely_complete_payload_still_seeds(tmp_path, monkeypatch):
    """The guard must not break real seeding detection."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}], "uploadSpeed": "5000"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED
