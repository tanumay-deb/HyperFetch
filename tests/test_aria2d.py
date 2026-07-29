"""Shared aria2 daemon + the RPC torrent engine.

No real aria2 process and no network: the daemon's RPC transport is stubbed so
the lifecycle rules and the status mapping can be asserted directly.
"""
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


def test_unreachable_but_live_daemon_is_killed(tmp_path, monkeypatch):
    """The orphan case proven in the spike: aria2 outlives its parent. One that
    is alive but no longer answering must be killed, not left holding ports."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "aria2d.json").write_text(json.dumps(
        {"pid": 9999, "port": 6800, "secret": "s"}), encoding="utf-8")

    d = aria2d.Aria2Daemon()
    def dead(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(d, "_post", dead)
    monkeypatch.setattr(aria2d, "_pid_alive", lambda pid: pid == 9999)
    killed = []
    monkeypatch.setattr(aria2d, "_kill", killed.append)

    assert d._attach() is False
    assert killed == [9999]


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
