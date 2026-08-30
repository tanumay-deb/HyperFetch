"""The per-file breakdown is fetched only while something is looking at it.

aria2.getFiles ran every FILES_POLL for EVERY torrent, but file_progress is read
in exactly two places — the drawer's Files tab and the preview action. With the
drawer closed (the normal case) every one of those calls was thrown away, and it
was a third of all torrent RPC traffic. aria2 serves RPC from the same single
thread that downloads, so that traffic is not free.
"""
import threading
import time

import pytest

import aria2d
import task as T
import torrent
import utils


class _Daemon:
    def __init__(self, total=1000):
        self.total = total
        self.counts = {}

    def ensure(self):
        return True

    def call(self, method, *a, **kw):
        self.counts[method] = self.counts.get(method, 0) + 1
        if method == "aria2.tellStatus":
            return {"status": "active", "completedLength": "1",
                    "totalLength": str(self.total), "connections": "3",
                    "files": [{"path": "x"}]}
        if method == "aria2.getFiles":
            return [{"path": "a.mkv", "length": "1000", "completedLength": "10",
                     "selected": "true"}]
        if method in ("aria2.addUri", "aria2.addTorrent"):
            return "gid1"
        return {}


def _run(tmp_path, monkeypatch, watched, seconds=1.2):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.01)
    monkeypatch.setattr(torrent, "FILES_POLL", 0.05)
    d = _Daemon()
    monkeypatch.setattr(aria2d, "DAEMON", d)
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "out"))
    t.files_watched = watched
    td = torrent.TorrentDownloader(t)
    th = threading.Thread(target=td._run_rpc, daemon=True)
    th.start()
    time.sleep(seconds)
    t.request_pause()
    th.join(timeout=5)
    return d, t


def test_getfiles_is_not_polled_when_nobody_is_watching(tmp_path, monkeypatch):
    d, t = _run(tmp_path, monkeypatch, watched=False)
    n = d.counts.get("aria2.getFiles", 0)
    # one fetch is allowed so the tab is not blank when first opened
    assert n <= 1, (
        f"{n} getFiles calls with the drawer closed — this ran every FILES_POLL "
        "for every torrent and the answer was discarded")


def test_the_first_fetch_still_happens(tmp_path, monkeypatch):
    """Opening the Files tab must not show an empty table while it waits."""
    d, t = _run(tmp_path, monkeypatch, watched=False)
    assert d.counts.get("aria2.getFiles", 0) == 1
    assert t.file_progress, "no file list at all, so the tab opens blank"


def test_getfiles_keeps_polling_while_the_tab_is_open(tmp_path, monkeypatch):
    d, t = _run(tmp_path, monkeypatch, watched=True)
    n = d.counts.get("aria2.getFiles", 0)
    assert n > 2, f"only {n} getFiles calls while the tab was open — it must stay live"


def test_status_polling_is_unaffected(tmp_path, monkeypatch):
    """Gating the file list must not slow progress or speed."""
    d, _ = _run(tmp_path, monkeypatch, watched=False)
    assert d.counts.get("aria2.tellStatus", 0) > 2


# ---- the drawer side: the flag has to be released ---------------------------
def test_the_watch_flag_is_released_when_the_drawer_moves_on():
    """A task left flagged would poll getFiles forever for a closed panel."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    from gui2.details_drawer import DetailsDrawer

    app = QApplication.instance() or QApplication([])
    a = T.DownloadTask("magnet:?xt=urn:btih:a", "C:/dl/a")
    a.id = "task-a"
    b = T.DownloadTask("magnet:?xt=urn:btih:b", "C:/dl/b")
    b.id = "task-b"

    class _Win(QWidget):
        class queue:
            @staticmethod
            def get_task(tid):
                return {"task-a": a, "task-b": b}.get(tid)

    # keep a reference: an unparented Qt object is garbage collected and the
    # underlying C++ widget goes with it
    win = _Win()
    drawer = DetailsDrawer(win)
    drawer._watch_files(a, True)
    assert a.files_watched is True

    drawer._watch_files(b, True)          # user selects another download
    assert a.files_watched is False, "the previous task kept polling getFiles"
    assert b.files_watched is True

    drawer._watch_files(None, False)      # drawer closed
    assert b.files_watched is False
