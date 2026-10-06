"""The app fetches its own update.

Reported: "when I download the new version of the app it downloads from Chrome,
not in the app". Settings -> About -> "Download v2.8.0" opened the installer's
link in the browser. Whether it then came back to the app depended on the
browser extension catching it - installed, paired, its capture switched on -
and when it did not, a download manager had sent its own update to Chrome's
downloads. The app's log said as much: until 4 October a browser handed the
installer back, after that none handed anything.

The button now puts the installer in the app's own list, without the New
Download dialog: there is nothing to choose about it.
"""
import inspect
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gui2.dialogs import settings_pages

INSTALLER = ("https://github.com/tanumay-deb/HyperFetch/releases/download/"
             "v2.8.1/HyperFetch-2.8.1-setup.exe")
RELEASE_PAGE = "https://github.com/tanumay-deb/HyperFetch/releases/tag/v2.8.1"


class _Label:
    def __init__(self):
        self.text, self.visible = "", True

    def setText(self, t):
        self.text = t

    def setVisible(self, v):
        self.visible = v


class _Window:
    def __init__(self):
        self.added = []

    def _add_download(self, url, suggested, headers, flash=False, ask=True):
        self.added.append((url, suggested, ask))


class _About(settings_pages.PageBuilderMixin):
    """The About page's update button, without the dialog around it."""

    def __init__(self, window, url):
        self._window = window
        self._update_url = url
        self.upd_lbl, self.upd_btn = _Label(), _Label()

    def parent(self):
        return self._window


@pytest.fixture
def browser(monkeypatch):
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    return opened


def test_the_update_goes_into_the_apps_own_list(browser):
    win = _Window()
    page = _About(win, INSTALLER)

    page._download_update()

    assert win.added == [(INSTALLER, "HyperFetch-2.8.1-setup.exe", False)], (
        "not added, or the New Download dialog was asked for")
    assert browser == [], "the installer was sent to the browser"
    assert "list" in page.upd_lbl.text, "nothing says where it went"
    assert page.upd_btn.visible is False, "a second click would add it again"


def test_with_no_installer_to_fetch_the_release_page_opens(browser):
    win = _Window()
    _About(win, RELEASE_PAGE)._download_update()
    assert browser == [RELEASE_PAGE] and win.added == []


@pytest.mark.parametrize("window", [None, object()])
def test_with_no_window_to_add_to_it_goes_to_the_browser_as_before(browser, window):
    _About(window, INSTALLER)._download_update()
    assert browser == [INSTALLER]


def test_nothing_happens_without_an_update(browser):
    win = _Window()
    _About(win, "")._download_update()
    assert browser == [] and win.added == []


def test_the_button_is_wired_to_it():
    about = inspect.getsource(settings_pages.PageBuilderMixin._p_about)
    assert "self.upd_btn.clicked.connect(self._download_update)" in about
    assert "webbrowser" not in about


# ---- the window adds it without asking ---------------------------------------------
def _window(base, extras):
    from gui2.app import DownloadAppV2
    added = []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8,
                                  add_task=lambda t, start=True: added.append(t))
    win = types.SimpleNamespace(
        save_dir=str(base), segments=8, queue=queue, _extras=dict(extras),
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _save_state=lambda: None, refresh=lambda: None)
    win._quick_values = lambda *a: DownloadAppV2._quick_values(win, *a)
    return win, added, DownloadAppV2


def test_it_is_added_straight_away_even_where_downloads_are_asked_about(tmp_path, monkeypatch):
    """"Ask before adding a download" is for downloads somebody has to decide
    about. A dialog here would open behind the Settings window it came from."""
    from gui2 import app
    monkeypatch.setattr(app, "NewDownloadDialog",
                        lambda *a, **k: pytest.fail("the New Download dialog was opened"))
    win, added, App = _window(tmp_path, {"skip_new_download_dialog": False})

    App._add_download(win, INSTALLER, "HyperFetch-2.8.1-setup.exe", None, ask=False)

    assert len(added) == 1
    assert added[0].url == INSTALLER
    assert added[0].save_path == str(tmp_path / "Programs" / "HyperFetch-2.8.1-setup.exe")
