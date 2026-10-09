"""In-progress downloads are files in a temp folder, and nobody else's are touched.

On 2026-10-09 a 2 GB download failed at 100% with "disk error: [WinError 2]
The system cannot find the file specified" for its own temp file. A second
copy of the app's window had been started eleven seconds earlier, off screen
with an app-data folder of its own, to check something else. As it starts the
window removes the temp files of downloads it does not know - and it knew
none, so it removed the first app's, in the seconds that one was being copied
to another drive (the one moment a temp file is not held open for writing).
The copy finished, removing its source failed, and the finished copy was then
thrown away as a failed move.

The test suite did the same thing: every test that loads the app's state ran
that sweep over the machine's real temp folder.
"""
import json
import os
import shutil
import tempfile
import time

import pytest

import conftest
import utils

A, B, C = "a" * 32, "b" * 32, "c" * 32


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _inside(path, folder):
    path, folder = (os.path.normcase(os.path.abspath(p)) for p in (path, folder))
    return os.path.commonpath([path, folder]) == folder


# ---- the tests themselves ---------------------------------------------------------
def test_no_test_is_given_the_machines_own_temp_folder(tmp_path, tmp_path_factory):
    here = tempfile.gettempdir()
    assert not _same(here, conftest.REAL_TEMP), "the suite would sweep the real temp folder"
    assert _inside(here, str(tmp_path_factory.getbasetemp())) and os.path.isdir(here)
    assert not _inside(here, str(tmp_path)), "tests list their own folder to see what was saved"
    assert os.listdir(here) == [], "another test's temp folder"
    assert not _same(os.path.dirname(utils.temp_download_path("a" * 32)), conftest.REAL_TEMP)
    for var in ("TEMP", "TMP"):
        assert _same(os.environ[var], here), "a child process would use the real one"


# ---- where they are ---------------------------------------------------------------------
def test_an_installation_keeps_its_temp_files_in_a_folder_of_its_own():
    p = utils.temp_download_path(A)
    assert os.path.basename(p) == A + ".hfdownload"
    assert _same(os.path.dirname(p), utils.temp_dir())
    assert _inside(utils.temp_dir(), tempfile.gettempdir())
    assert not _same(utils.temp_dir(), tempfile.gettempdir())
    assert os.path.isdir(utils.temp_dir()), "nowhere to write the file"


def test_two_installations_do_not_share_one(tmp_path, monkeypatch):
    """Whose a temp file is, is told by where it is: the folder goes with the
    app-data folder, which is what makes two HyperFetches two."""
    mine = utils.temp_dir()
    assert _same(utils.temp_dir(), mine), "not the same folder twice"
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "another"))
    assert not _same(utils.temp_dir(), mine)


def test_a_download_paused_before_this_goes_on_from_its_old_temp_file():
    """They were straight in the temp folder. One paused there before the
    update must not start again from nothing."""
    old = os.path.join(tempfile.gettempdir(), B + ".hfdownload")
    with open(old, "wb") as f:
        f.write(b"half of it")
    assert _same(utils.temp_download_path(B), old)
    os.remove(old)                              # finished, or started over
    assert _same(os.path.dirname(utils.temp_download_path(B)), utils.temp_dir())


# ---- what a crash left behind ------------------------------------------------------------
def _temp(tid, hours, where=None):
    """A temp file last written `hours` ago."""
    where = where or utils.temp_dir()
    os.makedirs(where, exist_ok=True)
    p = os.path.join(where, tid + ".hfdownload")
    with open(p, "wb") as f:
        f.write(b"x" * 10)
    at = time.time() - hours * 3600
    os.utime(p, (at, at))
    return p


def test_an_orphan_of_ours_goes_once_nobody_has_written_it_for_a_day():
    old, fresh, known = _temp(A, 48), _temp(B, 2), _temp(C, 48)

    removed = utils.sweep_orphan_temps({C})

    assert [os.path.basename(p) for p in removed] == [A + ".hfdownload"]
    assert not os.path.exists(old)
    assert os.path.exists(fresh), "a file written two hours ago is somebody's download"
    assert os.path.exists(known), "a download in the list lost its temp file"


def test_another_installations_temp_files_are_not_ours_to_remove(tmp_path, monkeypatch):
    """The server beside the desktop app; a second window started for a check,
    with an app-data folder of its own and so an empty list. What happened on
    2026-10-09: the first one's download was being copied into place."""
    being_copied = _temp(A, 0)
    paused_a_month_ago = _temp(B, 24 * 30)
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "second"))

    assert utils.sweep_orphan_temps(set()) == []

    assert os.path.exists(being_copied) and os.path.exists(paused_a_month_ago)


