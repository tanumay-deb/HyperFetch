"""Settings -> Network takes a proxy HyperFetch can use (2026-11-06).

A socks5:// one was saved as typed. Neither engine can use it: requests has
no SOCKS support in the build ("Missing dependencies for SOCKS support"), and
aria2 does not start with it - every download failed.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QPushButton, QWidget

from gui2.dialogs import settings_pages


@pytest.fixture
def page(monkeypatch):
    QApplication.instance() or QApplication([])

    class Page(settings_pages.PageBuilderMixin, QWidget):
        def __init__(self):
            QWidget.__init__(self)
            self._proxy_url, self.proxy_btn = "", QPushButton("Configure")

    p = Page()
    p.warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: p.warned.append(a[-1]))
    return p


@pytest.mark.parametrize("typed,kept", [("socks5://127.0.0.1:1080", ""),
                                        ("http://127.0.0.1:3128", "http://127.0.0.1:3128"),
                                        ("127.0.0.1:3128", "127.0.0.1:3128")])
def test_only_a_proxy_it_can_use_is_saved(page, monkeypatch, typed, kept):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (typed, True))
    page._config_proxy()
    assert page._proxy_url == kept
    assert bool(page.warned) is (kept == ""), page.warned
