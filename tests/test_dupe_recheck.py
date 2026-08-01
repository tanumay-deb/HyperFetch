"""Duplicate detection across add methods, and Force Recheck for torrents."""
import os

import pytest

import task as T
import torrent
from test_aria2d import _FakeDaemon, _drive_with

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_TORRENT = os.path.join(HERE, "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")


# ----------------------------------------------------------------- infohash
@pytest.mark.skipif(not os.path.isfile(REAL_TORRENT), reason="sample torrent absent")
def test_infohash_of_a_real_torrent_file():
    """Hashed from the raw info-dict bytes; this file is named after its own
    infohash, so it checks itself."""
    assert torrent.torrent_infohash(REAL_TORRENT) == \
        "08ada5a7a6183aae1e09d831df6748d566095a10"


@pytest.mark.skipif(not os.path.isfile(REAL_TORRENT), reason="sample torrent absent")
def test_a_magnet_and_its_torrent_file_are_the_same_download():
    """The gap that was missing: adding the .torrent for a magnet already in the
    list produced a second task fighting over the same files."""
    ih = torrent.torrent_infohash(REAL_TORRENT)
    assert torrent.infohash_for(REAL_TORRENT) == ih
    assert torrent.infohash_for(f"magnet:?xt=urn:btih:{ih}&dn=Sintel") == ih


def test_infohash_ignores_the_display_name_and_trackers():
    a = "magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01&dn=One"
    b = ("magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01&dn=Two"
         "&tr=udp%3A%2F%2Fx%3A80")
    assert torrent.infohash_for(a) == torrent.infohash_for(b)


def test_infohash_of_a_non_torrent_is_empty():
    assert torrent.infohash_for("https://example.test/file.zip") == ""
    assert torrent.infohash_for("") == ""


def test_a_missing_or_junk_torrent_file_does_not_raise(tmp_path):
    junk = tmp_path / "bad.torrent"
    junk.write_bytes(b"not bencode at all")
    assert torrent.torrent_infohash(str(junk)) == ""
    assert torrent.torrent_infohash(str(tmp_path / "nope.torrent")) == ""


# ------------------------------------------------------------ force recheck
def _task(tmp_path):
    return T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))


def test_recheck_is_consumed_not_left_armed(tmp_path):
    """Leaving it set would re-hash the whole payload on every later
    pause/resume — on a 50 GB torrent that is not a small mistake."""
    td = torrent.TorrentDownloader(_task(tmp_path))
    td.t.force_recheck = True
    assert td._take_recheck() is True
    assert td.t.force_recheck is False
    assert td._take_recheck() is False


def test_recheck_reaches_aria2_as_check_integrity(tmp_path, monkeypatch):
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = _task(tmp_path)
    t.force_recheck = True
    _drive_with(tmp_path, monkeypatch, daemon, task=t)
    adds = [p for m, p in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert adds[0][-1].get("check-integrity") == "true"


def test_a_normal_run_does_not_recheck(tmp_path, monkeypatch):
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    _drive_with(tmp_path, monkeypatch, daemon)
    adds = [p for m, p in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert "check-integrity" not in adds[0][-1]


def test_the_legacy_engine_also_rechecks(tmp_path):
    td = torrent.TorrentDownloader(_task(tmp_path))
    td.t.force_recheck = True
    assert "--check-integrity=true" in td._build_cmd("aria2c.exe", str(tmp_path))
    td2 = torrent.TorrentDownloader(_task(tmp_path))
    assert "--check-integrity=true" not in td2._build_cmd("aria2c.exe", str(tmp_path))
