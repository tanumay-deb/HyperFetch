"""A finished download is opened when Settings says so - a program never run.

"When complete: Open file" started the finished file with os.startfile: a
program, a script or a shortcut ran as it came off the internet, and with the
New Download dialog off a web page could start a download and have it run
(2026-11-02). A program is shown in its folder instead. And a finished
download gets the "from the internet" mark a browser gives one, so Windows
asks before a program from it runs - not the app's own update, which is
checked against GitHub's SHA-256 (and is not signed).
"""
import os
import sys
import types

import pytest

import task as T
import utils
from gui2.app import DownloadAppV2 as A

MARK = b"[ZoneTransfer]\r\nZoneId=3\r\n"
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own mark")


@pytest.mark.parametrize("name, runs", [
    ("setup.exe", True), ("Run.BAT", True), ("x.msi", True), ("a.lnk", True),
    ("s.ps1", True), ("film.mp4", False), ("notes.pdf", False), ("Pack", False),
])
def test_what_windows_would_run(name, runs):
    assert utils.runs_code(os.path.join("C:/d", name)) is runs


def _app(tasks, when="Open file", update=None):
    """Just enough of DownloadAppV2 for _check_completions."""
    opened = []
    app = types.SimpleNamespace(
        queue=types.SimpleNamespace(tasks=tasks),
        _completed_seen=None, _errored_seen=None, _unfiled=set(),
        _extras={"categorize": False, "when_complete": when, "skip_complete_popup": True},
        _save_state=lambda: None,
        _is_update=lambda t: t is update,
        _update_finished=lambda t: None,
        _maybe_categorize=lambda t: None,
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _flash_taskbar=lambda: None, tray=None,
        _open_file=lambda t: opened.append(("file", t.filename)),
        _open_folder=lambda t: opened.append(("folder", t.filename)),
    )
    app.opened = opened
    return app


def _finish(app, t):
    t.status = T.DOWNLOADING
    A._check_completions(app)                  # the first tick takes stock
    t.status = T.COMPLETED
    A._check_completions(app)


def _done(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"x")
    return T.DownloadTask("https://a.example/" + name, str(f), filename=name)


@pytest.mark.parametrize("name, how", [("setup.exe", "folder"), ("film.mp4", "file")])
def test_open_file_runs_no_program(tmp_path, name, how):
    t = _done(tmp_path, name)
    app = _app([t])
    _finish(app, t)
    assert app.opened == [(how, name)]


@windows
def test_a_finished_download_is_marked_from_the_internet(tmp_path):
    t = _done(tmp_path, "setup.exe")
    _finish(_app([t], when="Do nothing"), t)
    assert open(t.save_path + ":Zone.Identifier", "rb").read() == MARK


@windows
def test_a_folder_is_marked_file_by_file(tmp_path):
    pack = tmp_path / "Pack"
    (pack / "sub").mkdir(parents=True)
    for f in (pack / "a.exe", pack / "sub" / "b.docx"):
        f.write_bytes(b"x")
    utils.mark_from_internet(str(pack))
    for f in (pack / "a.exe", pack / "sub" / "b.docx"):
        assert open(str(f) + ":Zone.Identifier", "rb").read() == MARK


@windows
def test_the_apps_own_update_is_not_marked(tmp_path):
    t = _done(tmp_path, "HyperFetch-9.9.9-setup.exe")
    _finish(_app([t], when="Do nothing", update=t), t)
    with pytest.raises(FileNotFoundError):
        open(t.save_path + ":Zone.Identifier", "rb")


@windows
def test_a_finished_folder_is_marked_off_the_windows_thread(tmp_path):
    """A torrent's folder can hold thousands of files: marked on a thread."""
    import time
    pack = tmp_path / "Pack"
    pack.mkdir()
    (pack / "a.exe").write_bytes(b"x")
    t = T.DownloadTask("magnet:?xt=urn:btih:%040d" % 5, str(pack), filename="Pack")
    _finish(_app([t], when="Do nothing"), t)
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            if open(str(pack / "a.exe") + ":Zone.Identifier", "rb").read() == MARK:
                break
        except OSError:
            pass
        time.sleep(0.02)
    assert open(str(pack / "a.exe") + ":Zone.Identifier", "rb").read() == MARK
