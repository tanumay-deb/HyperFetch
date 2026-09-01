"""BitTorrent/magnet engine (aria2c sidecar). Detection + progress parsing are
pure; run() is driven against a fake aria2c subprocess (no real swarm/network)."""
import os
import subprocess

import pytest

import task as T
import torrent


# ---- detection ----
@pytest.mark.parametrize("url,fn,exp", [
    ("magnet:?xt=urn:btih:abc&dn=Foo", "", True),
    ("MAGNET:?xt=urn:btih:abc", "", True),
    ("http://x/file.torrent", "", True),
    ("http://x/get?id=1", "thing.torrent", True),
    ("http://x/file.zip", "file.zip", False),
    ("https://x/v.m3u8", "", False),
])
def test_is_torrent_task(url, fn, exp):
    assert torrent.is_torrent_task(url, fn) is exp


def test_magnet_name():
    assert torrent.magnet_name("magnet:?xt=urn:btih:abc&dn=My%20Movie%202024") == "My Movie 2024"
    assert torrent.magnet_name("magnet:?xt=urn:btih:abc") == ""
    assert torrent.magnet_name("") == ""


def test_magnet_name_decodes_plus_as_space():
    # dn= is a query value: '+' means space (was shown raw in the UI)
    assert torrent.magnet_name(
        "magnet:?xt=urn:btih:abc&dn=Killhouse+(2026)+%5B1080p%5D+%5BYTS.GG%5D"
    ) == "Killhouse (2026) [1080p] [YTS.GG]"


def test_magnet_trackers_deduplicates_and_preserves_order():
    url = (
        "magnet:?xt=urn:btih:abc&tr=udp%3A%2F%2Fone.example%3A80"
        "&tr=https%3A%2F%2Ftwo.example%2Fannounce&tr=UDP%3A%2F%2Fone.example%3A80"
    )

    assert torrent.magnet_trackers(url) == [
        "udp://one.example:80", "https://two.example/announce",
    ]


def test_merge_magnet_trackers_adds_only_new_trackers():
    current = "magnet:?xt=urn:btih:abc&dn=Example&tr=udp%3A%2F%2Fone.example%3A80"
    updated, added = torrent.merge_magnet_trackers(current, [
        "UDP://one.example:80", "https://two.example/announce",
    ])

    assert added == ["https://two.example/announce"]
    assert torrent.magnet_infohash(updated) == "abc"
    assert torrent.magnet_trackers(updated) == [
        "udp://one.example:80", "https://two.example/announce",
    ]


# ---- progress parsing ----
def test_run_errors_clearly_without_aria2c(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: None)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=x", str(tmp_path / "x"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.ERROR
    assert "aria2c not found" in t.error


# ---- run(): fake aria2c subprocess ----
class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = list(lines)          # reader iterates this
        self._rc = rc
        self.returncode = None
        self.terminated = False
        self.signalled = None               # mirrors Popen.send_signal

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._rc      # first wait "finishes" the process
        return self.returncode

    def send_signal(self, sig):
        # aria2 is asked politely first (CTRL_BREAK) so it can write out its
        # DHT routing table; a real Popen always has this.
        self.signalled = sig
        self.returncode = 0

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _bencode(obj):
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    if isinstance(obj, list):
        return b"l" + b"".join(_bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        out = b"d"
        for k in sorted(obj):
            out += _bencode(k) + _bencode(obj[k])
        return out + b"e"
    raise TypeError(type(obj))


def _write_multi(path, name=b"Show.S01", files=(("a.mkv", 900), ("subs/a.srt", 40))):
    entries = [{b"path": [p.encode() for p in rel.split("/")], b"length": ln}
               for rel, ln in files]
    path.write_bytes(_bencode({b"info": {b"name": name, b"files": entries}}))


def test_parse_torrent_files_multi(tmp_path):
    p = tmp_path / "m.torrent"
    _write_multi(p)
    assert torrent.parse_torrent_files(str(p)) == [("a.mkv", 900), ("subs/a.srt", 40)]


def test_parse_torrent_files_single(tmp_path):
    p = tmp_path / "s.torrent"
    p.write_bytes(_bencode({b"info": {b"name": b"movie.mkv", b"length": 1234}}))
    assert torrent.parse_torrent_files(str(p)) == [("movie.mkv", 1234)]


def test_parse_torrent_files_tolerates_junk(tmp_path):
    """A bad/absent metadata file must degrade to an empty list, never raise —
    the file list is a display nicety and can't be allowed to break a task."""
    bad = tmp_path / "bad.torrent"
    bad.write_bytes(b"not bencode at all")
    assert torrent.parse_torrent_files(str(bad)) == []
    assert torrent.parse_torrent_files(str(tmp_path / "missing.torrent")) == []
    trunc = tmp_path / "t.torrent"
    trunc.write_bytes(_bencode({b"info": {b"name": b"x", b"length": 5}})[:-3])
    assert torrent.parse_torrent_files(str(trunc)) == []


def test_magnet_infohash():
    assert torrent.magnet_infohash("magnet:?xt=urn:btih:ABCdef123&dn=x") == "abcdef123"
    assert torrent.magnet_infohash("magnet:?dn=noHash") == ""
    assert torrent.magnet_infohash("https://x/y.zip") == ""


def test_list_files_while_downloading(tmp_path):
    """Mid-download save_path is a placeholder inside aria2's --dir, where the
    <infohash>.torrent metadata lives."""
    ih = "a" * 40
    _write_multi(tmp_path / f"{ih}.torrent")
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}", str(tmp_path / "download.bin"))
    assert torrent.list_files(t) == [("a.mkv", 900), ("subs/a.srt", 40)]


