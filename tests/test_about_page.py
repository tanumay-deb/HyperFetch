"""Settings -> About (2026-11-04, list #13)."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from gui2.dialogs import settings_pages


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _About(settings_pages.PageBuilderMixin, QWidget):
    """The About page's update check, without the dialog around it."""

    def __init__(self):
        QWidget.__init__(self)
        self.upd_lbl, self.upd_btn = QLabel(), QPushButton()
        self._update_url = ""


def test_checking_for_updates_leaves_the_window_free(app, monkeypatch):
    """GitHub was asked on the window's thread: Settings froze until it
    answered, up to 6 s."""
    import updater
    asked = []

    def slow(version, force=False):
        asked.append(force)
        time.sleep(1.5)
        return {"available": False}

    monkeypatch.setattr(updater, "check_for_update", slow)
    page = _About()
    t0 = time.monotonic()
    page._check_updates()
    page._check_updates()                       # pressed again while it asks
    assert time.monotonic() - t0 < 0.5, "the window waited for GitHub"
    assert page.upd_lbl.text() == "Checking…"
    end = time.monotonic() + 10
    while page.upd_lbl.text() == "Checking…" and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert page.upd_lbl.text() == "You are on the latest version."
    assert asked == [True], "asked twice"
