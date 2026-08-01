"""ETA column on the main download list.

It used to appear only for non-torrent downloads, inside the sub-text line.
Now it is its own fixed-width right-hand column and works for torrents too.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import task as T
from gui2.download_card import DownloadCardWidget as DownloadCard


def _app():
    return QApplication.instance() or QApplication([])


def _task(status=T.DOWNLOADING, total=1000, done=200, url="https://x/f.zip"):
    t = T.DownloadTask(url, "C:/dl/f.zip", filename="f.zip", total_size=total)
    t.downloaded = done
    t.status = status
    return t


def test_eta_is_shown_for_a_live_download():
    _app()
    t = _task()                       # 800 bytes left at 100 B/s -> 8s
    card = DownloadCard(t, 1)
    card.update_task(t, 100.0)
    assert card.eta_cap.text() == "ETA"
    assert card.eta_val.text()


def test_eta_is_shown_for_torrents_too():
    """Torrents previously showed peers/seeds and never an ETA."""
    _app()
    t = _task(url="magnet:?xt=urn:btih:abc&dn=Movie")
    card = DownloadCard(t, 1)
    card.update_task(t, 100.0)
    assert card.eta_val.text(), "torrent rows still have no ETA"


def test_no_eta_without_a_speed():
    """A paused row must not keep the estimate it had while running."""
    _app()
    t = _task()
    card = DownloadCard(t, 1)
    card.update_task(t, 100.0)
    assert card.eta_val.text()
    t.status = T.PAUSED
    card.update_task(t, 0.0)
    assert card.eta_val.text() == ""
    assert card.eta_cap.text() == ""


def test_no_eta_before_torrent_metadata_arrives():
    """No total size means no honest estimate."""
    _app()
    t = _task(total=0, done=0, url="magnet:?xt=urn:btih:abc")
    card = DownloadCard(t, 1)
    card.update_task(t, 500.0)
    assert card.eta_val.text() == ""


def test_completed_row_shows_no_eta():
    _app()
    t = _task(status=T.COMPLETED, total=1000, done=1000)
    card = DownloadCard(t, 1)
    card.update_task(t, 0.0)
    assert card.eta_val.text() == ""


def test_the_column_keeps_its_width_so_rows_stay_aligned():
    """If the column collapsed when empty, the action buttons would shuffle
    left on every row that is not downloading."""
    _app()
    live = DownloadCard(_task(), 1)
    idle = DownloadCard(_task(status=T.PAUSED), 2)
    assert live.eta_box.width() == idle.eta_box.width()


def test_eta_is_not_repeated_in_the_subtext():
    _app()
    t = _task()
    card = DownloadCard(t, 1)
    card.update_task(t, 100.0)
    assert "ETA" not in card.sub.text()
