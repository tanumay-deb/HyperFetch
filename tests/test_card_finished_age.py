"""A finished card says how long ago it finished (2026-11-04, list #14). It
counted from when the download was added: one that waited three days in the
queue read "3 days ago" the moment it was done."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

import task as T                                                 # noqa: E402
from gui2.download_card import DownloadCardWidget as Card        # noqa: E402


def _sub(t):
    app = QApplication.instance() or QApplication([])
    card = Card(t, 1)
    card.update_task(t, 0.0)
    app.processEvents()
    return card.sub.text()


def test_a_finished_card_counts_from_when_it_finished():
    t = T.DownloadTask("https://example.test/a.zip", "C:/dl/a.zip",
                       added=time.time() - 3 * 86400)
    t.total_size = t.downloaded = 1000
    t.status = T.COMPLETED                      # stamps completed_at: now
    assert "just now" in _sub(t) and "days ago" not in _sub(t)
    t.completed_at = time.time() - 2 * 3600
    assert "2 hrs ago" in _sub(t)


def test_one_saved_before_that_was_noted_still_says_when_it_was_added():
    t = T.DownloadTask("https://example.test/a.zip", "C:/dl/a.zip",
                       added=time.time() - 3 * 86400)
    t.total_size = t.downloaded = 1000
    t.status = T.COMPLETED
    t.completed_at = 0.0
    assert "3 days ago" in _sub(t)
