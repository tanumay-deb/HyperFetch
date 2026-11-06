"""Settings -> About (2026-11-04, list #12 and #13)."""
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


def test_a_page_let_go_after_checking_does_not_take_the_app_down(app, monkeypatch):
    """The answer was taken by a closure over the page and its timer: that
    cycle, collected, tore the page down under Qt, and the next event loop
    aborted the interpreter (CI, 2026-11-06)."""
    import gc
    import updater
    monkeypatch.setattr(updater, "check_for_update", lambda v, force=False: {"available": False})

    def check_and_let_go():
        page = _About()
        page._check_updates()
        end = time.monotonic() + 10
        while page.upd_lbl.text() == "Checking…" and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    check_and_let_go()
    gc.collect()
    from gui2.download_card import DownloadCardWidget
    import task as T
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, "C:/dl/Show")
    DownloadCardWidget(t, 1).update_task(t, 0.0)
    app.processEvents()                         # where it aborted


@pytest.mark.parametrize("button,says", [("License", "MIT License"), ("Credits", "aria2")])
def test_license_and_credits_show_what_they_say(app, monkeypatch, button, says):
    """Both buttons were there, connected to nothing."""
    from gui2.dialogs.settings import SettingsDialogV2
    shown = []
    monkeypatch.setattr(settings_pages.PageBuilderMixin, "_show_text",
                        lambda self, title, text, rich=False: shown.append((title, text)))
    dlg = SettingsDialogV2(None, extras={}, save_dir=".", max_concurrent=3, segments=8,
                           verify_tls=True, pair_token="T", theme="Dark", accent="purple",
                           sched_en=False, sched_start="00:00", sched_stop="06:00")
    try:
        next(b for b in dlg.findChildren(QPushButton) if b.text() == button).click()
    finally:
        dlg.deleteLater()
    assert len(shown) == 1 and shown[0][0] == button and says in shown[0][1], shown


def test_the_build_ships_the_license_it_shows():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = open(os.path.join(root, "HyperFetch.spec"), encoding="utf-8").read()
    assert "('LICENSE', '.')" in spec
