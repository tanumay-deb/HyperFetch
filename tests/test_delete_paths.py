"""What "also delete from disk" actually removes.

save_path alone is wrong for torrents: it stays at the placeholder the task was
created with (…\Downloads\download.bin) until the download COMPLETES, so for
anything paused or unfinished it names a file that never existed — and the real
payload, a folder beside it named after the torrent, was left on disk while the
app reported success.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T
from gui2.app_shortcuts import ShortcutsMixin as S


def _task(save_path, filename):
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", save_path, filename=filename)
    return t


def test_the_torrent_payload_folder_is_found(tmp_path):
    """The real case from the user's machine."""
    payload = tmp_path / "Hannibal.S01.1080p"
    payload.mkdir()
    (payload / "ep1.mkv").write_bytes(b"x" * 10)
    (tmp_path / "Hannibal.S01.1080p.aria2").write_bytes(b"ctl")   # aria2's, while unfinished
    t = _task(str(tmp_path / "download.bin"), "Hannibal.S01.1080p")
    assert S._payload_paths(t) == [str(payload)]


def test_a_plain_file_download_is_found(tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    t = _task(str(f), "a.zip")
    t.status = T.COMPLETED
    assert S._payload_paths(t) == [str(f)]


def test_nothing_on_disk_returns_nothing(tmp_path):
    t = _task(str(tmp_path / "download.bin"), "Never.Downloaded")
    assert S._payload_paths(t) == []


def test_placeholder_names_are_never_joined(tmp_path):
    """"torrent" is the engine's own placeholder — a folder of that name in the
    download dir would not be this download's payload."""
    (tmp_path / "torrent").mkdir()
    t = _task(str(tmp_path / "download.bin"), "torrent")
    assert S._payload_paths(t) == []


def test_the_download_folder_itself_is_never_a_target(tmp_path):
    """One bad join here would take every download the user has."""
    t = _task(str(tmp_path / "download.bin"), ".")
    assert S._payload_paths(t) == []
    t2 = _task(str(tmp_path / "download.bin"), "..")
    assert S._payload_paths(t2) == []


def test_a_name_escaping_the_folder_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    t = _task(str(tmp_path / "download.bin"), os.path.join("..", "elsewhere"))
    assert S._payload_paths(t) == []


def test_both_are_returned_when_both_exist(tmp_path):
    f = tmp_path / "download.bin"
    f.write_bytes(b"x")
    (tmp_path / "download.bin.aria2").write_bytes(b"ctl")
    payload = tmp_path / "Show.S01"
    payload.mkdir()
    (tmp_path / "Show.S01.aria2").write_bytes(b"ctl")
    t = _task(str(f), "Show.S01")
    assert sorted(S._payload_paths(t)) == sorted([str(f), str(payload)])


def test_an_unfinished_torrent_does_not_take_another_file_of_its_name(tmp_path):
    """An earlier movie.mkv where a torrent of that name was to go: aria2
    refused to start over it, and "delete from disk" on the torrent then
    deleted it (2026-11-01). The torrent's own files have aria2's control
    file beside them until it finishes."""
    theirs = tmp_path / "movie.mkv"
    theirs.write_bytes(b"an earlier download")
    t = _task(str(tmp_path / "movie.mkv"), "movie.mkv")
    t.status = T.ERROR
    assert S._payload_paths(t) == []
    (tmp_path / "movie.mkv.aria2").write_bytes(b"ctl")      # now aria2's own
    assert S._payload_paths(t) == [str(theirs)]
    t.status = T.COMPLETED                                  # finished: its own, control file or not
    (tmp_path / "movie.mkv.aria2").unlink()
    assert S._payload_paths(t) == [str(theirs)]
    t.status = T.QUEUED                                     # queued again by a Force Recheck
    assert S._payload_paths(t) == [str(theirs)], "a finished one's files were left on disk"


def test_its_files_are_read_before_the_control_file_goes(tmp_path, monkeypatch):
    """remove_task and cleanup_artifacts take aria2's control file away, and
    the files were looked up after them: a half-done torrent was left on disk."""
    import types
    import gui2.dialogs.delete as D
    payload = tmp_path / "Pack"
    payload.mkdir()
    (payload / "e1.mkv").write_bytes(b"x")
    (tmp_path / "Pack.aria2").write_bytes(b"ctl")
    t = _task(str(tmp_path / "magnet_.bin"), "Pack")
    t.status = T.PAUSED

    class Asked:
        def __init__(self, **k):
            self.deleteDisk = types.SimpleNamespace(isChecked=lambda: True)

        def exec(self):
            return True

    monkeypatch.setattr(D, "DeleteDialog", Asked)
    win = _window()
    S._delete_tasks(win, [t])
    assert _gone(payload) and not (tmp_path / "Pack.aria2").exists()


class _Asked:
    def __init__(self, **k):
        import types
        self.deleteDisk = types.SimpleNamespace(isChecked=lambda: True)

    def exec(self):
        return True


def _window(remove_task=lambda t: None):
    """What _delete_tasks needs of the window."""
    import collections
    import types
    win = types.SimpleNamespace(
        queue=types.SimpleNamespace(remove_task=remove_task, tasks=[]),
        _toasts=types.SimpleNamespace(said=[]), _deletes=collections.deque(),
        _save_state=lambda: None, refresh=lambda: None,
        _payload_paths=S._payload_paths, _remove_path=S._remove_path)
    win._toasts.show = lambda *a, **k: win._toasts.said.append(a)
    win._delete_files = lambda jobs: S._delete_files(win, jobs)
    win._finish_deletes = lambda: S._finish_deletes(win)
    return win


def _wait_for(cond, timeout=10):
    import time
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.05)
    return cond()


