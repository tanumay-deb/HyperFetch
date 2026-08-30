"""Duplicate detection across add methods, and Force Recheck for torrents."""
import os

import pytest

import task as T
import torrent
from test_aria2d import _FakeDaemon, _drive_with

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_TORRENT = os.path.join(HERE, "data",
                             "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")


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


def test_a_recheck_drops_the_daemons_copy_first(tmp_path, monkeypatch):
    """A torrent the daemon still holds would be re-attached, carrying its OLD
    options — so check-integrity never applied and Force Recheck did nothing."""
    ih = "e6a095070aa5918e336f189b3e1d5f23103b7ff0"
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()

    class _Held(_FakeDaemon):
        def __init__(self, states):
            super().__init__(states)
            self.removed = []

        def call(self, method, *params, **kw):
            if method == "aria2.tellActive":
                self.calls.append((method, params))
                return [{"gid": "oldgid", "infoHash": ih}]
            if method in ("aria2.tellWaiting", "aria2.tellStopped"):
                return []
            if method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
                self.removed.append(params[0])
                return params[0]
            return super().call(method, *params, **kw)

    daemon = _Held([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}&dn=Movie",
                       str(tmp_path / "download.bin"))
    t.force_recheck = True
    _drive_with(tmp_path, monkeypatch, daemon, task=t)

    assert "oldgid" in daemon.removed, "stale registration was not dropped"
    adds = [p for m, p in daemon.calls if m in ("aria2.addUri", "aria2.addTorrent")]
    assert adds and adds[0][-1].get("check-integrity") == "true"


def test_a_normal_start_does_not_drop_anything(tmp_path, monkeypatch):
    """Only a recheck needs the fresh registration; a plain resume should
    re-attach rather than throw the torrent away and re-announce."""
    payload = str(tmp_path / "Movie.mkv")
    open(payload, "w").close()
    daemon = _FakeDaemon([
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "files": [{"path": payload}]},
    ])
    _drive_with(tmp_path, monkeypatch, daemon)
    assert not any(m == "aria2.forceRemove" for m, _ in daemon.calls)


# --- a dead daemon entry must never be re-attached to -----------------------
# Real failure: once a torrent hit --bt-stop-timeout aria2 kept it in
# tellStopped with status="error". Every Force Start then found that gid,
# "re-attached", unpaused a corpse, read status=error straight back and failed
# in under a second — 14 such 0-minute failures in one real log.

class _DeadDaemon:
    """tellStopped holds an errored entry for our infohash."""

    def __init__(self, status="error"):
        self.status = status
        self.removed = []
        self.added = False

    def call(self, method, *a):
        if method == "aria2.tellActive":
            return []
        if method == "aria2.tellWaiting":
            return []
        if method == "aria2.tellStopped":
            if any(g == "deadgid" for g in self.removed):
                return []
            return [{"gid": "deadgid", "infoHash": "a" * 40, "status": self.status}]
        if method == "aria2.removeDownloadResult":
            self.removed.append(a[0])
            return "OK"
        if method == "aria2.unpause":
            raise AssertionError("must not unpause a dead download result")
        raise AssertionError("unexpected call " + method)


def _magnet_td(tmp_path):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "out"))
    return torrent.TorrentDownloader(t)


def test_errored_entry_is_not_reattached(tmp_path):
    td = _magnet_td(tmp_path)
    d = _DeadDaemon("error")
    assert td._rpc_find_existing(d) == "", \
        "re-attached to an errored download result — Force Start fails instantly"
    assert "deadgid" in d.removed, \
        "the dead result must be cleared or the re-add is refused as a duplicate"


def test_a_live_entry_is_still_reattached(tmp_path):
    td = _magnet_td(tmp_path)

    class _Live(_DeadDaemon):
        def call(self, method, *a):
            if method == "aria2.tellActive":
                return [{"gid": "livegid", "infoHash": "a" * 40, "status": "active"}]
            return super().call(method, *a)

    assert td._rpc_find_existing(_Live()) == "livegid"


def test_a_completed_entry_is_still_reattached(tmp_path):
    """Clearing a finished torrent would restart a download that is done."""
    td = _magnet_td(tmp_path)
    d = _DeadDaemon("complete")
    assert td._rpc_find_existing(d) == "deadgid"
    assert d.removed == []


def test_drop_existing_still_sees_dead_entries(tmp_path):
    """_rpc_drop_existing wants ANY registration, dead ones included."""
    td = _magnet_td(tmp_path)
    d = _DeadDaemon("error")
    assert td._rpc_find_existing(d, live_only=False) == "deadgid"


# --- a moved payload must not re-attach to the old folder -------------------
# Reported: a torrent's files were moved from G: to D:, and the task then died
# with "Write disk cache flush failure index=608". aria2's --dir is fixed at add
# time, and re-attach matched on infohash alone, so it kept writing to G:.

class _MovedDaemon:
    """Holds a live entry whose dir is not where the task saves any more."""

    def __init__(self, entry_dir):
        self.entry_dir = entry_dir
        self.removed = []
        self.added = []

    def call(self, method, *a):
        if method == "aria2.tellActive":
            return [{"gid": "oldgid", "infoHash": "a" * 40, "status": "active"}]
        if method in ("aria2.tellWaiting", "aria2.tellStopped"):
            return []
        if method == "aria2.tellStatus":
            return {"dir": self.entry_dir, "status": "active"}
        if method in ("aria2.forceRemove", "aria2.remove",
                      "aria2.removeDownloadResult"):
            self.removed.append(a[0] if a else None)
            return "OK"
        if method == "aria2.addUri":
            self.added.append(a[-1] if a else None)
            return "newgid"
        if method == "aria2.unpause":
            raise AssertionError("re-attached to the entry writing to the old folder")
        return {}


def _moved_td(tmp_path):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "out"))
    return torrent.TorrentDownloader(t)


def test_a_moved_payload_is_re_added_not_re_attached(tmp_path):
    d = _MovedDaemon(entry_dir=r"G:\HF\old")
    td = _moved_td(tmp_path)
    gid = td._rpc_add(d, {"dir": str(tmp_path / "new")})
    assert "oldgid" in d.removed, "kept the registration pointing at the old folder"
    assert gid == "newgid", f"did not re-add at the new location (got {gid})"


def test_an_unmoved_payload_still_re_attaches(tmp_path):
    """Re-attach is the whole point of the shared daemon; only a real move
    should cost a re-add."""
    same = str(tmp_path / "out")
    d = _MovedDaemon(entry_dir=same)
    d.call_unpause_ok = True

    class _OK(_MovedDaemon):
        def call(self, method, *a):
            if method == "aria2.unpause":
                return "OK"
            return super().call(method, *a)

    d = _OK(entry_dir=same)
    td = _moved_td(tmp_path)
    gid = td._rpc_add(d, {"dir": same})
    assert gid == "oldgid", "re-added a torrent that had not moved"
    assert d.removed == []


def test_an_unanswering_daemon_does_not_trigger_a_re_add(tmp_path):
    """Not knowing the folder is not evidence it changed."""
    class _Silent(_MovedDaemon):
        def call(self, method, *a):
            if method == "aria2.tellStatus":
                raise RuntimeError("timed out")
            if method == "aria2.unpause":
                return "OK"
            return super().call(method, *a)

    d = _Silent(entry_dir="whatever")
    td = _moved_td(tmp_path)
    gid = td._rpc_add(d, {"dir": str(tmp_path / "out")})
    assert gid == "oldgid" and d.removed == []