def test_list_files_when_completed(tmp_path):
    """Once finished, save_path is the payload FOLDER — the metadata sits in its
    parent, so resolution has to look one level up."""
    ih = "b" * 40
    _write_multi(tmp_path / f"{ih}.torrent")
    payload = tmp_path / "Show.S01"
    payload.mkdir()
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}", str(payload))
    assert len(torrent.list_files(t)) == 2


def test_list_files_local_torrent_file(tmp_path):
    p = tmp_path / "local.torrent"
    _write_multi(p)
    t = T.DownloadTask(str(p), str(tmp_path / "out.bin"), filename="local.torrent")
    assert len(torrent.list_files(t)) == 2


def test_list_files_empty_for_non_torrent_and_missing_metadata(tmp_path):
    assert torrent.list_files(T.DownloadTask("https://x/a.zip", str(tmp_path / "a.zip"))) == []
    # magnet whose metadata has not arrived yet
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "c" * 40, str(tmp_path / "d.bin"))
    assert torrent.list_files(t) == []


# ---- aria2 leftovers: keep the user's download folder clean ----
def test_archive_metadata_moves_out_of_download_folder(tmp_path, monkeypatch):
    ih = "d" * 40
    dl = tmp_path / "Downloads"; dl.mkdir()
    app = tmp_path / "meta_store"; app.mkdir()
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(app))
    _write_multi(dl / f"{ih}.torrent")
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}", str(dl / "download.bin"))

    dest = torrent.archive_metadata(t, str(dl))
    assert dest and os.path.isfile(dest)
    assert not (dl / f"{ih}.torrent").exists()        # gone from Downloads
    # and the Files tab still resolves it from the archive
    assert len(torrent.list_files(t)) == 2


def test_archive_metadata_noop_without_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path))
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "e" * 40, str(tmp_path / "x.bin"))
    assert torrent.archive_metadata(t, str(tmp_path)) == ""
    # a non-magnet has no infohash to look for
    assert torrent.archive_metadata(T.DownloadTask("https://x/a.zip", "a.zip"), str(tmp_path)) == ""


def test_cleanup_artifacts_removes_control_file_and_metadata(tmp_path, monkeypatch):
    ih = "f" * 40
    app = tmp_path / "meta_store"; app.mkdir()
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(app))
    payload = tmp_path / "Show.S01"
    payload.mkdir()
    ctl = tmp_path / "Show.S01.aria2"
    ctl.write_bytes(b"control")
    _write_multi(tmp_path / f"{ih}.torrent")
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{ih}", str(payload))
    torrent.archive_metadata(t, str(tmp_path))

    torrent.cleanup_artifacts(t)
    assert not ctl.exists()                                   # control file gone
    assert not (tmp_path / f"{ih}.torrent").exists()
    assert not (app / "torrents" / f"{ih}.torrent").exists()  # archive gone too
    assert payload.exists()          # payload itself is NOT touched here


