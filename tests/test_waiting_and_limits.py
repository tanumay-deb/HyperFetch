"""Old bugs from the reviews of 2026-11-01..03, fixed together.

- aria2's own limit on downloads at once was set only when Settings were
  saved, from the Main queue alone: after a restart it ran 8 whatever was
  chosen, and torrents past that waited inside it as "Downloading".
- Manage Queues' limit for Main was put back by the next Settings save.
- The ▶ on a waiting (Queued) card was "Resume", which does nothing for one
  that is not stopped.
- Twenty downloads finishing one after another opened twenty windows.
- A torrent or magnet opened while HyperFetch was starting was dropped.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import aria2d
import task as T
import utils


def _q(name, n):
    return types.SimpleNamespace(name=name, max_concurrent=n)


# ---- aria2's limit counts every queue, from the start --------------------------------
def test_aria2s_limit_counts_every_queue(monkeypatch):
    from gui2.app_settings import SettingsMixin
    told = []
    monkeypatch.setattr(aria2d, "DAEMON", types.SimpleNamespace(
        apply_concurrency=lambda: told.append(utils.MAX_CONCURRENT_DOWNLOADS)))
    monkeypatch.setattr(utils, "MAX_CONCURRENT_DOWNLOADS", 3)
    win = types.SimpleNamespace(queue=types.SimpleNamespace(
        queues={"Main": _q("Main", 6), "Torrents": _q("Torrents", 6)}))
    SettingsMixin._sync_concurrency(win)
    assert told == [12]
    assert aria2d.max_concurrent() == 16


def test_it_is_set_as_the_window_starts_and_after_manage_queues():
    import inspect
    from gui2.app import DownloadAppV2
    assert "self._sync_concurrency()" in inspect.getsource(DownloadAppV2.__init__)
    assert "self._sync_concurrency()" in inspect.getsource(DownloadAppV2._open_queues)


def test_manage_queues_keeps_mains_limit_for_settings(monkeypatch):
    import gui2.dialogs.queues as Q
    from gui2.app import DownloadAppV2
    main = _q("Main", 3)

    class Dialog:
        def __init__(self, *a):
            pass

        def exec(self):
            main.max_concurrent = 6             # changed in Manage Queues

    monkeypatch.setattr(Q, "QueueManagerDialog", Dialog)
    win = types.SimpleNamespace(
        queue=types.SimpleNamespace(queues={"Main": main}), max_concurrent=3,
        _sync_concurrency=lambda: None, _save_settings=lambda: None, refresh=lambda: None)
    DownloadAppV2._open_queues(win)
    assert win.max_concurrent == 6, "the next Settings save would put 3 back"


# ---- a waiting card's ▶ starts it ---------------------------------------------------
def test_a_waiting_cards_play_button_starts_it():
    import time
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = T.DownloadTask("https://a.example/f.zip", "C:/dl/f.zip", filename="f.zip")
    t.status = T.QUEUED
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert card._primary_action == "start" and card.btn_primary.toolTip() == "Start now"
    t.retry_after = time.time() + 60            # a torrent waiting out a retry
    card.update_task(t, 0.0)
    assert card._primary_action == "resume", "Resume is what ends the wait"


def test_start_starts_it_now():
    from gui2.app_actions import ActionsMixin
    started = []
    t = T.DownloadTask("https://a.example/f.zip", "C:/dl/f.zip")
    win = types.SimpleNamespace(
        queue=types.SimpleNamespace(get_task=lambda i: t, force_start=started.append),
        list=types.SimpleNamespace(selected_ids=lambda: set()),
        _save_state=lambda: None, refresh=lambda: None)
    win._targets = lambda x: ActionsMixin._targets(win, x)
    ActionsMixin._on_card_action(win, "start", t.id)
    assert started == [t]


# ---- one completion window at a time -----------------------------------------------
def test_one_completion_window_at_a_time(monkeypatch):
    import gui2.app as A
    opened = []

    class Signal:
        def __init__(self):
            self.slots = []

        def connect(self, f):
            self.slots.append(f)

    class Dialog:
        def __init__(self, parent, t):
            self.viewInList, self.finished = Signal(), Signal()
            opened.append(self)

        def show(self):
            pass

    monkeypatch.setattr(A, "CompleteDialog", Dialog)
    tasks = [T.DownloadTask("https://a.example/%d" % i, "C:/dl/%d" % i, filename=str(i))
             for i in range(3)]
    app = types.SimpleNamespace(
        queue=types.SimpleNamespace(tasks=tasks),
        _completed_seen=None, _errored_seen=None, _unfiled=set(),
        _extras={"categorize": False, "when_complete": "Show notification"},
        _save_state=lambda: None, _is_update=lambda t: False,
        _update_finished=lambda t: None, _maybe_categorize=lambda t: None,
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _flash_taskbar=lambda: None, tray=None, _set_filter=lambda f: None,
        _remember_skip_complete=lambda d: None)
    for t in tasks:
        t.status = T.DOWNLOADING
    A.DownloadAppV2._check_completions(app)            # takes stock
    tasks[0].status = tasks[1].status = T.COMPLETED
    A.DownloadAppV2._check_completions(app)
    assert len(opened) == 1, "a window for each one that finished"
    for f in opened[0].finished.slots:                  # it is closed
        f(0)
    tasks[2].status = T.COMPLETED
    A.DownloadAppV2._check_completions(app)
    assert len(opened) == 2


# ---- a file opened while HyperFetch starts reaches it ------------------------------
def test_a_file_opened_while_it_starts_is_handed_over(monkeypatch):
    import main
    tries = []

    def post(path, payload):
        if path == "/open":
            tries.append(payload["target"])
            return {"status": "queued"} if len(tries) >= 3 else None   # up on the third
        return None

    monkeypatch.setattr(main, "_post_running", post)
    monkeypatch.setattr(main, "_claim_single_instance", lambda *a: False)   # it holds the lock
    monkeypatch.setattr(main.sys, "argv", ["main.py", "C:/x/b.torrent"])
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert main.main() == 0
    assert tries == ["C:/x/b.torrent"] * 3, "dropped while the first instance started"


# ---- the smaller ones ------------------------------------------------------------
def test_a_view_that_matches_nothing_is_not_called_an_empty_list():
    from PySide6.QtWidgets import QApplication
    from gui2.download_list import DownloadList
    QApplication.instance() or QApplication([])
    lst = DownloadList()
    lst.set_tasks([], {}, listed=5)                  # a filter that shows none of 5
    assert lst._empty_title.text() == "Nothing here"
    lst.set_tasks([], {}, listed=0)
    assert lst._empty_title.text() == "No downloads yet"


def test_seeds_and_peers_count_the_torrents_connections_only(monkeypatch):
    import inspect
    from gui2.app import DownloadAppV2
    src = inspect.getsource(DownloadAppV2.refresh)
    assert "self.sidebar.set_stats(total_bps, peer_conns, seeds)" in src
    assert 'peer_conns += getattr(t, "tor_conns", 0)' in src


def test_a_dns_lookup_with_no_answer_is_not_asked_again_at_once(monkeypatch):
    import doh
    calls = []

    def unreachable(*a, **k):
        calls.append(1)
        raise OSError("no route to cloudflare-dns.com")

    monkeypatch.setattr(doh, "_cache", {})
    monkeypatch.setattr(doh.requests, "get", unreachable)
    assert doh._query("example.test", "A") is None
    assert doh._query("example.test", "A") is None
    assert calls == [1], "each new connection waited for the timeout again"


def test_a_typed_folder_that_is_not_there_yet_is_made(tmp_path, monkeypatch):
    from test_torrent_duplicates import _Box  # noqa: F401  (the fixture's box)
    import gui2.app as A
    win, added = _add_window(tmp_path, monkeypatch, A)
    target = tmp_path / "New folder"
    win._quick_values = lambda url, s, h: dict(A.DownloadAppV2._quick_values(win, url, s, h),
                                               save_dir=str(target))
    A.DownloadAppV2._add_download(win, "https://a.example/f.zip", "f.zip", None)
    assert target.is_dir() and os.path.dirname(added[0].save_path).startswith(str(target))


def _add_window(tmp_path, monkeypatch, A):
    added = []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8,
                                  add_task=lambda t, start=True: added.append(t))
    win = types.SimpleNamespace(
        save_dir=str(tmp_path / "default"), segments=8, queue=queue,
        _extras={"skip_new_download_dialog": True, "categorize": False},
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _save_state=lambda: None, refresh=lambda: None)
    return win, added


def test_the_web_page_groups_and_times_as_the_desktop_does():
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "web", "app.js"), encoding="utf-8").read()
    assert '["Downloading", "Queued", "Scheduled"].includes(d.status)' in page
    assert 'd.status === "Error" || d.status === "Cancelled"' in page
    assert "sec = Math.round(sec);" in page, '119.6 s read "1m 60s"'


def test_the_web_page_counts_seeds_and_peers_of_running_torrents_only():
    """As the desktop does: a paused torrent made it read "0 / 0 Seeds / Peers"
    over a list whose one running download was not a torrent (2026-11-03)."""
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "web", "app.js"), encoding="utf-8").read()
    assert 'd.isTorrent && !d.seeding && d.status === "Downloading"' in page
