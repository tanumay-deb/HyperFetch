"""A .torrent task must survive its source file moving or disappearing.

It used to fall through to addUri() with a file PATH, which aria2 rejects as
"No URI to download." — the task ended up named just "torrent" and could never
start again.
"""
import os
import shutil

import pytest

import task as T
import torrent
import utils

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(HERE, "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")
pytestmark = pytest.mark.skipif(not os.path.isfile(REAL),
                                reason="sample torrent absent")


def _td(path, tmp_path):
    t = T.DownloadTask(path, str(tmp_path / "out"), filename="f.torrent")
    return torrent.TorrentDownloader(t)


def test_a_copy_is_kept_the_first_time(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    src = tmp_path / "mine.torrent"
    shutil.copy2(REAL, src)
    td = _td(str(src), tmp_path)

    assert td._torrent_file() == str(src)
    ih = td.t.infohash
    assert ih == "08ada5a7a6183aae1e09d831df6748d566095a10"
    assert os.path.isfile(os.path.join(torrent.metadata_dir(), ih + ".torrent"))


def test_the_copy_is_used_once_the_original_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    src = tmp_path / "mine.torrent"
    shutil.copy2(REAL, src)
    td = _td(str(src), tmp_path)
    td._torrent_file()                      # first run keeps the copy
    os.remove(src)                          # user tidies up / moves it

    got = td._torrent_file()
    assert got and os.path.isfile(got)
    assert got != str(src)


def test_a_genuinely_missing_torrent_says_so(tmp_path, monkeypatch):
    """No copy and no original: the error must name the file, not surface as
    aria2's baffling "No URI to download.\""""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    td = _td(str(tmp_path / "never.torrent"), tmp_path)
    assert td._torrent_file() == ""

    class _D:
        def call(self, *a, **k):
            raise AssertionError("must not reach aria2 with a bad path")

    with pytest.raises(FileNotFoundError) as e:
        td._rpc_add_new(_D(), {})
    assert "never.torrent" in str(e.value)


def test_a_magnet_is_unaffected(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    td = torrent.TorrentDownloader(t)
    sent = {}

    class _D:
        def call(self, method, *params, **kw):
            sent["m"] = method
            return "gid"

    assert td._rpc_add_new(_D(), {}) == "gid"
    assert sent["m"] == "aria2.addUri"