def test_cleanup_artifacts_leaves_other_tasks_alone(tmp_path, monkeypatch):
    """Only this task's leftovers may be removed — a blind sweep would break
    another torrent that is still resuming from its control file."""
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path / "app"))
    mine = tmp_path / "Mine"; mine.mkdir()
    (tmp_path / "Mine.aria2").write_bytes(b"x")
    other = tmp_path / "Other.aria2"
    other.write_bytes(b"keep me")
    torrent.cleanup_artifacts(T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(mine)))
    assert other.exists()


def test_a_path_on_another_drive_does_not_rename_the_torrent():
    top = torrent.TorrentDownloader._top_entry(
        r"G:\HF\Pretty Little Liars Season 2\S02E13.mkv (12 more)", r"D:\HF")
    assert top == "", f"renamed the torrent to {top!r} after the payload moved"


def test_a_multi_file_torrent_never_resolves_to_a_bare_filename(tmp_path):
    """'(N more)' says there are several files, so a filename cannot be the
    top-level entry."""
    out = str(tmp_path)
    top = torrent.TorrentDownloader._top_entry(
        os.path.join(out, "Episode.mkv") + " (12 more)", out)
    assert top == ""


def test_the_normal_multi_file_case_still_resolves(tmp_path):
    out = str(tmp_path)
    top = torrent.TorrentDownloader._top_entry(
        os.path.join(out, "Season 2", "S02E13.mkv") + " (12 more)", out)
    assert top == "Season 2"


def test_a_single_file_torrent_still_resolves(tmp_path):
    out = str(tmp_path)
    top = torrent.TorrentDownloader._top_entry(os.path.join(out, "Movie.mkv"), out)
    assert top == "Movie.mkv"


# ---- where a finished torrent's files actually are ----
def _dl(tmp_path, filename, save_path=None):
    """A torrent task shaped the way a magnet leaves one: the name is known
    from `dn`, but save_path is still the placeholder made before any metadata
    arrived."""
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=x",
                       save_path or str(tmp_path / "magnet_.bin"),
                       filename=filename)
    d = torrent.TorrentDownloader.__new__(torrent.TorrentDownloader)
    d.t = t
    d._started = 0
    return d, t


def test_the_finished_folder_is_found_from_the_name_we_already_have(tmp_path):
    """The Odyssey case: aria2 gave no FILE: line, so save_path stayed at the
    placeholder even though the folder was sitting right there under the name
    the magnet's dn had already given us."""
    name = "The Odyssey (2026) [1080p] [WEBRip] [5.1] [YTS.GG - YTS.BZ]"
    got = tmp_path / name
    got.mkdir()
    (got / "movie.mkv").write_bytes(b"x")

    d, t = _dl(tmp_path, name)
    d._resolve_save_path(str(tmp_path), "")

    assert t.save_path == str(got)
    assert os.path.isdir(t.save_path)


def test_the_file_line_still_wins_when_aria2_gives_one(tmp_path):
    (tmp_path / "from-aria2").mkdir()
    (tmp_path / "from-dn").mkdir()
    d, t = _dl(tmp_path, "from-dn")
    d._resolve_save_path(str(tmp_path), "from-aria2")
    assert t.save_path == str(tmp_path / "from-aria2")
    assert t.filename == "from-aria2"


def test_a_name_that_is_not_on_disk_does_not_become_the_save_path(tmp_path):
    d, t = _dl(tmp_path, "never-downloaded")
    d._resolve_save_path(str(tmp_path), "")
    assert not t.save_path.endswith("never-downloaded")


def test_the_name_cannot_walk_out_of_the_download_folder(tmp_path):
    """`dn` is remote input. A name of `../x` must not point save_path at
    something outside the folder the user chose."""
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    inner = tmp_path / "dl"
    inner.mkdir()

    d, t = _dl(inner, os.path.join("..", "outside-target"))
    d._resolve_save_path(str(inner), "")

    assert os.path.realpath(t.save_path) != os.path.realpath(str(outside))


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_missing_name_falls_through_to_the_mtime_guess(tmp_path, name):
    # Its own folder: tmp_path also holds whatever the app-data fixture put
    # there, and the guess is "newest entry", not "the one this test made".
    out = tmp_path / "out"
    out.mkdir()
    real = out / "picked-by-mtime"
    real.mkdir()
    d, t = _dl(tmp_path, name)
    d._resolve_save_path(str(out), "")
    assert t.save_path == str(real)
