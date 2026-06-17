"""Updater: pure logic + cache + offline fallback. The actual GitHub API call
is mocked so this stays hermetic."""
import json
import time

import pytest

import updater


def test_parse_semver_strips_v_and_meta():
    assert updater._parse_semver("v1.2.3") == (1, 2, 3)
    assert updater._parse_semver("1.2") == (1, 2, 0)
    assert updater._parse_semver("v1.2.3-rc1") == (1, 2, 3)
    assert updater._parse_semver("garbage") == ()
    assert updater._parse_semver("") == ()
    assert updater._parse_semver(None) == ()


def test_newer_compares_semver_not_string():
    assert updater._newer("1.10.0", "1.2.0")          # 10 > 2, NOT lexicographic
    assert updater._newer("v2.0.0", "1.99.0")
    assert not updater._newer("1.2.0", "1.2.0")
    assert not updater._newer("1.1.0", "1.2.0")
    assert not updater._newer("garbage", "1.0.0")


def test_check_uses_cache_under_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.utils, "app_data_dir", lambda: str(tmp_path))
    # seed a fresh cache
    cache = {"checked_at": time.time(), "tag": "v2.0.0",
             "url": "https://example.com/release"}
    with open(updater._cache_path(), "w", encoding="utf-8") as f:
        json.dump(cache, f)

    def fail_fetch(_repo):
        raise AssertionError("should NOT hit the network when cache is fresh")

    monkeypatch.setattr(updater, "_fetch_latest", fail_fetch)
    info = updater.check_for_update("1.0.0")
    assert info == {"available": True, "version": "v2.0.0",
                    "url": "https://example.com/release"}


def test_force_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.utils, "app_data_dir", lambda: str(tmp_path))
    with open(updater._cache_path(), "w", encoding="utf-8") as f:
        json.dump({"checked_at": time.time(), "tag": "v1.0.0", "url": "x"}, f)
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda _: {"tag": "v3.0.0", "url": "https://fresh"})
    info = updater.check_for_update("1.0.0", force=True)
    assert info["version"] == "v3.0.0"
    assert info["available"]


def test_check_returns_none_on_network_failure(tmp_path, monkeypatch):
    """Offline / API down -> None, NOT a crash. UI shows 'offline or unavailable'."""
    monkeypatch.setattr(updater.utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater, "_fetch_latest", lambda _: None)
    assert updater.check_for_update("1.0.0", force=True) is None


def test_stale_cache_triggers_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.utils, "app_data_dir", lambda: str(tmp_path))
    # cache older than TTL
    with open(updater._cache_path(), "w", encoding="utf-8") as f:
        json.dump({"checked_at": time.time() - updater.CACHE_TTL - 10,
                   "tag": "v1.0.0", "url": "x"}, f)
    called = []
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda r: (called.append(r), {"tag": "v1.5.0", "url": "y"})[1])
    info = updater.check_for_update("1.0.0")
    assert called, "stale cache should have triggered a refetch"
    assert info["version"] == "v1.5.0"
