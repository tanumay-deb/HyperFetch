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


def test_settings_reach_the_daemon(monkeypatch):
    """Every torrent setting has to turn into an aria2 flag. One that
    persists but never reaches the engine looks like it works.

    (Was a parity check across two engines; the per-torrent-process engine
    has been removed, so only the daemon is left to assert.)
    """
    monkeypatch.setattr(utils, "SEED_ENABLED", True, raising=False)
    monkeypatch.setattr(utils, "SEED_RATIO", 2.5, raising=False)
    monkeypatch.setattr(utils, "TORRENT_PREVIEW", True, raising=False)

    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    opts = d._options()
    for flag in ("--seed-ratio=2.5", "--bt-prioritize-piece=head,tail"):
        assert flag in opts, f"daemon missing {flag}"

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


def test_preallocation_uses_falloc_not_prealloc(monkeypatch):
    """prealloc WRITES ZERO BYTES across the whole file, and aria2 is single
    threaded — measured on a live daemon, RPC that normally answers in 1-60ms
    took up to 9.5s and the download speed collapsed for the duration. That is
    the "10 Mb/s then 0 then it climbs back" sawtooth. falloc asks the
    filesystem to reserve the space, which on NTFS is near-instant."""
    monkeypatch.setattr(utils, "PREALLOCATE", True, raising=False)
    assert torrent.allocation_opt() == "--file-allocation=falloc"


def test_preallocation_off_allocates_nothing(monkeypatch):
    monkeypatch.setattr(utils, "PREALLOCATE", False, raising=False)
    assert torrent.allocation_opt() == "--file-allocation=none"


def test_the_daemon_uses_the_allocation_setting(monkeypatch):
    monkeypatch.setattr(utils, "PREALLOCATE", True, raising=False)
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    assert torrent.allocation_opt() in d._options()

