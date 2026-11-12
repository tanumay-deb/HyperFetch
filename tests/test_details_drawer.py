import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import task as T
import utils
from gui2.details_drawer import DetailsDrawer


def test_opening_drawer_populates_structured_headers_without_crashing():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    task = T.DownloadTask(
        "https://example.test/file.zip",
        "C:/Downloads/file.zip",
        headers={"Referer": "https://example.test/"},
    )
    task.status = T.DOWNLOADING

    drawer.open_for(task)
    app.processEvents()

    # the tab now shows Request AND Response sections, so assert on content
    # rather than a fixed row count
    names = [drawer.h_table.item(r, 0).text()
             for r in range(drawer.h_table.rowCount())]
    assert "Referer" in names
    assert any(n.startswith("— Request") for n in names)
    assert any(n.startswith("— Response") for n in names)
    # nothing has connected yet, so the response side says so instead of lying
    assert any("captured once" in n for n in names)
    assert any("Downloading" in line for line in drawer._log_lines)


def test_response_headers_are_shown_once_captured():
    app = QApplication.instance() or QApplication([])
    # keep a Python reference to the host: passing QWidget() inline lets it be
    # collected, and the drawer's parent then raises "C++ object already deleted"
    host = QWidget()
    drawer = DetailsDrawer(host)
    task = T.DownloadTask("https://example.test/f.zip", "C:/Downloads/f.zip",
                          headers={"Referer": "https://example.test/"})
    task.response_headers = {"Content-Type": "application/zip",
                             "X-Served-By": "edge-1"}
    task.response_status = 206
    task.remote_address = "203.0.113.7:443"
    task.status = T.DOWNLOADING

    drawer.open_for(task)
    app.processEvents()
    rows = {drawer.h_table.item(r, 0).text(): drawer.h_table.item(r, 1).text()
            for r in range(drawer.h_table.rowCount())}
    assert rows.get("X-Served-By") == "edge-1"
    assert any("HTTP 206" in n and "203.0.113.7" in n for n in rows)


# --- the Connections tab must not tell you to enable what is already on -----
# Real report: "Shared torrent engine (beta)" was ON in Settings, yet the
# Connections tab still said "Enable it in Settings -> Advanced." The gate is
# the task's gid (only set when a torrent runs on the daemon), and a torrent
# started BEFORE the toggle was flipped has none — so the advice was wrong,
# not the state.

def _tor_task(tmp_path):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "out"))
    t.status = T.DOWNLOADING
    t.gid = None                       # not on the shared daemon
    return t


def _conn_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = _tor_task(tmp_path)
    drawer.open_for(t)
    app.processEvents()
    drawer._fill_conns(t, True)      # the periodic refresh fills this tab
    return drawer.conn_empty.text()
    return d.conn_empty.text()


def test_no_gid_says_the_engine_has_not_picked_it_up_yet(tmp_path):
    """There is one engine now, so the old "enable the shared engine" advice
    is gone. No gid simply means the daemon is not holding this torrent yet.
    """
    txt = _conn_text(tmp_path)
    assert "Enable it in Settings" not in txt, txt
    assert "Waiting for the torrent engine" in txt, txt


def test_peer_polling_uses_a_short_timeout(monkeypatch):
    import aria2d
    from gui2 import details_drawer as dd

    seen = {}

    class _Daemon:
        def quick_call(self, method, *a, **kw):
            seen["method"] = method
            seen["timeout"] = kw.get("timeout")
            return []

    monkeypatch.setattr(aria2d, "DAEMON", _Daemon())
    app = QApplication.instance() or QApplication([])
    drawer = DetailsDrawer(QWidget())
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.gid = "gid1"
    drawer._peer_rows(t)
    app.processEvents()

    assert seen.get("method") == "aria2.getPeers"
    assert seen.get("timeout") is not None, \
        "no timeout passed — the GUI can block for the full CALL_TIMEOUT"
    assert seen["timeout"] <= 3.0, (
        f"{seen['timeout']}s on the GUI thread is long enough to look like a "
        "hang; aria2 blocks RPC while it works")
    assert dd.GUI_RPC_TIMEOUT <= 3.0


def test_a_seed_reads_100_percent(monkeypatch):
    """aria2 pads a peer's bitfield to whole bytes: 1001 pieces are 1008 bits,
    and counting the 7 spare bits as missing pieces put "99%" against every
    seed (reported 2026-10-29 with a screenshot)."""
    import aria2d

    class _Daemon:
        def quick_call(self, method, *a, **kw):
            return [{"ip": "203.0.113.5", "port": "6881", "seeder": "true",
                     "bitfield": "ff" * 125 + "80", "downloadSpeed": "2000", "uploadSpeed": "0"},
                    {"ip": "203.0.113.6", "port": "6881", "seeder": "false",
                     "bitfield": "ff" * 63 + "00" * 63, "downloadSpeed": "1000", "uploadSpeed": "0"}]

    monkeypatch.setattr(aria2d, "DAEMON", _Daemon())
    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.gid = "gid1"
    rows = drawer._peer_rows(t)
    assert [(label.endswith("seed"), pct) for label, pct, *_ in rows] == [(True, 100), (False, 50)]


