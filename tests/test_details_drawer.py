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


def _conn_text(tmp_path, rpc_on, monkeypatch):
    monkeypatch.setattr(utils, "TORRENT_RPC", rpc_on, raising=False)
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = DetailsDrawer(host)
    t = _tor_task(tmp_path)
    drawer.open_for(t)
    app.processEvents()
    drawer._fill_conns(t, True)      # the periodic refresh fills this tab
    return drawer.conn_empty.text()
    return d.conn_empty.text()


def test_says_enable_it_only_when_it_is_off(tmp_path, monkeypatch):
    txt = _conn_text(tmp_path, False, monkeypatch)
    assert "Enable it in Settings" in txt


def test_does_not_say_enable_it_when_already_on(tmp_path, monkeypatch):
    txt = _conn_text(tmp_path, True, monkeypatch)
    assert "Enable it in Settings" not in txt, (
        "told the user to switch on a setting that is already on: " + txt)
    assert "Pause and resume" in txt, "must say how to actually fix it"
