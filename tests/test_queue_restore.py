"""Restoring the queue, the retention hook, and the seam to the users site.

Engine and desktop concerns, so they stay in the public repository. What is
pinned here is that the desktop never reaches the users site: the retention
sweep used to import site_limits from inside this queue, and PyInstaller
followed that import into the shipped binary.
"""

import os
import sys
import threading
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


def test_retention_runs_without_a_qt_event_loop(tmp_path, monkeypatch):
    """A QTimer only ticks while Qt is running, which made retention a promise
    only the desktop app could keep."""
    calls = []
    q = QueueManager()
    q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01,
                         sweep=lambda qq, d, **kw: calls.append(d) or [])
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    assert calls, "the sweep never ran"
    assert calls[0] == str(tmp_path)
    q.shutdown()                      # or it sweeps every 50ms for the rest of the run


def test_a_failing_sweep_does_not_kill_the_thread(tmp_path, monkeypatch):
    """Retention runs forever unattended; one bad night must not stop it."""
    calls = []

    def boom(qq, d, **kw):
        calls.append(d)
        raise RuntimeError("disk on fire")

    q = QueueManager()
    q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01, sweep=boom)
    deadline = time.time() + 3
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(calls) >= 2, "the thread died on the first failure"
    q.shutdown()


def test_the_first_sweep_does_not_wait_a_whole_day(tmp_path, monkeypatch):
    """A machine that is only on for an hour a day would otherwise never reach
    it."""
    import inspect
    src = inspect.getsource(QueueManager.start_housekeeping)
    assert "delay=30" in src, "there is no short first run"


def test_the_desktop_no_longer_offers_the_users_site():
    """A download manager is not a hosting service. Mixing the two is how
    somebody publishes a machine they did not mean to."""
    import inspect
    from gui2.dialogs import settings, settings_pages
    assert "Users Site" not in settings._SECTIONS
    assert not hasattr(settings_pages.PageBuilderMixin, "_p_users")
    src = inspect.getsource(settings_pages)
    assert "site_auth" not in src, "the desktop settings still reach the site store"


def test_the_desktop_does_not_start_the_site():
    import inspect
    from gui2 import app
    src = inspect.getsource(app)
    assert "run_site_server" not in src
    assert "site_server" not in src


def test_the_renamed_section_is_there():
    from gui2.dialogs import settings
    assert "Browser Access" in settings._SECTIONS
    assert len(settings._SECTIONS) == 8


def test_housekeeping_without_a_sweeper_starts_no_thread(tmp_path):
    """The desktop has no site accounts and nothing to retain. It used to start
    a thread anyway, which woke every 24 hours to sweep nothing."""
    # By name, not by count: the tests above leave their own retention threads
    # running on a 50ms interval, so a count is really measuring the rest of
    # the suite. What this needs to know is that no retention thread appeared.
    def retention_threads():
        return {t.ident for t in threading.enumerate()
                if t.name == "hyperfetch-retention"}

    before = retention_threads()
    q = QueueManager()
    t = q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01)
    assert t is None, "a retention thread was started with nothing to sweep"
    time.sleep(0.15)
    assert retention_threads() == before, "a retention thread started anyway"


def test_the_desktop_window_does_not_start_retention():
    """Retention belongs to the server build. The desktop calling it is what
    dragged site_limits into the frozen desktop app."""
    import io as _io, os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = _io.open(_os.path.join(root, "gui2", "app.py"), encoding="utf-8").read()
    assert "start_housekeeping" not in src, (
        "gui2/app.py still starts the retention sweep")
