"""Card serial numbers.

Cards are recycled across refreshes, so the number belongs to the row being
shown, not to the widget. Caching it at construction gave several rows the same
number — the list showed #5, #5, #5, #3, #1.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import task as T
from gui2.download_card import DownloadCardWidget


def _app():
    return QApplication.instance() or QApplication([])


def _task(status=T.DOWNLOADING, name="a.zip"):
    t = T.DownloadTask("https://x/" + name, "C:/dl/" + name, filename=name,
                       total_size=1000)
    t.downloaded = 400
    t.status = status
    return t


def test_a_recycled_card_takes_the_new_number():
    """The regression: update_task rewrote the label from the constructor's
    value, undoing the list's assignment."""
    _app()
    t = _task()
    card = DownloadCardWidget(t, 5)
    card.set_serial(2)
    card.update_task(t, 0.0)              # must not revert to #5
    assert card.sl_lbl.text() == "#2"


def test_distinct_cards_keep_distinct_numbers():
    _app()
    cards = []
    for i in range(1, 4):
        t = _task(name=f"f{i}.zip")
        c = DownloadCardWidget(t, 99)     # all constructed with the same number
        c.set_serial(i)
        c.update_task(t, 0.0)
        cards.append(c)
    assert [c.sl_lbl.text() for c in cards] == ["#1", "#2", "#3"]


def test_a_completed_row_shows_no_number():
    _app()
    t = _task(status=T.COMPLETED)
    t.downloaded = t.total_size
    card = DownloadCardWidget(t, 3)
    card.update_task(t, 0.0)
    assert card.sl_lbl.text() == ""


def test_renumbering_a_completed_row_keeps_it_blank():
    """Its position still changes as rows above it are removed; it just must
    not start displaying one."""
    _app()
    t = _task(status=T.COMPLETED)
    t.downloaded = t.total_size
    card = DownloadCardWidget(t, 3)
    card.update_task(t, 0.0)
    card.set_serial(7)
    assert card.sl_lbl.text() == ""


def test_the_column_keeps_its_width_when_blank():
    _app()
    live = DownloadCardWidget(_task(), 1)
    done = _task(status=T.COMPLETED)
    done.downloaded = done.total_size
    fin = DownloadCardWidget(done, 2)
    fin.update_task(done, 0.0)
    assert live.sl_lbl.width() == fin.sl_lbl.width()