def test_a_finished_torrent_with_nobody_connected_says_why(monkeypatch):
    """Seeding with nobody downloading from it, the tab said "Resume the
    download to see connections" (asked 2026-10-30: why can't we see whom we
    seed to?) - and so did a finished torrent that is not seeding."""
    import aria2d

    class _Daemon:
        def quick_call(self, method, *a, **kw):
            return []

    monkeypatch.setattr(aria2d, "DAEMON", _Daemon())
    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.status, t.seeding, t.gid = T.COMPLETED, True, "gid1"
    drawer._fill_conns(t, True)
    assert drawer.conn_empty.text().startswith("Seeding"), drawer.conn_empty.text()
    t.seeding, t.gid = False, None
    drawer._fill_conns(t, True)
    assert drawer.conn_empty.text().startswith("Finished"), drawer.conn_empty.text()


def test_a_peer_says_how_much_it_has(monkeypatch):
    """Asked 2026-09-30, how much of a torrent each peer holds - in bytes, and
    its percent counted over the torrent's own pieces, not the padded field
    (a peer missing only the last of 1001 pieces read 99% on 1008 bits too,
    but one with 1001 of 1001 read 99% unless it said it was a seed)."""
    import aria2d
    from gui.theme import human_size
    MB = 1 << 20

    class _Daemon:
        def quick_call(self, method, *a, **kw):
            return [{"ip": "203.0.113.5", "port": "6881", "seeder": "false",
                     "bitfield": "ff" * 125 + "80", "downloadSpeed": "3000", "uploadSpeed": "0"},
                    {"ip": "203.0.113.6", "port": "6881", "seeder": "false",
                     "bitfield": "ff" * 125 + "00", "downloadSpeed": "2000", "uploadSpeed": "0"}]

    monkeypatch.setattr(aria2d, "DAEMON", _Daemon())
    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.gid = "gid1"
    t.tor_pieces = (1001, MB)
    t.files = [["Show/a.mkv", 1000 * MB + 5]]       # the last piece is 5 bytes
    drawer._fill_conns(t, True)
    shown = [drawer.conns_table.item(r, 1).text() for r in range(drawer.conns_table.rowCount())]
    assert shown == ["100% · " + human_size(1000 * MB + 5), "99% · " + human_size(1000 * MB)]


def test_the_windows_calls_skip_the_slow_liveness_check(monkeypatch):
    """call() runs ensure(), whose probe waits 10 s on a lock the torrent
    threads share: the window froze while aria2 was busy, short timeout or not."""
    import aria2d
    d = aria2d.Aria2Daemon.__new__(aria2d.Aria2Daemon)
    d.port, d.secret = 1, "s"
    monkeypatch.setattr(d, "ensure", lambda: (_ for _ in ()).throw(AssertionError("ensure")),
                        raising=False)
    sent = []
    monkeypatch.setattr(d, "_post", lambda *a: sent.append(a) or [], raising=False)
    assert d.quick_call("aria2.getPeers", "g", timeout=1.5) == []
    assert sent and sent[0][2] == "aria2.getPeers" and sent[0][4] == 1.5


def test_a_failed_peer_ask_waits_before_the_next(monkeypatch):
    import aria2d
    asked = []

    class _Busy:
        def quick_call(self, *a, **kw):
            asked.append(1)
            raise TimeoutError("busy")

    monkeypatch.setattr(aria2d, "DAEMON", _Busy())
    app = QApplication.instance() or QApplication([])
    drawer = DetailsDrawer(QWidget())
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.gid = "gid1"
    for _ in range(5):                     # five half-second ticks
        drawer._avail_counts(t, 8)
    assert asked == [1]


def test_queued_task_in_drawer_shows_start_now_and_emits_start():
    """A Queued task not waiting out a retry shows 'Start now' in the drawer
    and emits 'start' (force_start) instead of 'resume' (which QueueManager
    ignores for a queued task)."""
    import time
    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = T.DownloadTask("https://example.test/file.bin", "C:/dl/file.bin")
    t.status = T.QUEUED
    emitted = []
    drawer.action.connect(lambda act, tid: emitted.append((act, tid)))
    drawer.open_for(t)
    assert "Start now" in drawer.btn_primary.text()
    drawer.btn_primary.click()
    assert emitted == [("start", t.id)]

    # One waiting out a retry keeps Resume
    emitted.clear()
    t.retry_after = time.time() + 120
    drawer.update_live(t, 0.0)
    assert "Resume" in drawer.btn_primary.text()
    drawer.btn_primary.click()
    assert emitted == [("resume", t.id)]

