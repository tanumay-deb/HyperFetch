"""Seeding, preview priority, and a graceful stop for the legacy engine."""
import subprocess
import sys

import pytest

import aria2d
import torrent
import utils


def _opts(monkeypatch, **settings):
    for k, v in settings.items():
        monkeypatch.setattr(utils, k, v, raising=False)
    return torrent.preference_opts()


def test_seeding_is_off_by_default(monkeypatch):
    """The app has always stopped dead on completion; that stays the default."""
    assert "--seed-time=0" in _opts(monkeypatch, SEED_ENABLED=False)


def test_a_share_ratio_is_passed_when_seeding_is_on(monkeypatch):
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=2.0, SEED_MINUTES=0)
    assert "--seed-ratio=2" in o
    assert "--seed-time=0" not in o          # would defeat the whole thing


def test_a_time_limit_can_be_combined_with_the_ratio(monkeypatch):
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=1.5, SEED_MINUTES=30)
    assert "--seed-ratio=1.5" in o and "--seed-time=30" in o


def test_seeding_with_no_limits_does_not_become_seed_forever(monkeypatch):
    """aria2 reads --seed-ratio=0 as 'seed indefinitely'. Clearing both boxes
    must not silently opt the user into that."""
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=0, SEED_MINUTES=0)
    assert "--seed-ratio=1.0" in o


def test_preview_priority_is_opt_in(monkeypatch):
    assert not any("prioritize-piece" in x
                   for x in _opts(monkeypatch, TORRENT_PREVIEW=False))
    assert "--bt-prioritize-piece=head,tail" in _opts(
        monkeypatch, TORRENT_PREVIEW=True)


def test_both_engines_get_the_same_preferences(monkeypatch):
    """A setting that works on one engine and silently does nothing on the
    other is worse than not having it."""
    monkeypatch.setattr(utils, "SEED_ENABLED", True, raising=False)
    monkeypatch.setattr(utils, "SEED_RATIO", 2.5, raising=False)
    monkeypatch.setattr(utils, "TORRENT_PREVIEW", True, raising=False)

    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    daemon_opts = d._options()

    t = torrent.TorrentDownloader.__new__(torrent.TorrentDownloader)
    t.t = type("T", (), {"url": "magnet:?xt=urn:btih:abc"})()
    legacy = t._build_cmd("aria2c.exe", ".")

    for flag in ("--seed-ratio=2.5", "--bt-prioritize-piece=head,tail"):
        assert flag in daemon_opts, f"daemon missing {flag}"
        assert flag in legacy, f"legacy engine missing {flag}"


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK is Windows-only")
def test_legacy_stop_asks_before_it_forces(monkeypatch):
    """TerminateProcess gives aria2 no chance to write its DHT routing table —
    measured: after a terminate() dht.dat was gone, after a CTRL_BREAK it was
    written. So ask first, and only force a process that ignores the request."""
    import signal as _signal
    calls = []

    class P:
        def send_signal(self, sig):
            calls.append(("signal", sig))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

        def terminate(self):
            calls.append(("terminate", None))

        def kill(self):
            calls.append(("kill", None))

    td = torrent.TorrentDownloader.__new__(torrent.TorrentDownloader)
    td._proc = P()
    td._stop()
    assert calls[0] == ("signal", _signal.CTRL_BREAK_EVENT)
    assert not any(c[0] in ("terminate", "kill") for c in calls)


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK is Windows-only")
def test_a_process_that_ignores_the_break_is_still_forced(monkeypatch):
    calls = []

    class P:
        def __init__(self):
            self.n = 0

        def send_signal(self, sig):
            calls.append("signal")

        def wait(self, timeout=None):
            self.n += 1
            if self.n == 1:
                raise subprocess.TimeoutExpired("aria2c", timeout)
            return 0

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

    td = torrent.TorrentDownloader.__new__(torrent.TorrentDownloader)
    td._proc = P()
    td._stop()
    assert calls == ["signal", "terminate"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process groups")
def test_aria2_runs_in_its_own_process_group():
    """Without this the CTRL_BREAK would also hit HyperFetch itself."""
    assert hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
    src = (torrent.__file__)
    with open(src, encoding="utf-8") as f:
        text = f.read()
    assert "CREATE_NEW_PROCESS_GROUP" in text


def test_settings_reach_the_engine(tmp_path, monkeypatch):
    """End to end: what the dialog saves must turn into aria2 flags. A setting
    that persists but never reaches the engine looks like it works."""
    import types
    import gui2.app_settings as S

    stub = types.SimpleNamespace(
        _extras={"torrent_preview": True, "seed_enabled": True,
                 "seed_ratio": 2.5, "seed_minutes": 45},
        _settings_path=str(tmp_path / "settings.json"))
    S.SettingsMixin._apply_network_settings(stub)

    assert utils.TORRENT_PREVIEW is True
    assert utils.SEED_ENABLED is True
    o = torrent.preference_opts()
    assert "--seed-ratio=2.5" in o
    assert "--seed-time=45" in o
    assert "--bt-prioritize-piece=head,tail" in o


def test_defaults_keep_the_old_behaviour(tmp_path, monkeypatch):
    """An existing install with no new keys must not suddenly start seeding."""
    import types
    import gui2.app_settings as S

    stub = types.SimpleNamespace(_extras={}, _settings_path=str(tmp_path / "s.json"))
    S.SettingsMixin._apply_network_settings(stub)
    assert utils.SEED_ENABLED is False
    assert "--seed-time=0" in torrent.preference_opts()


def test_peer_recruitment_threshold_is_raised(monkeypatch):
    """aria2 only hunts for MORE peers while total speed is under
    bt-request-peer-speed-limit. Its 50 KiB/s default means it stops looking
    almost immediately on a modern line and plateaus on the peers it has."""
    o = torrent.preference_opts()
    assert f"--bt-request-peer-speed-limit={torrent.PEER_SPEED_TARGET}" in o
    assert torrent.PEER_SPEED_TARGET != "50K"


def test_it_is_a_threshold_not_a_download_cap(monkeypatch):
    """Nothing here may ever cap download speed — that is the one thing this
    option must not be confused with."""
    o = torrent.preference_opts()
    assert not any("max-overall-download-limit" in x for x in o)
    assert not any("--max-download-limit" in x for x in o)
