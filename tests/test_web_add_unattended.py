"""A download added from the web page needs nobody at the PC.

It went into the same list as the browser's, and the window opened the New
Download dialog for it - on top of everything, on the PC - and nothing started
until somebody there pressed OK (2026-11-03). The phone heard "queued" and
never saw it come. Now the web page's add goes in as the "don't show this
dialog" path does, and a duplicate is said in a toast rather than a question
nobody is there to answer.
"""
import types
from collections import deque

from test_torrent_duplicates import _Box, window  # noqa: F401

import task as T
import web_auth
from api_server import create_app

PW = "correct horse battery"
LAN = "192.168.1.20"


class _Queue:
    def __init__(self):
        self.tasks = []

    def add_task(self, t, start=True):
        self.tasks.append(t)


def test_the_web_page_s_add_asks_nothing(tmp_path, monkeypatch):
    import utils
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    web_auth.set_password(PW)
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    pending = deque()
    c = create_app(_Queue(), str(tmp_path), pending=pending, token="tok").test_client()
    lan = {"REMOTE_ADDR": LAN}
    assert c.post("/api/login", json={"username": "admin", "password": PW},
                  environ_overrides=lan).status_code == 200
    r = c.post("/api/downloads", json={"url": "https://example.test/big.iso"},
               environ_overrides=lan)
    assert r.status_code == 200
    assert pending[0]["ask"] is False, "the add waits for a click at the PC"


def test_the_window_passes_that_on():
    from gui2.app import DownloadAppV2
    calls = []
    win = types.SimpleNamespace(pending=deque([
        {"url": "https://example.test/web.iso", "filename": "", "headers": {}, "ask": False},
        {"url": "https://example.test/ext.zip", "filename": "ext.zip", "headers": {}},
    ]), _add_download=lambda *a, **k: calls.append((a, k)))
    DownloadAppV2._drain_pending(win)
    assert calls[0][1]["ask"] is False
    assert calls[1][1]["ask"] is True, "the browser's adds still go through the dialog"


def test_a_link_already_in_the_list_is_said_not_asked(window, tmp_path):
    window.save_dir = str(tmp_path)
    window._extras["skip_new_download_dialog"] = False   # the dialog is on, as by default
    url = "https://example.test/f.zip"
    t = T.DownloadTask(url, str(tmp_path / "f.zip"), filename="f.zip")
    window.queue.tasks.append(t)
    from gui2.app import DownloadAppV2
    DownloadAppV2._add_download(window, url, "", None, ask=False)
    assert _Box.shown == [], "a question went up at the PC"
    assert window.added == []
    assert window.toasts[-1][1] == "Already in the list"


def test_a_torrent_already_in_the_list_is_said_not_asked(window, tmp_path):
    window.save_dir = str(tmp_path)
    magnet = "magnet:?xt=urn:btih:" + "c" * 40
    window.add(magnet)
    from gui2.app import DownloadAppV2
    DownloadAppV2._add_download(window, magnet + "&tr=udp://x.example:1/announce", "", None,
                                ask=False)
    assert _Box.shown == [] and len(window.added) == 1
    assert window.toasts[-1][1] == "Already in the list"


def test_a_stream_handed_to_ytdlp_from_its_master_is_not_added_again(window, tmp_path):
    """Its sound a playlist of its own, it went to yt-dlp from its master, and
    its link is the master now. The page's button sends the quality again,
    with the master beside it: that was added a second time (list #3)."""
    window.save_dir = str(tmp_path)
    master = "https://cdn.example.test/v/master.m3u8"
    quality = "https://cdn.example.test/v/720p.m3u8"
    t = T.DownloadTask(master, str(tmp_path / "Film.mp4"), filename="Film.mp4")
    t.use_ytdlp = True                          # as hls._to_ytdlp leaves it
    window.queue.tasks.append(t)
    from gui2.app import DownloadAppV2
    DownloadAppV2._add_download(window, quality, "Film.mp4", None, ask=False,
                                hls_master=master)
    assert window.added == [], "added twice"
    assert window.toasts[-1][1] == "Already in the list"
    # a stream of another master is a download of its own
    DownloadAppV2._add_download(window, "https://cdn.example.test/w/720p.m3u8", "", None,
                                ask=False, hls_master="https://cdn.example.test/w/master.m3u8")
    assert len(window.added) == 1


