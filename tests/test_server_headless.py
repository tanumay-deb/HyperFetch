"""The headless server, and the logic that had to leave the GUI to make it work.

Three things a server needs lived in `gui2/app.py` and so only ran while a
window was open: starting the site, resuming downloads after a restart, and
retention. A Qt-less build would have come back with everything paused and
nothing ever expiring, silently. These tests are mostly about that.
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
    q = QueueManager()
    q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01,
                         sweep=lambda qq, d, **kw: calls.append(d) or [])
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    assert calls, "the sweep never ran"
    assert calls[0] == str(tmp_path)


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


# ---- account management, which has nowhere else to live --------------------
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


def test_accounts_can_be_managed_from_the_command_line(capsys):
    """The panel is gone, so this is the only way. If it broke, a server would
    be unmanageable."""
    import server
    import site_auth

    assert server.main(["users"]) == 0
    assert "no accounts" in capsys.readouterr().out

    site_auth.create_user_as_admin("tanumay", "t@e.test", "correct horse battery")
    assert server.main(["users"]) == 0
    out = capsys.readouterr().out
    assert "tanumay" in out and "active" in out

    assert server.main(["users", "disable", "tanumay"]) == 0
    assert site_auth.find_user("tanumay")["status"] == site_auth.STATUS_DISABLED
    assert server.main(["users", "enable", "tanumay"]) == 0
    assert site_auth.find_user("tanumay")["status"] == site_auth.STATUS_ACTIVE


def test_removing_an_account_says_the_files_stay(capsys):
    """The opposite of what a delete usually means, and the files are the
    expensive part."""
    import server
    import site_auth
    site_auth.create_user_as_admin("tanumay", "", "correct horse battery")
    assert server.main(["users", "remove", "tanumay"]) == 0
    assert "still on disk" in capsys.readouterr().out
    assert site_auth.find_user("tanumay") is None


def test_the_site_switch_and_invite_code_are_reachable(capsys):
    import server
    import site_auth
    assert server.main(["site", "on"]) == 0
    assert site_auth.is_enabled() is True
    assert "no accounts yet" in capsys.readouterr().out

    assert server.main(["invite"]) == 0
    first = capsys.readouterr().out
    assert site_auth.invite_code() in first
    assert "never expires" in first

    assert server.main(["invite", "--new", "--days", "7"]) == 0
    assert "expires in" in capsys.readouterr().out

    assert server.main(["site", "off"]) == 0
    assert site_auth.is_enabled() is False


def test_a_password_is_never_taken_as_an_argument():
    """It would end up in shell history and in the process list."""
    import inspect
    import server
    src = inspect.getsource(server._admin)
    assert "getpass" in src
    for flag in ("--password", "-p "):
        assert flag not in src, flag


def test_an_unknown_command_is_refused_rather_than_starting_a_server(capsys):
    import server
    # The verb is checked before the name, so a typo says what is actually
    # wrong rather than complaining about an account nobody meant to name.
    assert server.main(["users", "frobnicate", "x"]) == 2
    assert "frobnicate" in capsys.readouterr().err


# ---- the seam between the free app and the paid server ---------------------
# These exist so the users-site can be lifted out into its own repository. The
# desktop must not reach it, and until now it did: queue_manager imported
# site_limits inside the retention loop, PyInstaller followed that import, and
# the shipped desktop binary carried the whole users-site with it.
def test_the_queue_does_not_reach_into_the_users_site():
    """The one edge that coupled the two halves. An import anywhere in here —
    including inside a function, which is how the last one hid — puts the site
    modules back in the desktop build."""
    import ast, io as _io, os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = _io.open(_os.path.join(root, "queue_manager.py"), encoding="utf-8").read()
    found = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith("site_")]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("site_"):
                found.append(node.module)
    assert not found, "queue_manager imports %s — the desktop build will ship it" % found


def test_housekeeping_without_a_sweeper_starts_no_thread(tmp_path):
    """The desktop has no site accounts and nothing to retain. It used to start
    a thread anyway, which woke every 24 hours to sweep nothing."""
    before = threading.active_count()
    q = QueueManager()
    t = q.start_housekeeping(str(tmp_path), interval=0.05, delay=0.01)
    assert t is None, "a retention thread was started with nothing to sweep"
    time.sleep(0.15)
    assert threading.active_count() <= before + 1, "a thread was started anyway"


def test_the_desktop_window_does_not_start_retention():
    """Retention belongs to the server build. The desktop calling it is what
    dragged site_limits into the frozen desktop app."""
    import io as _io, os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = _io.open(_os.path.join(root, "gui2", "app.py"), encoding="utf-8").read()
    assert "start_housekeeping" not in src, (
        "gui2/app.py still starts the retention sweep")


def test_the_server_passes_its_own_sweeper():
    """The server is the half that has accounts to sweep, so it supplies the
    function rather than the queue reaching for it."""
    import io as _io, os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = _io.open(_os.path.join(root, "server.py"), encoding="utf-8").read()
    assert "sweep=" in src and "site_limits" in src, (
        "server.py no longer hands its sweeper to start_housekeeping")