def _gone(path):
    return _wait_for(lambda: not os.path.exists(path))


def test_a_running_torrents_files_are_deleted_once_its_engine_lets_go(tmp_path, monkeypatch):
    """Its engine had the files open: the window retried for 2 s, frozen, then
    said "Try again in a moment" of an entry already gone from the list
    (2026-11-04, list #2). Now the delete waits for the worker, off the
    window's thread."""
    import threading
    import time
    import gui2.dialogs.delete as D
    payload = tmp_path / "Pack"
    payload.mkdir()
    (tmp_path / "Pack.aria2").write_bytes(b"ctl")
    held = open(payload / "e1.mkv", "wb")       # as aria2 holds it while it runs
    t = _task(str(tmp_path / "magnet_.bin"), "Pack")
    t.status, t._worker_alive = T.DOWNLOADING, True

    def worker_stops():                         # the run ends a while after the cancel
        time.sleep(3)
        held.close()
        t._worker_alive = False

    monkeypatch.setattr(D, "DeleteDialog", _Asked)
    win = _window(remove_task=lambda t: threading.Thread(target=worker_stops).start())
    t0 = time.monotonic()
    S._delete_tasks(win, [t])
    assert time.monotonic() - t0 < 0.5, "the window waited for the files"
    assert _gone(payload), "the files were left on disk"
    assert _gone(tmp_path / "Pack.aria2")
    assert _wait_for(lambda: win._deletes)         # the thread's last step
    S._finish_deletes(win)
    assert win._toasts.said == []


def test_files_that_will_not_go_are_said_once_and_not_as_try_again(tmp_path, monkeypatch):
    import gui2.dialogs.delete as D
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    t = T.DownloadTask("https://a.example/a.zip", str(f), filename="a.zip")
    t.status = T.COMPLETED
    monkeypatch.setattr(D, "DeleteDialog", _Asked)
    monkeypatch.setattr(S, "_remove_path", staticmethod(lambda p, **k: False))
    win = _window()
    win._remove_path = S._remove_path
    S._delete_tasks(win, [t])
    assert _wait_for(lambda: win._deletes)
    S._finish_deletes(win)
    assert len(win._toasts.said) == 1
    kind, title, body = win._toasts.said[0]
    assert kind == "error" and "a.zip" in body and "Try again" not in body


def test_removal_handles_a_directory(tmp_path):
    d = tmp_path / "Show.S01"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "ep.mkv").write_bytes(b"x" * 5)
    assert S._remove_path(str(d)) is True
    assert not d.exists()


def test_an_unfinished_download_does_not_take_a_finished_file_of_its_name(tmp_path):
    """Two of one name were both given file.bin while neither was on disk; the
    first finished there. Deleting the second "from disk" took the first's."""
    f = tmp_path / "file.bin"
    f.write_bytes(b"the first one's")
    t = T.DownloadTask("https://a.example/file.bin", str(f), filename="file.bin")
    t.status = T.PAUSED
    assert S._payload_paths(t) == []
    t.status = T.COMPLETED                     # its own, once it has finished
    assert S._payload_paths(t) == [str(f)]


def test_a_torrent_added_again_while_its_delete_waits_keeps_its_files(tmp_path, monkeypatch):
    """The same magnet added again as the removed one's run ends: the files
    are the new one's, and aria2's registration too."""
    import time
    import torrent
    import gui2.dialogs.delete as D
    payload = tmp_path / "Pack"
    payload.mkdir()
    (payload / "e1.mkv").write_bytes(b"x")
    (tmp_path / "Pack.aria2").write_bytes(b"ctl")
    t = _task(str(tmp_path / "magnet_.bin"), "Pack")
    t.status, t._worker_alive = T.DOWNLOADING, True
    let_go = []
    monkeypatch.setattr(torrent, "_let_go", let_go.append)
    monkeypatch.setattr(D, "DeleteDialog", _Asked)
    win = _window()
    S._delete_tasks(win, [t])
    win.queue.tasks.append(_task(str(tmp_path / "magnet_.bin"), "Pack"))
    t._worker_alive = False
    assert _wait_for(lambda: win._deletes)
    assert payload.exists() and let_go == []
