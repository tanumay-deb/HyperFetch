"""Move to… runs on a thread and the card shows how far it is.

The window used to call shutil.move itself: a move to another drive is a
whole copy, and the window froze for as long as it took, with nothing on
screen to say why. Now utils.move_with_progress does it - a rename on one
drive, a copy that counts its bytes to another, the source removed only once
all of it is across - and the card reads "Moving…  •  X / Y" with its bar.
Resume waits for it: the files are on their way elsewhere.
"""
import errno
import os
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import task as T
import utils

DATA = os.urandom(3 * 1024 * 1024 + 7)


def _other_drive(monkeypatch):
    """os.rename as it answers for a move to another drive."""
    def rename(a, b):
        raise OSError(errno.EXDEV, "cross-device link")
    monkeypatch.setattr(utils.os, "rename", rename)


def _tree(root):
    return sorted(os.path.relpath(os.path.join(d, f), root)
                  for d, _, files in os.walk(root) for f in files)


# ---- the helper -----------------------------------------------------------------
def test_on_one_drive_it_is_a_rename(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(DATA)
    seen = []
    utils.move_with_progress(str(src), str(tmp_path / "b.bin"), seen.append)
    assert (tmp_path / "b.bin").read_bytes() == DATA and not src.exists()
    assert seen == [len(DATA)]


def test_to_another_drive_a_file_is_copied_counting(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    src, dst = tmp_path / "a.bin", tmp_path / "out" / "a.bin"
    src.write_bytes(DATA)
    dst.parent.mkdir()
    seen = []
    utils.move_with_progress(str(src), str(dst), seen.append, chunk=1024 * 1024)
    assert dst.read_bytes() == DATA and not src.exists()
    assert seen == sorted(seen) and seen[-1] == len(DATA) and len(seen) == 4
    assert os.listdir(dst.parent) == ["a.bin"], "the staged copy was left"


def test_to_another_drive_a_folder_is_copied_whole(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    src = tmp_path / "Show"
    (src / "Season 1").mkdir(parents=True)
    (src / "Season 1" / "e1.mkv").write_bytes(DATA)
    (src / "info.nfo").write_bytes(b"x" * 10)
    dst = tmp_path / "out" / "Show"
    (tmp_path / "out").mkdir()
    seen = []
    utils.move_with_progress(str(src), str(dst), seen.append)
    assert _tree(dst) == [os.path.join("Season 1", "e1.mkv"), "info.nfo"]
    assert (dst / "Season 1" / "e1.mkv").read_bytes() == DATA
    assert not src.exists() and seen[-1] == len(DATA) + 10


def test_a_copy_that_fails_leaves_the_source_whole(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    src = tmp_path / "Show"
    src.mkdir()
    (src / "e1.mkv").write_bytes(DATA)
    (src / "e2.mkv").write_bytes(DATA)
    dst = tmp_path / "out" / "Show"
    (tmp_path / "out").mkdir()

    def full(n):                          # the other drive fills up part way
        if n > len(DATA):
            raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError):
        utils.move_with_progress(str(src), str(dst), full, chunk=1024 * 1024)
    assert not dst.exists(), "half a copy was left behind"
    assert _tree(src) == ["e1.mkv", "e2.mkv"]
    assert (src / "e2.mkv").read_bytes() == DATA


def test_a_file_that_arrives_while_it_copies_is_not_written_over(tmp_path, monkeypatch):
    """A download of the same name finished in that folder while the move
    copied there, and the move's replace wrote over it (2026-11-01)."""
    _other_drive(monkeypatch)
    src, dst = tmp_path / "a.bin", tmp_path / "out" / "a.bin"
    src.write_bytes(DATA)
    dst.parent.mkdir()

    def meanwhile(n):
        if not dst.exists():
            dst.write_bytes(b"finished meanwhile")

    with pytest.raises(FileExistsError):
        utils.move_with_progress(str(src), str(dst), meanwhile, chunk=1024 * 1024)
    assert dst.read_bytes() == b"finished meanwhile"
    assert src.read_bytes() == DATA, "the source went"
    assert os.listdir(dst.parent) == ["a.bin"], "the staged copy was left"


def test_a_folder_that_arrives_while_it_copies_is_left_alone(tmp_path, monkeypatch):
    """It was copied INTO that folder, and a failure then removed the lot."""
    _other_drive(monkeypatch)
    src = tmp_path / "Show"
    src.mkdir()
    (src / "e1.mkv").write_bytes(DATA)
    dst = tmp_path / "out" / "Show"
    (tmp_path / "out").mkdir()

    def meanwhile(n):
        if not dst.exists():
            dst.mkdir()
            (dst / "theirs.mkv").write_bytes(b"theirs")

    with pytest.raises(FileExistsError):
        utils.move_with_progress(str(src), str(dst), meanwhile, chunk=1024 * 1024)
    assert _tree(dst) == ["theirs.mkv"] and (dst / "theirs.mkv").read_bytes() == b"theirs"
    assert _tree(src) == ["e1.mkv"]
    assert os.listdir(tmp_path / "out") == ["Show"], "the staged copy was left"


# ---- the window ---------------------------------------------------------------------
@pytest.fixture
def window(monkeypatch, tmp_path):
    from collections import deque
    import gui2.app_actions as actions
    from PySide6.QtWidgets import QFileDialog
    dest = tmp_path / "elsewhere"
    dest.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(dest))
    toasts, resumed = [], []

    def pause(t):                           # the queue, with the worker gone at once
        t.status = T.PAUSED

    def resume(t):
        resumed.append(t)
        t.status = T.QUEUED

    win = types.SimpleNamespace(
        save_dir=str(tmp_path), _moves=deque(), refresh=lambda: None,
        _save_state=lambda: None,
        _toasts=types.SimpleNamespace(show=lambda *a, **k: toasts.append(a)),
        queue=types.SimpleNamespace(pause_task=pause, resume_task=resume))
    win.resumed = resumed
    for name in ("_move_task_files", "_finish_moves", "_moved"):
        setattr(win, name, (lambda n: lambda *a: getattr(actions.ActionsMixin, n)(win, *a))(name))
    win.toasts, win.dest = toasts, dest
    return win


def _finished(t, file):
    t.status = T.COMPLETED
    t.save_path = str(file)
    t.filename = file.name
    return t


def test_a_move_runs_beside_the_window_and_lands(window, tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    f = tmp_path / "film.mkv"
    f.write_bytes(DATA)
    t = _finished(T.DownloadTask("https://a.example/film.mkv", str(f)), f)
    # not finished, so Resume has something to wait for; a video's, as an
    # unfinished plain download's save path holds another's file (2026-11-03)
    t.status, t.use_ytdlp = T.PAUSED, True
    gate = threading.Event()
    real = utils.move_with_progress

    def held(src, dst, progress, **kw):    # hold it half way, as a slow drive would
        progress(len(DATA) // 2)
        gate.wait(10)
        real(src, dst, progress, **kw)

    monkeypatch.setattr(utils, "move_with_progress", held)
    window._move_task_files(t)
    assert t.moving and t.move_total == len(DATA), "the window is doing the move itself"
    end = time.monotonic() + 5
    while not t.move_done and time.monotonic() < end:
        time.sleep(0.01)
    assert t.move_done == len(DATA) // 2

    from queue_manager import QueueManager
    q = QueueManager()
    q.shutdown()                            # nothing may really start here
    q.resume_task(t)
    q.force_start(t)
    assert t.status == T.PAUSED, "Resume did not wait for the move"

    gate.set()
    end = time.monotonic() + 10
    while not window._moves and time.monotonic() < end:
        time.sleep(0.02)
    window._finish_moves()
    assert not t.moving
    assert t.save_path == str(window.dest / "film.mkv") and t.sort_base == ""
    assert (window.dest / "film.mkv").read_bytes() == DATA and not f.exists()
    assert window.toasts[-1][1] == "Moved"


def test_a_move_that_fails_says_so_and_changes_nothing(window, tmp_path, monkeypatch):
    f = tmp_path / "film.mkv"
    f.write_bytes(DATA)
    t = _finished(T.DownloadTask("https://a.example/film.mkv", str(f)), f)

    def fails(*a, **k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(utils, "move_with_progress", fails)
    window._move_task_files(t)
    end = time.monotonic() + 10
    while not window._moves and time.monotonic() < end:
        time.sleep(0.02)
    window._finish_moves()
    assert not t.moving and t.save_path == str(f)
    assert window.toasts[-1][1] == "Move failed" and "denied" in window.toasts[-1][2]


# ---- a torrent that is not finished (asked 2026-10-29) ---------------------------------
# "Pause first" refused a running one, and a paused one left its bytes and
# aria2's control file behind: its save_path is a stand-in until it finishes
# (magnet_.bin), so "nothing on disk" was read, only the destination changed,
# the card was renamed magnet_.bin, and it started over in the new folder.
def _half_done(tmp_path, status):
    old = tmp_path / "old"
    (old / "Pack" / "Season 1").mkdir(parents=True)
    (old / "Pack" / "Season 1" / "e1.mkv").write_bytes(DATA)
    (old / "Pack.aria2").write_bytes(b"control")
    t = T.DownloadTask("magnet:?xt=urn:btih:%040d" % 7, str(old / "magnet_.bin"),
                       filename="Pack")
    t.status, t.downloaded = status, len(DATA)
    t.file_progress = [{"index": 1, "path": str(old / "Pack" / "Season 1" / "e1.mkv"),
                        "length": len(DATA), "completed": len(DATA), "selected": True}]
    return t, old


def _landed(window):
    end = time.monotonic() + 10
    while not window._moves and time.monotonic() < end:
        time.sleep(0.02)
    window._finish_moves()


@pytest.mark.parametrize("status", [T.DOWNLOADING, T.PAUSED])
def test_an_unfinished_torrent_takes_its_bytes_and_goes_on_there(window, tmp_path, status):
    t, old = _half_done(tmp_path, status)
    window._move_task_files(t)
    _landed(window)
    assert window.toasts[-1][1] == "Moved", window.toasts
    assert _tree(window.dest) == sorted([os.path.join("Pack", "Season 1", "e1.mkv"), "Pack.aria2"])
    assert not (old / "Pack").exists() and not (old / "Pack.aria2").exists()
    assert t.save_path == str(window.dest / "magnet_.bin") and t.filename == "Pack"
    assert t.sort_base == "" and not t.moving
    # its files' numbers name the new place, or the Files tab lost its folders
    assert t.file_progress[0]["path"] == str(window.dest / "Pack" / "Season 1" / "e1.mkv")
    # it was downloading: it goes on, there; paused, it stays paused
    assert window.resumed == ([t] if status == T.DOWNLOADING else [])


def test_an_unfinished_torrent_will_not_take_a_name_already_there(window, tmp_path):
    t, old = _half_done(tmp_path, T.DOWNLOADING)
    (window.dest / "Pack").mkdir()
    window._move_task_files(t)
    _landed(window)
    assert window.toasts[-1][1] == "Move failed" and "Pack" in window.toasts[-1][2]
    assert (old / "Pack" / "Season 1" / "e1.mkv").exists() and (old / "Pack.aria2").exists()
    assert t.save_path == str(old / "magnet_.bin")
    assert window.resumed == [t], "paused for the move, and left paused"


def test_the_card_shows_how_far_the_move_is():
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = T.DownloadTask("https://a.example/film.mkv", "C:/dl/film.mkv", filename="film.mkv")
    t.status = T.COMPLETED
    t.total_size = t.downloaded = 1000
    t.moving, t.move_done, t.move_total = True, 250, 1000
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert card.sub._full.startswith("Moving…")
    assert card.bar.value() == 25 and not card.bar.isHidden()
    t.moving = False
    card.update_task(t, 0.0)
    assert card.sub._full.startswith("Completed") and card.bar.isHidden()


def test_the_internet_mark_goes_with_a_file_to_another_drive(tmp_path, monkeypatch):
    """A copy to another drive takes a file's contents only, and the "from the
    internet" mark (utils.mark_from_internet) stayed behind (2026-11-02)."""
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows' own mark")
    _other_drive(monkeypatch)
    src, dst = tmp_path / "setup.exe", tmp_path / "out" / "setup.exe"
    src.write_bytes(DATA)
    dst.parent.mkdir()
    utils.mark_from_internet(str(src))
    utils.move_with_progress(str(src), str(dst))
    assert open(str(dst) + ":Zone.Identifier", "rb").read() == b"[ZoneTransfer]\r\nZoneId=3\r\n"


def test_an_unfinished_download_never_takes_another_one_s_file(window, tmp_path):
    """Its bytes are in its temp file; what is at its save path is another
    download's - one of its name that finished first. That file was moved and
    recorded as this one's, and the finished download pointed at nothing
    (2026-11-03)."""
    theirs = tmp_path / "setup.exe"
    theirs.write_bytes(b"the one that finished first")
    t = T.DownloadTask("https://a.example/setup.exe", str(theirs), filename="setup.exe")
    t.status = T.PAUSED
    window._move_task_files(t)
    assert theirs.read_bytes() == b"the one that finished first", "the other file was moved"
    assert not (window.dest / "setup.exe").exists()
    assert t.save_path == str(window.dest / "setup.exe"), "this one was not sent there"
    assert not t.moving


@pytest.mark.parametrize("status", [T.DOWNLOADING, T.PAUSED])
def test_an_unfinished_download_to_another_drive_takes_its_partial_along(window, tmp_path,
                                                                         monkeypatch, status):
    """Move to… only changed save_path, leaving the partial on the full drive
    it was moved off: every Resume failed with No space left on device again
    (2026-11-12, #1)."""
    monkeypatch.setattr(utils, "same_drive",
                        lambda a, b: os.path.abspath(a) == os.path.abspath(b))
    old = tmp_path / "old"
    old.mkdir()
    t = T.DownloadTask("https://a.example/big.bin", str(old / "big.bin"), filename="big.bin")
    t.status, t.downloaded = status, len(DATA)
    old_temp = utils.temp_download_path(t)
    open(old_temp, "wb").write(DATA)

    window._move_task_files(t)
    _landed(window)

    assert window.toasts[-1][1] == "Moved", window.toasts
    assert t.save_path == str(window.dest / "big.bin") and t.sort_base == ""
    new_temp = utils.temp_download_path(t)
    assert os.path.dirname(os.path.dirname(new_temp)) == str(window.dest)
    assert open(new_temp, "rb").read() == DATA
    assert not os.path.exists(old_temp) and os.listdir(old) == []
    assert window.resumed == ([t] if status == T.DOWNLOADING else [])


def test_a_partial_in_the_unfinished_downloads_folder_stays_there(window, tmp_path,
                                                                  monkeypatch):
    """With an unfinished-downloads folder set, a partial is kept there
    whatever the destination: the move "moved" it onto itself on a thread,
    the card saying "Moving…" (round 2 #5)."""
    monkeypatch.setattr(utils, "same_drive",
                        lambda a, b: os.path.abspath(a) == os.path.abspath(b))
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unfinished"))
    t = T.DownloadTask("https://a.example/big.bin", str(tmp_path / "big.bin"),
                       filename="big.bin")
    t.status, t.downloaded = T.PAUSED, len(DATA)
    partial = utils.temp_download_path(t)
    open(partial, "wb").write(DATA)
    assert os.path.dirname(os.path.dirname(partial)) == str(tmp_path / "unfinished")

    window._move_task_files(t)

    assert not getattr(t, "moving", False), "nothing to move: it stays where it is"
    assert t.save_path == str(window.dest / "big.bin")
    assert utils.temp_download_path(t) == partial and open(partial, "rb").read() == DATA

