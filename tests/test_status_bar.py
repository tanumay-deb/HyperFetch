"""The thin bar along the bottom of the window (asked 2026-10-29): what this
session has downloaded and uploaded, the version, and a button that switches
between dark and light (tests/test_reskin.py: the switch itself)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

import task as T
import torrent
from gui.theme import APP_VERSION, human_size
from gui2 import palette
from gui2.status_bar import SessionCount, StatusBar
from test_aria2d import _FakeDaemon, _drive_with


def _t(downloaded=0, status=T.DOWNLOADING):
    t = T.DownloadTask("https://example.com/f.bin", "C:/dl/f.bin")
    t.downloaded, t.status = downloaded, status
    return t


def test_what_downloads_gain_while_they_run_is_counted():
    a, b = _t(500), _t(0)                  # a came back from before with 500 bytes
    s = SessionCount()
    s.tick([a, b])
    a.downloaded, b.downloaded = 800, 100
    s.tick([a, b])
    assert s.down == 400
    b.downloaded = 30                      # a video starts its sound: not taken off
    s.tick([a, b])
    b.downloaded = 60
    s.tick([a, b])
    assert s.down == 430


def test_a_change_while_paused_and_a_recheck_are_not_downloading():
    t = _t(1000)
    s = SessionCount()
    s.tick([t])
    t.status, t.downloaded = T.PAUSED, 600          # files unticked while paused
    s.tick([t])
    t.status, t.verifying, t.downloaded = T.DOWNLOADING, True, 0
    s.tick([t])
    t.verifying, t.downloaded = False, 5000          # what the check found on disk
    s.tick([t])
    t.downloaded = 5100
    s.tick([t])
    assert s.down == 100


def test_a_removed_download_keeps_what_it_brought():
    t = _t(0)
    s = SessionCount()
    s.tick([t])
    t.downloaded = 700
    s.tick([t])
    s.tick([])
    assert s.down == 700


def test_what_torrents_send_counts_from_their_first_poll(tmp_path, monkeypatch):
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": "400", "totalLength": "1000",
         "uploadLength": "5000", "files": [{"path": payload}]},
        {"status": "active", "completedLength": "600", "totalLength": "1000",
         "uploadLength": "5300", "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "uploadLength": "5900", "files": [{"path": payload}]},
    ])
    before = torrent.sent_this_session()
    _drive_with(tmp_path, monkeypatch, daemon)
    assert torrent.sent_this_session() - before == 900, "5000 was sent before this run"


def test_the_bar_says_it_and_offers_the_other_theme():
    QApplication.instance() or QApplication([])
    bar = StatusBar()
    bar.set_totals(3 << 20, 5 << 10)
    assert bar.totals.text() == f"↓ {human_size(3 << 20)}    ↑ {human_size(5 << 10)}"
    assert f"v{APP_VERSION}" in [w.text() for w in bar.findChildren(QLabel)]
    other = "light" if palette.active_theme() == "dark" else "dark"
    assert other in bar.theme_btn.toolTip()


def test_the_details_panel_and_toasts_stay_above_it():
    from gui2.details_drawer import DetailsDrawer
    from gui2.toast import ToastManager
    QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(900, 600)
    host.status_bar = StatusBar(host)
    drawer = DetailsDrawer(host)
    drawer.reposition()
    assert drawer.height() == 600 - host.status_bar.height()
    toasts = ToastManager(host)
    toasts.show("info", "Download added", "x.bin")
    toasts.reposition()
    assert toasts.items[-1].geometry().bottom() < 600 - host.status_bar.height()
