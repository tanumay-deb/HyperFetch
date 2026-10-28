"""The list's controls, as asked for on 2026-10-28.

- Seeds before peers on every card (they were peers-first while a torrent
  ran and seeds-first while it waited) - tests/test_card_swarm.py.
- Sort by Seeds and by Peers.
- The bulk bar follows what the selection is, and goes on following it: five
  queued torrents had no Pause in it, so they had to be paused by right-click.
- A click on blank space anywhere clears the selection - in the list it did,
  on the header row or the window's margins it did not.
- Keep the PC awake while downloading (Settings -> Downloads).
"""
import os
import subprocess
import sys
import textwrap
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import task as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tor(status, seeds=0, peers=0, scraped=None):
    t = T.DownloadTask("magnet:?xt=urn:btih:%040x&dn=x" % id(object()), "C:/dl/x", filename="x")
    t.status = status
    t.tor_seeds, t.tor_conns = seeds, peers
    if scraped:
        t.swarm_seeds, t.swarm_peers = scraped
        t.swarm_at = time.time() - 60
    return t


# ---- sort by seeds / peers ------------------------------------------------------
def test_seeds_and_peers_are_sort_keys():
    from gui2.app import SORT_KEYS
    assert SORT_KEYS[5:] == ["Seeds", "Peers"]


def test_what_a_torrent_is_sorted_by():
    from gui2.app import _seeds_peers
    now = time.time()
    assert _seeds_peers(_tor(T.DOWNLOADING, seeds=4, peers=9), now) == (4, 9)    # live
    assert _seeds_peers(_tor(T.QUEUED, scraped=(12, 40)), now) == (12, 40)        # its trackers
    assert _seeds_peers(_tor(T.QUEUED), now) == (-1, -1)                          # nobody knows
    http = T.DownloadTask("https://a.example/f.zip", "C:/dl/f.zip", filename="f.zip")
    assert _seeds_peers(http, now) == (-1, -1)                                    # not a torrent


# ---- the bulk bar -----------------------------------------------------------------
def _bar_for(*statuses, queues=1):
    from PySide6.QtWidgets import QApplication
    from gui2.action_bar import ActionBar
    from gui2.app_actions import ActionsMixin
    QApplication.instance() or QApplication([])
    ts = [_tor(s) for s in statuses]
    bar = ActionBar()
    by_id = {t.id: t for t in ts}
    win = types.SimpleNamespace(
        action_bar=bar, _position_action_bar=lambda: None,
        queue=types.SimpleNamespace(get_task=by_id.get, queues=dict.fromkeys(range(queues))))
    ActionsMixin._update_action_bar(win, set(by_id))
    return {name: not getattr(bar, attr).isHidden() for name, attr in (
        ("pause", "btn_pause"), ("resume", "btn_resume"), ("start", "btn_force"),
        ("open", "btn_open"), ("folder", "btn_folder"), ("queue", "move_btn"))}


def test_five_queued_torrents_can_be_paused_from_the_bar():
    shown = _bar_for(*[T.QUEUED] * 5)
    assert shown["pause"] and shown["start"] and not shown["resume"]


def test_each_state_gets_its_actions():
    assert _bar_for(T.DOWNLOADING, T.DOWNLOADING) == dict(
        pause=True, resume=False, start=False, open=False, folder=False, queue=False)
    assert _bar_for(T.PAUSED, T.ERROR) == dict(
        pause=False, resume=True, start=True, open=False, folder=False, queue=False)
    assert _bar_for(T.COMPLETED) == dict(
        pause=False, resume=False, start=False, open=True, folder=True, queue=False)
    mixed = _bar_for(T.DOWNLOADING, T.PAUSED)
    assert mixed["pause"] and mixed["resume"]
    assert _bar_for(T.QUEUED, queues=2)["queue"]


def test_the_bar_follows_the_selection_as_it_changes():
    import inspect
    from gui2 import app
    assert "_update_action_bar(self.list.selected_ids())" in inspect.getsource(
        app.DownloadAppV2.refresh)


# ---- keep awake ----------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="SetThreadExecutionState is Windows")
def test_keep_awake_tells_windows_once_each_way(monkeypatch):
    import ctypes
    from gui2.app_system import SystemMixin
    told = []
    monkeypatch.setattr(ctypes.windll.kernel32, "SetThreadExecutionState", told.append)
    win = types.SimpleNamespace()
    for on in (True, True, False, False):
        SystemMixin._keep_awake(win, on)
    assert told == [0x80000001, 0x80000000], [hex(x) for x in told]


def test_keep_awake_is_off_unless_switched_on():
    import inspect
    from gui2.dialogs import settings_pages
    from gui2 import app
    assert 'ex.get("keep_awake", False)' in inspect.getsource(settings_pages)
    assert 'self._extras.get("keep_awake", False)' in inspect.getsource(app.DownloadAppV2.refresh)


# ---- a click on blank space ------------------------------------------------------------
SCRIPT = textwrap.dedent('''
    import json, os, sys
    appdata, root = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(appdata, "HyperFetch"), exist_ok=True)
    with open(os.path.join(appdata, "HyperFetch", "settings.json"), "w") as f:
        json.dump({"welcomed": True}, f)
    sys.path.insert(0, root)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import gui2.app as appmod
    appmod.run_server = lambda *a, **k: None          # no local server
    import task as T
    win = appmod.DownloadAppV2()
    win.resize(1300, 900); win.show(); app.processEvents()
    ts = []
    for i in range(5):
        t = T.DownloadTask("magnet:?xt=urn:btih:%040d&dn=T%d" % (i, i),
                           os.path.join(appdata, "dl", str(i)), filename="T%d" % i)
        t.status = T.PAUSED
        win.queue.add_task(t, start=False)
        ts.append(t)
    win.refresh(); app.processEvents()
    for t in ts:
        QTest.mouseClick(win.list._cards[t.id].chk, Qt.LeftButton)
    picked = len(win.list.selected_ids())
    # outside the list: the window's own top-left corner, which no widget holds
    spot = next((QPoint(x, y) for y in range(0, 80, 2) for x in range(0, 400, 4)
                 if win.childAt(QPoint(x, y)) is None
                 or not win.childAt(QPoint(x, y)).focusPolicy()), None)
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, spot)
    app.processEvents()
    print("RESULT", picked, len(win.list.selected_ids()), flush=True)
    win.queue.shutdown()
    os._exit(0)
''')


def test_a_click_on_blank_space_outside_the_list_clears_the_selection(tmp_path):
    script = tmp_path / "blank_case.py"
    script.write_text(SCRIPT, encoding="utf-8")
    appdata = tmp_path / "appdata"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata),
               HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), str(appdata), ROOT], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=90)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), "")
    assert line, r.stderr[-2000:]
    picked, left = map(int, line.split()[1:])
    assert picked == 5, "the checkboxes did not select"
    assert left == 0, "a click on blank space left %d ticked" % left
