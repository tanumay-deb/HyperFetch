"""Deleting every download must stay deleted across a restart.

The reported bug: delete the whole list, reopen the app, and the same list is
back. save_json rotates the current file to <path>.bak *before* writing, so the
save that empties downloads.json puts the full list into downloads.json.bak.
The loader then read "[]" as a failed read and restored that backup — handing
back exactly what the user had just deleted, on every launch.
"""
import json

import pytest

pytest.importorskip("PySide6")

import utils                                              # noqa: E402
from gui2.app import DownloadAppV2                        # noqa: E402


def _state_file(tmp_path, monkeypatch, rows, bak=None):
    """Point the app-data dir at tmp_path and lay down a downloads.json (+bak)."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    p = tmp_path / "downloads.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    if bak is not None:
        (tmp_path / "downloads.json.bak").write_text(json.dumps(bak),
                                                     encoding="utf-8")
    return p


def _task_row(tid, name):
    return {"id": tid, "url": f"http://x/{name}", "save_path": f"C:/dl/{name}",
            "filename": name, "status": "Completed", "total_size": 10,
            "downloaded": 10}


def _load(app_cls, tmp_path, monkeypatch, rows, bak=None):
    """Run just the state-load step and report what it loaded."""
    _state_file(tmp_path, monkeypatch, rows, bak)
    app = app_cls.__new__(app_cls)                 # no GUI construction
    app._state_path = str(tmp_path / "downloads.json")

    # A real QueueManager rather than a fake: restore() lives there now, and a
    # stub would just be a second copy of its contract that could drift. What
    # is under test here is the loader's retry-and-backup logic, not the queue.
    from queue_manager import QueueManager
    app.queue = QueueManager()
    app._load_state()
    return list(app.queue.tasks)


def test_deleting_everything_survives_a_restart(tmp_path, monkeypatch):
    """The exact reported sequence: empty list on disk, full list in the .bak."""
    old = [_task_row(1, "a.mkv"), _task_row(2, "b.mkv"), _task_row(3, "c.mkv")]
    loaded = _load(DownloadAppV2, tmp_path, monkeypatch, rows=[], bak=old)
    assert loaded == [], (
        f"restored {len(loaded)} deleted download(s) from the backup — "
        "an empty list is a user action, not a failed read")


def test_a_real_list_still_loads(tmp_path, monkeypatch):
    rows = [_task_row(1, "a.mkv"), _task_row(2, "b.mkv")]
    loaded = _load(DownloadAppV2, tmp_path, monkeypatch, rows=rows)
    assert len(loaded) == 2


def test_corrupt_file_still_recovers_from_the_backup(tmp_path, monkeypatch):
    """The backup path must keep working — that is what it is for."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "downloads.json").write_text("{ this is not json",
                                             encoding="utf-8")
    (tmp_path / "downloads.json.bak").write_text(
        json.dumps([_task_row(1, "a.mkv")]), encoding="utf-8")

    app = DownloadAppV2.__new__(DownloadAppV2)
    app._state_path = str(tmp_path / "downloads.json")
    from queue_manager import QueueManager
    app.queue = QueueManager()
    app._load_state()
    loaded = list(app.queue.tasks)
    assert len(loaded) == 1, "a genuinely corrupt file must still use the .bak"


def test_missing_file_is_a_fresh_install_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    app = DownloadAppV2.__new__(DownloadAppV2)
    app._state_path = str(tmp_path / "downloads.json")
    from queue_manager import QueueManager
    app.queue = QueueManager()
    app._load_state()
    loaded = list(app.queue.tasks)
    assert loaded == []
    assert not getattr(app, "_state_load_failed", False), \
        "a first run must not disable saving for the session"
