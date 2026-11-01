"""The connections chosen in New Download are that download's.

Found 2026-11-01: the dialog's value was written over the queue's setting,
which every download reads as it starts. A download added with 16 that waited
started with whatever the next dialog said, and one added with 2 for a slow
server put every later download - browser ones too - on 2.
"""
import os
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T
import queue_manager
from queue_manager import QueueManager


def _window(base):
    from gui2.app import DownloadAppV2
    added = []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8,
                                  add_task=lambda t, start=True: added.append(t))
    win = types.SimpleNamespace(
        save_dir=str(base), segments=8, queue=queue, _extras={},
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _save_state=lambda: None, refresh=lambda: None)
    win._quick_values = lambda *a: DownloadAppV2._quick_values(win, *a)
    return win, added, DownloadAppV2


def _answered(monkeypatch, conns):
    from PySide6.QtWidgets import QApplication, QDialog
    from gui2 import app
    from gui2.dialogs.new_download import NewDownloadDialog   # not an earlier stand-in
    QApplication.instance() or QApplication([])

    class Answered(NewDownloadDialog):
        def __init__(self, parent, *a, **kw):
            super().__init__(None, *a, **kw)
            if conns is not None:
                self.conns.setValue(conns)

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(app, "NewDownloadDialog", Answered)


def test_the_dialogs_count_goes_on_the_download_not_the_setting(tmp_path, monkeypatch):
    win, added, App = _window(tmp_path)
    _answered(monkeypatch, 2)
    App._add_download(win, "https://files.example/a.zip", "a.zip", None)
    _answered(monkeypatch, None)                    # left as it opened
    App._add_download(win, "https://files.example/b.zip", "b.zip", None)

    a, b = added
    assert a.connections == 2
    assert b.connections == 0, "an untouched box follows the setting"
    assert win.queue.segments == 8 and win.segments == 8


def test_each_download_starts_with_its_own_count(monkeypatch):
    got = {}

    class Recorder:
        def __init__(self, task, segments=8):
            self.t, got[task.filename] = task, segments

        def run(self):
            self.t.status = T.COMPLETED

    monkeypatch.setattr(queue_manager, "Downloader", Recorder)
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}], segments=8)
    try:
        own = T.DownloadTask("http://x/own.bin", "own.bin", filename="own.bin")
        own.connections = 16
        q.add_task(own)
        q.add_task(T.DownloadTask("http://x/set.bin", "set.bin", filename="set.bin"))
        end = time.time() + 10
        while len(got) < 2 and time.time() < end:
            time.sleep(0.02)
    finally:
        q.shutdown()
    assert got == {"own.bin": 16, "set.bin": 8}


def test_the_count_is_kept_across_a_restart():
    t = T.DownloadTask("http://x/a.bin", "a.bin")
    t.connections = 4
    assert T.DownloadTask.from_dict(t.to_dict()).connections == 4
    old = t.to_dict()
    del old["connections"]                          # a list saved before this
    assert T.DownloadTask.from_dict(old).connections == 0