def test_temp_files_straight_in_the_temp_folder_are_left_alone():
    """Where every HyperFetch kept them before, and the server still does:
    whose one is cannot be told, however old it is."""
    p = _temp(A, 24 * 30, where=tempfile.gettempdir())
    assert utils.sweep_orphan_temps(set()) == []
    assert os.path.exists(p)


def test_nothing_but_temp_files_is_removed():
    other = os.path.join(utils.temp_dir(), "notes.txt")
    os.makedirs(utils.temp_dir(), exist_ok=True)
    with open(other, "w") as f:
        f.write("x")
    at = time.time() - 48 * 3600
    os.utime(other, (at, at))
    assert utils.sweep_orphan_temps(set()) == []
    assert os.path.exists(other)


def test_the_window_sweeps_only_that_way_and_says_what_it_removed(caplog):
    pytest.importorskip("PySide6")
    from gui2.app import DownloadAppV2
    from queue_manager import QueueManager
    known = {"id": C, "url": "http://x/c.bin", "save_path": "C:/dl/c.bin",
             "filename": "c.bin", "status": "Paused", "total_size": 10, "downloaded": 5}
    state = os.path.join(utils.app_data_dir(), "downloads.json")
    with open(state, "w", encoding="utf-8") as f:
        json.dump([known], f)
    ours_old, ours_known = _temp(A, 48), _temp(C, 48)
    somebody_elses = _temp(B, 48, where=tempfile.gettempdir())
    app = DownloadAppV2.__new__(DownloadAppV2)          # no GUI construction
    app._state_path = state
    app.queue = QueueManager()
    try:
        with caplog.at_level("INFO", logger="hyperfetch.gui"):
            app._load_state()
    finally:
        app.queue.shutdown()

    assert not os.path.exists(ours_old)
    assert os.path.exists(ours_known) and os.path.exists(somebody_elses)
    assert any(A + ".hfdownload" in r.getMessage() for r in caplog.records), (
        "a file was removed and the log does not say so")


# ---- the move into place ---------------------------------------------------------------------
def _finished(tmp_path, body=b"x" * 1000):
    temp = tmp_path / "t.hfdownload"
    temp.write_bytes(body)
    dest = tmp_path / "out" / "file.bin"
    dest.parent.mkdir()
    return str(temp), str(dest)


def test_a_finished_copy_is_kept_when_its_source_went_from_under_the_move(tmp_path,
                                                                        monkeypatch):
    """Moving to another drive is a copy and then a remove. Windows copies
    with the source open for others to delete (Python 3.12 and later), so
    another program's delete succeeds meanwhile; the copy finishes, and the
    remove fails with WinError 2 for the temp file. The whole copy was then
    thrown away as a failed move: 2 GB of it, on 2026-10-09."""
    temp, dest = _finished(tmp_path)

    def move(src, dst):
        shutil.copy2(src, dst)                  # the copy, whole
        os.remove(src)                          # somebody else's delete
        raise FileNotFoundError(2, "The system cannot find the file specified", src)

    monkeypatch.setattr(shutil, "move", move)

    utils.finalize_download(temp, dest)

    assert open(dest, "rb").read() == b"x" * 1000
    assert not os.path.exists(dest + ".hfmove")


def test_a_copy_cut_short_is_still_a_failed_move(tmp_path, monkeypatch):
    temp, dest = _finished(tmp_path)

    def move(src, dst):
        with open(dst, "wb") as f:
            f.write(b"x" * 10)
        os.remove(src)
        raise FileNotFoundError(2, "The system cannot find the file specified", src)

    monkeypatch.setattr(shutil, "move", move)
    with pytest.raises(OSError):
        utils.finalize_download(temp, dest)
    assert not os.path.exists(dest) and not os.path.exists(dest + ".hfmove")


def test_a_move_that_failed_with_its_source_still_there_can_be_tried_again(tmp_path,
                                                                         monkeypatch):
    temp, dest = _finished(tmp_path)

    def move(src, dst):
        shutil.copy2(src, dst)
        raise PermissionError(13, "Access is denied", src)

    monkeypatch.setattr(shutil, "move", move)
    with pytest.raises(OSError):
        utils.finalize_download(temp, dest)
    assert os.path.exists(temp), "nothing left to retry from"
    assert not os.path.exists(dest) and not os.path.exists(dest + ".hfmove")


def test_a_temp_file_that_is_not_there_is_still_a_failure(tmp_path):
    dest = tmp_path / "file.bin"
    with pytest.raises(OSError):
        utils.finalize_download(str(tmp_path / "gone.hfdownload"), str(dest))
    assert not dest.exists()
