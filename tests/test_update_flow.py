"""A new version: told about it, fetched here, checked, installed.

The app only ever found out about a new version when somebody opened Settings
-> About and pressed Check for Updates. A copy left running for a month kept
its month-old yt-dlp, which is what stops YouTube downloads, and said nothing.

Now it looks once after it starts and once a day (Settings -> General can
switch that off), says so in the sidebar, downloads the installer into its own
list, checks it against the SHA-256 GitHub publishes for the release, and
offers to run it.
"""
import hashlib
import io
import json
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import task as T
import updater
from gui2 import app_update
from gui2.app_update import UpdateMixin

URL = ("https://github.com/tanumay-deb/HyperFetch/releases/download/"
       "v2.8.1/HyperFetch-2.8.1-setup.exe")
PAGE = "https://github.com/tanumay-deb/HyperFetch/releases/tag/v2.8.1"
BODY = b"the installer, as far as a checksum cares"
SHA = hashlib.sha256(BODY).hexdigest()


# ---- what GitHub says about the release ---------------------------------------
def _github(monkeypatch, assets):
    payload = json.dumps({"tag_name": "v2.8.1", "html_url": PAGE, "assets": assets}).encode()

    class _Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout=0: _Reply(payload))


def test_the_latest_release_says_where_its_installer_is_and_what_it_hashes_to(monkeypatch):
    _github(monkeypatch, [
        {"name": "HyperFetch-2.8.1-portable.zip", "browser_download_url": "https://x/p.zip",
         "digest": "sha256:" + "0" * 64},
        {"name": "HyperFetch-2.8.1-setup.exe", "browser_download_url": URL,
         "digest": "sha256:" + SHA}])
    info = updater._fetch_latest("tanumay-deb/HyperFetch")
    assert info["tag"] == "v2.8.1" and info["url"] == PAGE
    assert info["installer"] == URL
    assert info["sha256"] == SHA


def test_a_release_with_no_installer_still_has_its_page(monkeypatch):
    _github(monkeypatch, [{"name": "notes.txt", "browser_download_url": "https://x/n.txt"}])
    info = updater._fetch_latest("tanumay-deb/HyperFetch")
    assert info["url"] == PAGE and not info.get("installer")


def test_the_installer_and_its_checksum_reach_whoever_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(updater, "_fetch_latest", lambda _: {
        "tag": "v2.8.1", "url": PAGE, "installer": URL, "sha256": SHA})
    first = updater.check_for_update("2.8.0", force=True)
    again = updater.check_for_update("2.8.0")           # from the cache this time
    for info in (first, again):
        assert info["available"] and info["installer"] == URL and info["sha256"] == SHA


def test_a_file_is_checked_against_the_releases_checksum(tmp_path):
    good = tmp_path / "setup.exe"
    good.write_bytes(BODY)
    assert updater.file_matches(str(good), SHA) is True
    assert updater.file_matches(str(good), SHA.upper()) is True
    assert updater.file_matches(str(good), "0" * 64) is False
    assert updater.file_matches(str(tmp_path / "gone.exe"), SHA) is False


# ---- the window ---------------------------------------------------------------
class _Sidebar:
    def __init__(self):
        self.said = []

    def set_update(self, text, enabled=True):
        self.said.append((text, enabled))


class _Window(UpdateMixin):
    def __init__(self, extras=None, info=None):
        self._extras = dict(extras or {})
        self.sidebar = _Sidebar()
        self.toasts, self.added, self.asked, self.ran = [], [], [], []
        self.answer, self.quits = True, 0
        self._toasts = types.SimpleNamespace(
            show=lambda kind, title, msg="", **k: self.toasts.append((kind, title)))
        self._init_update()
        self._update_info = info

    def _add_download(self, url, suggested, headers, flash=False, ask=True):
        self.added.append((url, suggested, ask))

    def _real_quit(self):
        self.quits += 1

    def _ask_install(self, version):
        self.asked.append(version)
        return self.answer

    def _run_installer(self, path):
        self.ran.append(path)


INFO = {"available": True, "version": "v2.8.1", "url": PAGE, "installer": URL, "sha256": SHA}


def _done(tmp_path, body=BODY):
    p = tmp_path / "HyperFetch-2.8.1-setup.exe"
    p.write_bytes(body)
    t = T.DownloadTask(URL, str(p), filename=p.name)
    t.status = T.COMPLETED
    return t


def test_it_looks_a_little_after_starting_and_then_once_a_day():
    w = _Window()
    start = w._update_started
    assert not w._update_due(start + 1), "looked before the window had settled"
    assert w._update_due(start + app_update.CHECK_AFTER + 1)
    w._update_looked_at = start + 100
    assert not w._update_due(start + 100 + 3600)
    assert w._update_due(start + 100 + app_update.CHECK_EVERY + 1)


def test_it_does_not_look_when_that_is_switched_off():
    w = _Window({"auto_update_check": False})
    assert not w._update_due(w._update_started + 10 * app_update.CHECK_EVERY)


def test_a_new_version_is_announced_once(monkeypatch):
    w = _Window(info=INFO)
    monkeypatch.setattr(w, "_update_due", lambda now: False)
    w._update_tick()
    w._update_tick()
    assert w.sidebar.said == [("Update to 2.8.1", True)]
    assert len(w.toasts) == 1 and "2.8.1" in w.toasts[0][1]


