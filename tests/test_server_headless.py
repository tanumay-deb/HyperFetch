"""The headless server, and the logic that had to leave the GUI to make it work.

Three things a server needs lived in `gui2/app.py` and so only ran while a
window was open: starting the site, resuming downloads after a restart, and
retention. A Qt-less build would have come back with everything paused and
nothing ever expiring, silently. These tests are mostly about that.
"""
import os
import sys
import time

import pytest

import task as T
import utils
from queue_manager import QueueManager


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


def _row(name, status, owner="", **kw):
    t = T.DownloadTask("https://e.test/" + name, "C:/dl/" + name, filename=name)
    t.status = status
    t.owner = owner
    for k, v in kw.items():
        setattr(t, k, v)
    return t.to_dict()


# ---- restore ---------------------------------------------------------------
def test_restore_brings_saved_downloads_back():
    q = QueueManager()
    restored, skipped = q.restore([_row("a.bin", T.PAUSED), _row("b.bin", T.COMPLETED)])
    assert (restored, skipped) == (2, 0)
    assert {t.filename for t in q.tasks} == {"a.bin", "b.bin"}


def test_an_in_flight_download_resumes_by_itself():
    """This is what only happened with a window open. A server that comes back
    with everything paused and nobody watching is not a server."""
    q = QueueManager()
    q.restore([_row("busy.bin", T.DOWNLOADING)])
    t = q.tasks[0]
    assert getattr(t, "_auto_resume", False) is True
    assert t.status != T.PAUSED, "it came back paused with nothing to un-pause it"


def test_a_site_user_s_download_waits_for_them():
    """Decision B6: this machine should not silently resume work on behalf of
    somebody who is not here. They restart it from the page."""
    q = QueueManager()
    q.restore([_row("theirs.bin", T.DOWNLOADING, owner="tanumay")])
    t = q.tasks[0]
    assert t.status == T.PAUSED
    assert getattr(t, "_auto_resume", False) is True, "the flag itself should survive"


def test_a_damaged_row_is_skipped_rather_than_losing_the_rest():
    q = QueueManager()
    restored, skipped = q.restore([
        _row("good.bin", T.PAUSED),
        {"nonsense": True},
        _row("also-good.bin", T.PAUSED),
    ])
    assert restored == 2
    assert skipped == 1
    assert len(q.tasks) == 2


def test_restoring_nothing_is_not_an_error():
    q = QueueManager()
    assert q.restore([]) == (0, 0)
    assert q.restore(None) == (0, 0)


# ---- housekeeping ----------------------------------------------------------
def test_retention_runs_without_a_qt_event_loop(tmp_path, monkeypatch):
    """A QTimer only ticks while Qt is running, which made retention a promise
    only the desktop app could keep."""
    calls = []
    import site_limits
    monkeypatch.setattr(site_limits, "sweep",
                        lambda q, d, **kw: calls.append(d) or [])

    q = QueueManager()
    q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01)
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    assert calls, "the sweep never ran"
    assert calls[0] == str(tmp_path)


def test_a_failing_sweep_does_not_kill_the_thread(tmp_path, monkeypatch):
    """Retention runs forever unattended; one bad night must not stop it."""
    calls = []
    import site_limits

    def boom(q, d, **kw):
        calls.append(d)
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(site_limits, "sweep", boom)

    q = QueueManager()
    q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01)
    deadline = time.time() + 3
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(calls) >= 2, "the thread died on the first failure"


def test_the_first_sweep_does_not_wait_a_whole_day(tmp_path, monkeypatch):
    """A machine that is only on for an hour a day would otherwise never reach
    it."""
    import inspect
    src = inspect.getsource(QueueManager.start_housekeeping)
    assert "delay=30" in src, "there is no short first run"


# ---- the entry point -------------------------------------------------------
def test_the_check_flag_reports_and_exits_clean(capsys):
    import server
    assert server.main(["--check"]) == 0
    out = capsys.readouterr().out
    for expected in ("save dir", "users site", "control port", "server check OK"):
        assert expected in out, expected


def test_the_check_proves_the_extension_routes_are_absent():
    """The whole point of the split. If this ever passes silently on a merged
    app, the server has become reachable in ways it should not be."""
    import server
    assert server.main(["--check"]) == 0


def test_it_builds_a_queue_that_already_has_the_saved_downloads(tmp_path):
    import server
    utils.save_json(os.path.join(str(tmp_path), "downloads.json"),
                    [_row("saved.bin", T.PAUSED)])
    q, save_dir = server.build()
    assert [t.filename for t in q.tasks] == ["saved.bin"]
    assert save_dir


def test_nothing_here_imports_qt():
    """The reason a server build is a third smaller. If Qt creeps back in, the
    spec that excludes it starts failing at runtime rather than at build time.
    """
    import server
    import queue_manager
    for mod in (server, queue_manager):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "PySide6" not in src, mod.__name__
        assert "QtCore" not in src, mod.__name__


def test_the_server_never_starts_a_tunnel():
    """Publishing a machine to the internet should be a command somebody types,
    not something a service decides on their behalf."""
    import server
    src = open(server.__file__, encoding="utf-8").read()
    assert "funnel" not in src.replace("never starts a tunnel", "")
    assert "site_tunnel" not in src


@pytest.mark.skipif(sys.platform != "win32", reason="path shape differs")
def test_it_reads_the_same_settings_as_the_desktop_app(tmp_path):
    """A machine that has run both should not need its download folder
    configured twice."""
    import server
    target = tmp_path / "elsewhere"
    target.mkdir()
    utils.save_json(os.path.join(str(tmp_path), "settings.json"),
                    {"save_dir": str(target), "max_concurrent": 5})
    settings, save_dir = server._load_settings()
    assert save_dir == str(target)
    assert settings["max_concurrent"] == 5


def test_a_missing_download_folder_falls_back_rather_than_crashing(tmp_path):
    import server
    utils.save_json(os.path.join(str(tmp_path), "settings.json"),
                    {"save_dir": r"Z:\gone\missing"})
    _settings, save_dir = server._load_settings()
    assert os.path.isdir(save_dir)
