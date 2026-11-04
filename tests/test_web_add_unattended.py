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