def test_being_up_to_date_says_nothing(monkeypatch):
    w = _Window(info={"available": False, "version": "v2.8.0", "url": PAGE})
    monkeypatch.setattr(w, "_update_due", lambda now: False)
    w._update_tick()
    assert w.sidebar.said == [] and w.toasts == []


def test_the_button_puts_the_installer_in_the_apps_own_list():
    w = _Window(info=INFO)
    w._on_update_clicked()
    w._on_update_clicked()                       # an impatient second click
    assert w.added == [(URL, "HyperFetch-2.8.1-setup.exe", False)]
    assert w.sidebar.said[-1] == ("Downloading the update…", False)


def test_with_no_installer_attached_the_release_page_opens(monkeypatch):
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u, *a, **k: opened.append(u))
    w = _Window(info=dict(INFO, installer=""))
    w._on_update_clicked()
    assert opened == [PAGE] and w.added == []


def test_which_download_is_the_update():
    w = _Window(info=INFO)
    other = T.DownloadTask("https://example.test/a.zip", "a.zip")
    assert not w._is_update(other)
    w._on_update_clicked()
    assert w._is_update(T.DownloadTask(URL, "x.exe")) and not w._is_update(other)


def test_a_finished_update_is_checked_and_offered(tmp_path):
    w = _Window(info=INFO)
    w._on_update_clicked()
    t = _done(tmp_path)

    w._update_finished(t)

    assert w.asked == ["2.8.1"]
    assert w.ran == [t.save_path], "Install now did not run the installer"
    assert w.quits == 1, "the app has to close, or the installer waits for it"


def test_later_keeps_it_one_click_away(tmp_path):
    w = _Window(info=INFO)
    w.answer = False
    w._on_update_clicked()
    t = _done(tmp_path)

    w._update_finished(t)

    assert w.ran == [] and w.quits == 0
    assert w.sidebar.said[-1] == ("Install 2.8.1", True)
    w._on_update_clicked()                       # ...and now it is wanted
    assert w.ran == [t.save_path] and w.quits == 1
    assert len(w.added) == 1, "downloaded a second time"


def test_an_installer_that_is_not_the_releases_file_is_not_run(tmp_path):
    w = _Window(info=INFO)
    w._on_update_clicked()
    t = _done(tmp_path, body=b"something else entirely")

    w._update_finished(t)

    assert w.asked == [] and w.ran == [] and w.quits == 0
    assert w.toasts[-1][0] == "error"
    assert w.sidebar.said[-1] == ("Update to 2.8.1", True), "no way left to try again"
    w._on_update_clicked()
    assert len(w.added) == 2


def test_a_release_with_no_checksum_is_still_offered(tmp_path):
    """Older releases, or an answer from before the checksum was kept."""
    w = _Window(info={k: v for k, v in INFO.items() if k != "sha256"})
    w._on_update_clicked()
    w._update_finished(_done(tmp_path))
    assert w.asked == ["2.8.1"]


# ---- the pieces it is wired to --------------------------------------------------
def test_the_sidebar_shows_the_button_only_when_there_is_something_to_say():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui2.sidebar import Sidebar
    s = Sidebar()
    assert s.btn_update.isHidden()
    clicks = []
    s.updateClicked.connect(lambda: clicks.append(1))
    s.set_update("Update to 2.8.1")
    assert not s.btn_update.isHidden() and "2.8.1" in s.btn_update.text()
    s.btn_update.click()
    assert clicks == [1]
    s.set_update("Downloading the update…", enabled=False)
    assert not s.btn_update.isEnabled()
    s.set_update("")
    assert s.btn_update.isHidden()
    del app


def test_a_main_button_that_is_switched_off_looks_switched_off():
    """"Downloading the update…" cannot be pressed. The rule for a disabled
    button lost to the main button's own colours, so it looked as if it could:
    seen by drawing the sidebar."""
    from gui2 import palette
    rule = palette.qss().split("QPushButton#primary:disabled {", 1)
    assert len(rule) == 2, "a disabled main button has no look of its own"
    assert "background" in rule[1].split("}", 1)[0]


def test_the_window_has_it_wired_in():
    import inspect
    from gui2 import app
    assert issubclass(app.DownloadAppV2, UpdateMixin)
    src = inspect.getsource(app.DownloadAppV2)
    assert "self.sidebar.updateClicked.connect(self._on_update_clicked)" in src
    assert "self._update_tick()" in inspect.getsource(app.DownloadAppV2.refresh)
    done = inspect.getsource(app.DownloadAppV2._check_completions)
    assert "self._is_update(t)" in done and "self._update_finished(t)" in done


def test_settings_has_the_switch_and_about_uses_the_same_look():
    import inspect
    from gui2.dialogs import settings, settings_pages
    general = inspect.getsource(settings_pages.PageBuilderMixin._p_general)
    assert "auto_update_check" in general and "Check for updates automatically" in general
    assert '"auto_update_check"' in inspect.getsource(settings.SettingsDialogV2.values)
    check = inspect.getsource(settings_pages.PageBuilderMixin._check_updates)
    assert "updater.check_for_update" in check and "api.github.com" not in check


@pytest.mark.parametrize("page", ["PRIVACY.md", "docs/privacy.html"])
def test_the_privacy_policy_says_when_github_is_asked(page):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = " ".join(open(os.path.join(root, *page.split("/")), encoding="utf-8").read().split())
    assert "only when you press Check for Updates" not in text
    assert "once a day" in text and "Check for updates automatically" in text
