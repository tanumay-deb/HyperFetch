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


# ---- a socks proxy saved earlier holds ordinary downloads, in words (2026-11-11) -----
# requests speaks SOCKS only with PySocks, which the build does not carry: every
# download failed as "Connection lost - Resume to retry", the real error only in
# the log. yt-dlp speaks SOCKS itself, so a video still goes.
import utils                                                      # noqa: E402
import task as T                                                  # noqa: E402

SOCKS = {"http": "socks5://127.0.0.1:1080", "https": "socks5://127.0.0.1:1080"}


@pytest.fixture
def socks(monkeypatch):
    monkeypatch.setattr(utils, "PROXIES", SOCKS)
    monkeypatch.setattr(utils, "_requests_speak_socks", lambda: False, raising=False)


def test_which_proxies_the_downloads_cannot_use(monkeypatch):
    for proxies, held in ((SOCKS, True), ({"http": "http://h:1", "https": "http://h:1"}, False),
                          ({"http": "h:1", "https": "h:1"}, False), (None, False), ({}, False)):
        monkeypatch.setattr(utils, "PROXIES", proxies)
        monkeypatch.setattr(utils, "_requests_speak_socks", lambda: False, raising=False)
        assert bool(utils.proxy_unusable()) is held, proxies
    monkeypatch.setattr(utils, "PROXIES", SOCKS)
    monkeypatch.setattr(utils, "_requests_speak_socks", lambda: True)
    assert utils.proxy_unusable() == "", "with PySocks there requests can use it"


@pytest.mark.parametrize("path", ["files/report.zip", "live/index.m3u8"])
def test_a_download_with_such_a_proxy_says_why_and_asks_nobody(socks, media_server, tmp_path,
                                                               path):
    from downloader import Downloader
    media_server.put(path, b"x" * 4096)
    t = T.DownloadTask(media_server.url(path), str(tmp_path / path.split("/")[-1]),
                       filename=path.split("/")[-1])
    Downloader(t, segments=4).run()
    assert t.status == T.ERROR
    assert "proxy" in t.error and "Settings" in t.error, t.error
    assert "Connection lost" not in t.error
    assert media_server.log == [], "a request went out around the proxy"


def test_a_video_still_goes_through_yt_dlp(socks, tmp_path, monkeypatch):
    import yt_dl
    from downloader import Downloader
    ran = []
    monkeypatch.setattr(yt_dl, "YtDlpDownloader",
                        lambda t, segments=None: type("Y", (), {"run": lambda s: ran.append(t)})())
    t = T.DownloadTask("https://www.youtube.com/watch?v=abcdefghijk", str(tmp_path / "v.mp4"),
                       filename="v.mp4")
    Downloader(t).run()
    assert ran == [t] and t.status != T.ERROR