def test_web_add_does_not_wait_while_new_download_window_is_open(tmp_path):
    """B2: in the real window, a browser item (ask=True) opens New Download,
    and a web item (ask=False) queued behind it in pending must land in the
    list on refresh while New Download is still open."""
    import json
    import os
    import subprocess
    import sys
    import textwrap

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    appdata = tmp_path / "appdata"
    (appdata / "HyperFetch").mkdir(parents=True)
    (appdata / "HyperFetch" / "settings.json").write_text(
        json.dumps({"welcomed": True, "auto_start": False}), encoding="utf-8")
    script = tmp_path / "b2_window.py"
    script.write_text(textwrap.dedent('''
        import json, os, sys
        root = sys.argv[1]
        sys.path.insert(0, root)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        import gui2.app as appmod
        from gui2.dialogs.new_download import NewDownloadDialog

        app = QApplication([])
        appmod.run_server = lambda *a, **k: None
        win = appmod.DownloadAppV2()
        win.show()
        app.processEvents()

        win.pending.append({
            "url": "https://example.test/browser.zip",
            "filename": "browser.zip",
            "headers": {},
            "ask": True,
        })
        win.pending.append({
            "url": "https://example.test/web.iso",
            "filename": "web.iso",
            "headers": {},
            "ask": False,
        })

        def check_while_open():
            from PySide6.QtWidgets import QMessageBox
            dlgs = [w for w in win.findChildren(NewDownloadDialog) if w.isVisible()]
            while_open = [t.filename for t in win.queue.tasks]
            if dlgs:
                dlgs[0].accept()
                app.processEvents()
            after_accept = [t.filename for t in win.queue.tasks]

            # A duplicate question ("Already added") must also be shown, not
            # exec()'d, so a web add behind it does not wait either.
            win._extras["skip_new_download_dialog"] = True
            win.pending.append({
                "url": "https://example.test/browser.zip",
                "filename": "browser.zip",
                "headers": {},
                "ask": True,
            })
            win.pending.append({
                "url": "https://example.test/web2.iso",
                "filename": "web2.iso",
                "headers": {},
                "ask": False,
            })
            win.refresh()
            app.processEvents()
            boxes = [w for w in win.findChildren(QMessageBox) if w.isVisible()]
            while_dup_open = [t.filename for t in win.queue.tasks]
            if boxes:
                again_btn = next(
                    b for b in boxes[0].buttons() if b.text() == "Add Anyway")
                again_btn.click()
                app.processEvents()
            after_add_anyway = [t.filename for t in win.queue.tasks]

            print("RESULT " + json.dumps({
                "dlg_open": bool(dlgs),
                "while_open": while_open,
                "after_accept": after_accept,
                "dup_box_open": bool(boxes),
                "while_dup_open": while_dup_open,
                "after_add_anyway": after_add_anyway,
            }), flush=True)
            win.queue.shutdown()
            os._exit(0)

        QTimer.singleShot(0, win.refresh)
        QTimer.singleShot(200, check_while_open)
        app.exec()
    '''), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata),
               HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), root], env=env, cwd=root,
                       capture_output=True, text=True, timeout=90)
    line = next((l[7:] for l in r.stdout.splitlines() if l.startswith("RESULT ")), "")
    assert line, r.stdout + r.stderr[-2000:]
    out = json.loads(line)
    assert out["dlg_open"] is True, out
    assert out["while_open"] == ["web.iso"], (
        f"web item waited for New Download to be answered: {out}")
    assert out["after_accept"] == ["web.iso", "browser.zip"], out
    assert out["dup_box_open"] is True, out
    assert out["while_dup_open"] == ["web.iso", "browser.zip", "web2.iso"], out
    assert len(out["after_add_anyway"]) == 4, (
        f"pressing Add Anyway on the shown box did not add the second download: {out}")


def test_a_deleted_queue_in_new_download_falls_back_to_main(tmp_path):
    """B2: while New Download is open, Manage Queues can delete the queue it
    picked; pressing Download must fall back to Main rather than letting
    QueueManager recreate the deleted queue."""
    from gui2.app import DownloadAppV2
    from queue_manager import QueueManager
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 2},
                             {"name": "Night", "max_concurrent": 1}])
    try:
        q.delete_queue("Night")
        win = types.SimpleNamespace(
            save_dir=str(tmp_path), segments=8, queue=q, _extras={"categorize": False},
            _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
            _save_state=lambda: None, refresh=lambda: None)
        v = {
            "url": "https://example.test/report.pdf",
            "filename": "report.pdf",
            "save_dir": str(tmp_path),
            "category": "Auto",
            "queue": "Night",
            "priority": 100,
            "connections": 8,
            "start_now": False,
            "use_ytdlp": False,
            "yt_format": "",
            "headers": {},
            "skip_dialog": False,
        }
        DownloadAppV2._continue_add_download(
            win, v, v["url"], "report.pdf", ask=True)
        assert len(q.tasks) == 1
        assert q.tasks[0].queue_name == "Main"
        assert "Night" not in q.queues
    finally:
        for t in list(q.tasks):
            t.request_cancel()
        q.shutdown()
        q.wait_active(5)


def test_drain_pending_does_not_reenter_during_torrent_fetch(window, tmp_path):
    """B2: while _fetch_torrent's loop runs, the 500 ms timer firing
    _drain_pending again must not start a second add inside the first."""
    from gui2.app import DownloadAppV2
    from test_torrent_duplicates import SINTEL
    window.save_dir = str(tmp_path)
    data = open(SINTEL, "rb").read()
    url = "https://example.test/dl/Sintel.torrent"
    window.pending = deque([
        {"url": url, "filename": "Sintel.torrent", "headers": {}, "ask": False},
        {"url": url, "filename": "Sintel.torrent", "headers": {}, "ask": False},
    ])

    in_flight = [0]
    max_in_flight = [0]

    def slow_fetch(u, h):
        in_flight[0] += 1
        max_in_flight[0] = max(max_in_flight[0], in_flight[0])
        try:
            # Simulate the 500 ms timer firing _drain_pending while _fetch_torrent's
            # nested event loop is waiting for the .torrent response.
            DownloadAppV2._drain_pending(window)
            return data
        finally:
            in_flight[0] -= 1

    window._fetch_torrent = slow_fetch
    window._add_download = lambda *a, **k: DownloadAppV2._add_download(window, *a, **k)
    DownloadAppV2._drain_pending(window)
    assert max_in_flight[0] == 1, "a second add started inside the first while _drain_pending waited"
    assert len(window.added) == 1, "double-clicked .torrent link was added twice"

