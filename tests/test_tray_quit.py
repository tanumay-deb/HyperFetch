"""Quitting ends the process - from the tray too.

Qt ends an application when its last *visible* window closes. From the tray
the main window is already hidden, so closing it ended nothing: the window and
the tray icon went, and HyperFetch.exe stayed in Task Manager for good."""
import json
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import utils


def _window():
    with open(os.path.join(utils.app_data_dir(), "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"welcomed": True}, f)        # no first-run dialog in the way
    from gui2.app import DownloadAppV2
    return DownloadAppV2()


def _run_until_it_ends(app, seconds=6):
    """The event loop's exit code: 0 when the app ended itself, 99 when the
    guard had to end it because it never did."""
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(lambda: app.exit(99))
    guard.start(seconds * 1000)
    try:
        return app.exec()
    finally:
        guard.stop()


def test_quit_from_the_tray_ends_the_app():
    app = QApplication.instance() or QApplication([])
    win = _window()
    win.show()
    win.hide()                                  # where "Minimize to tray" leaves it
    QTimer.singleShot(0, win._real_quit)
    assert _run_until_it_ends(app) == 0, "the app kept running after Quit from the tray"


def test_quit_with_the_window_showing_still_ends_it():
    app = QApplication.instance() or QApplication([])
    win = _window()
    win.show()
    QTimer.singleShot(0, win._real_quit)
    assert _run_until_it_ends(app) == 0
