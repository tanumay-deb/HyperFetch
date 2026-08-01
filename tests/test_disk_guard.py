"""Free-space guard for torrents.

HTTP downloads already refused to start a file the volume could not hold;
torrents did not. A torrent bigger than the free space preallocated, ran for
hours and then died on ENOSPC with nothing to show for it.
"""
import pytest

import aria2d
import task as T
import torrent
import utils
from test_aria2d import _FakeDaemon, _drive_with


def test_shortfall_is_zero_when_it_fits(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 100 << 30})())
    assert utils.disk_shortfall(str(tmp_path), 1 << 30) == 0


def test_shortfall_accounts_for_the_slack(tmp_path, monkeypatch):
    """Filling a volume to its last byte breaks more than the download."""
    import shutil
    need = 10 << 30
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: type("U", (), {"free": need})())
    assert utils.disk_shortfall(str(tmp_path), need) == utils.DISK_SLACK


def test_an_unreadable_volume_does_not_block_the_download(tmp_path, monkeypatch):
    """If we cannot tell, let the write find out — a guess must not stop a
    download that would have worked."""
    import shutil

    def boom(p):
        raise OSError("no such volume")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    assert utils.disk_shortfall(str(tmp_path), 1 << 40) == 0


def test_a_torrent_too_big_for_the_drive_fails_with_a_clear_reason(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 1 << 20})())   # 1 MiB
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": "0", "totalLength": str(50 << 30),
         "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.ERROR
    assert "disk space" in t.error.lower()
    assert "Resume" in t.error                      # tells them how to recover
    # and it stopped aria2 rather than letting it grind on
    assert any(m == "aria2.forceRemove" for m, _ in daemon.calls)


def test_only_the_remaining_bytes_must_fit(tmp_path, monkeypatch):
    """A resumed torrent has already paid for what is on disk — charging it the
    full size again would refuse downloads that fit perfectly well."""
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: type("U", (), {"free": (2 << 30)})())
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": str(9 << 30),
         "totalLength": str(10 << 30), "files": [{"path": payload}]},
        {"status": "complete", "completedLength": str(10 << 30),
         "totalLength": str(10 << 30), "files": [{"path": payload}]},
    ])
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED, f"refused a torrent that fits: {t.error}"


def test_the_check_runs_once_not_every_poll(tmp_path, monkeypatch):
    import shutil
    calls = {"n": 0}

    def counting(p):
        calls["n"] += 1
        return type("U", (), {"free": 100 << 30})()

    monkeypatch.setattr(shutil, "disk_usage", counting)
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    states = [{"status": "active", "completedLength": str(i * 100),
               "totalLength": "1000", "files": [{"path": payload}]}
              for i in range(1, 6)]
    states.append({"status": "complete", "completedLength": "1000",
                   "totalLength": "1000", "files": [{"path": payload}]})
    _drive_with(tmp_path, monkeypatch, _FakeDaemon(states))
    assert calls["n"] == 1
