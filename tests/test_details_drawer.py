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
        def call(self, method, *a, **kw):
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
        def call(self, method, *a, **kw):
            return [{"ip": "203.0.113.5", "port": "6881", "seeder": "true",
                     "bitfield": "ff" * 125 + "80", "downloadSpeed": "2000", "uploadSpeed": "0"},
                    {"ip": "203.0.113.6", "port": "6881", "seeder": "false",
                     "bitfield": "ff" * 63 + "00" * 63, "downloadSpeed": "1000", "uploadSpeed": "0"}]

    monkeypatch.setattr(aria2d, "DAEMON", _Daemon())
    QApplication.instance() or QApplication([])
    drawer = DetailsDrawer(QWidget())
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x")
    t.gid = "gid1"
    rows = drawer._peer_rows(t)
    assert [(label.endswith("seed"), pct) for label, pct, *_ in rows] == [(True, 100), (False, 50)]


def test_a_peer_says_how_much_it_has(monkeypatch):
    """Asked 2026-09-30, how much of a torrent each peer holds - in bytes, and
    its percent counted over the torrent's own pieces, not the padded field
    (a peer missing only the last of 1001 pieces read 99% on 1008 bits too,
    but one with 1001 of 1001 read 99% unless it said it was a seed)."""
    import aria2d
    from gui.theme import human_size
    MB = 1 << 20

    class _Daemon:
        def call(self, method, *a, **kw):
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
