"""file:// .torrent paths, and visible hash-check progress."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import task as T
import torrent
from test_aria2d import _FakeDaemon, _drive_with


# ------------------------------------------------------------- file:// URIs
def test_a_file_uri_resolves_to_a_path():
    """Browsers and drag-and-drop hand over file:///C:/... — os.path.isfile is
    False for that string even though the file is right there, so three of the
    user's torrents reported their .torrent as missing."""
    got = torrent.local_torrent_path(
        "file:///C:/Users/tanum/Downloads/Hannibal.Rising.2007.torrent")
    assert got.replace("\\", "/").lower() == \
        "c:/users/tanum/downloads/hannibal.rising.2007.torrent"


def test_percent_escapes_are_decoded():
    got = torrent.local_torrent_path("file:///C:/My%20Files/a%20b.torrent")
    assert "My Files" in got.replace("\\", "/")
    assert "a b.torrent" in got.replace("\\", "/")


def test_a_plain_path_is_left_alone():
    p = r"C:\Users\tanum\Downloads\x.torrent"
    assert torrent.local_torrent_path(p) == p


def test_a_magnet_is_left_alone():
    m = "magnet:?xt=urn:btih:abc"
    assert torrent.local_torrent_path(m) == m


def test_a_unc_path_survives():
    """file://server/share/x -> \\\\server\\share\\x, not a stripped path."""
    got = torrent.local_torrent_path("file://server/share/x.torrent")
    unc = got.replace("/", os.sep)
    assert unc.startswith(os.sep * 2 + "server" + os.sep + "share")


def test_a_file_uri_torrent_is_found_on_disk(tmp_path, monkeypatch):
    import shutil
    import utils
    here = os.path.dirname(os.path.abspath(__file__))
    real = os.path.join(here, "data",
                        "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")
    if not os.path.isfile(real):
        pytest.skip("sample torrent absent")
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    src = tmp_path / "mine.torrent"
    shutil.copy2(real, src)
    # as_uri(), not "file:///" + path: on POSIX the path already starts with a
    # slash, so the hand-built form produced file:////tmp/... and the extra
    # slash survived normpath as a leading "//" (POSIX keeps exactly two).
    uri = src.as_uri()
    t = T.DownloadTask(uri, str(tmp_path / "out"), filename="mine.torrent")
    td = torrent.TorrentDownloader(t)
    assert td._torrent_file() == str(src)


# --------------------------------------------------------- verify progress
def test_verification_is_reported_while_it_runs(tmp_path, monkeypatch):
    """A recheck reads the whole payload off disk. With nothing shown it is
    indistinguishable from a hang — which is exactly how it was reported."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "active", "completedLength": "0", "totalLength": "1000",
         "verifiedLength": "400", "files": [{"path": payload}]},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "d.bin"))
    seen = {}
    real_sleep = torrent.time.sleep

    def spy(_s):
        seen.setdefault("verifying", t.verifying)
        seen.setdefault("pct", t.verified_pct)
        real_sleep(0)

    monkeypatch.setattr(torrent.time, "sleep", spy)
    _drive_with(tmp_path, monkeypatch, daemon, task=t)
    assert seen.get("verifying") is True
    assert seen.get("pct") == 40
    assert t.verifying is False               # cleared once it finished


def test_the_card_says_it_is_verifying():
    QApplication.instance() or QApplication([])
    from gui2.download_card import DownloadCardWidget
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "C:/dl/x", filename="x",
                       total_size=1000)
    t.status = T.DOWNLOADING
    t.verifying = True
    t.verified_pct = 42
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert "Verifying" in card.sub.text()
    assert "42" in card.sub.text()
