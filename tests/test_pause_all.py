"""The command palette's "Pause all" pauses the waiting downloads too.

It paused the running ones only. Each pause freed a slot, and the next one
waiting started in it: with three running and five waiting, three others ran
on (2026-11-03).
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T


def test_pause_all_leaves_nothing_to_start(monkeypatch):
    import gui2.command_palette as palette
    from gui2.app import DownloadAppV2
    got = {}

    class Palette:
        def __init__(self, parent, actions):
            got["actions"] = {a[0]: a[3] for a in actions}

        def width(self):
            return 400

        def move(self, *a):
            pass

        def exec(self):
            pass

    monkeypatch.setattr(palette, "CommandPalette", Palette)
    tasks = []
    for name, status in (("a", T.DOWNLOADING), ("b", T.QUEUED), ("c", T.COMPLETED),
                         ("d", T.PAUSED)):
        t = T.DownloadTask("https://e.test/" + name, name)
        t.status = status
        tasks.append(t)
    paused = []
    where = types.SimpleNamespace(center=lambda: types.SimpleNamespace(x=lambda: 0),
                                  top=lambda: 0)
    win = types.SimpleNamespace(
        queue=types.SimpleNamespace(tasks=tasks, pause_task=paused.append),
        _save_state=lambda: None, refresh=lambda: None, geometry=lambda: where,
        _new_download=None, _open_settings=None, _open_queues=None, _open_history=None,
        _set_filter=lambda f: None)
    DownloadAppV2._open_command_palette(win)
    got["actions"]["Pause all"]()
    assert paused == tasks[:2], "a waiting download was left to start"
