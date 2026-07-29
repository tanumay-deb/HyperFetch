"""BitTorrent/magnet engine (aria2c sidecar). Detection + progress parsing are
pure; run() is driven against a fake aria2c subprocess (no real swarm/network)."""
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


# ---- progress parsing ----
@pytest.mark.parametrize("line,done,total", [
    ("[#7d6f3a 12MiB/100MiB(12%) CN:5 DL:2.0MiB ETA:44s]", 12 * 1024**2, 100 * 1024**2),
    ("[#a 1.5GiB/3.0GiB(50%) CN:8]", int(1.5 * 1024**3), 3 * 1024**3),
    ("[#a 500KB/2MB(25%)]", 500 * 1000, 2 * 1000**2),
])
def test_parse_progress(line, done, total):
    assert torrent.parse_progress(line) == (done, total)


def test_parse_progress_none_for_noise():
    assert torrent.parse_progress("aria2 will resume download") is None
    assert torrent.parse_progress("") is None


# ---- run(): missing binary ----
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

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._rc      # first wait "finishes" the process
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def test_run_completes_and_tracks_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    lines = ["[#a 50MiB/100MiB(50%) CN:5]\n", "[#a 100MiB/100MiB(100%) CN:5]\n"]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=0))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(tmp_path / "out"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.COMPLETED
    assert t.total_size == 100 * 1024**2
    assert t.downloaded == t.total_size
    assert t.filename == "Movie"           # taken from magnet dn=


def test_run_skips_metadata_pseudo_entry_and_names_from_payload(tmp_path, monkeypatch):
    """A magnet's first FILE: line is the [MEMORY][METADATA] pseudo-entry — it must
    NOT become the display name; the real payload FILE: line that follows should
    (and save_path must repoint at that on-disk entry, not fall back to guessing)."""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    out = tmp_path / "out"
    real = out / "Killhouse (2026) [1080p] [WEBRip] [YTS.GG]"
    real.mkdir(parents=True)
    (real / "movie.mkv").write_bytes(b"x")
    lines = [
        "FILE: [MEMORY][METADATA]Killhouse+(2026)+[1080p]+[YTS.GG]\n",
        f"FILE: {real / 'movie.mkv'} (1 more)\n",
        "[#a 100MiB/100MiB(100%) CN:5]\n",
    ]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=0))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Killhouse+(2026)", str(out / "x"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.COMPLETED
    assert t.filename == "Killhouse (2026) [1080p] [WEBRip] [YTS.GG]"
    assert "[METADATA]" not in t.filename and "+" not in t.filename
    assert t.save_path == str(real)


def test_run_resolves_save_path_before_completed(tmp_path, monkeypatch):
    """The GUI's completion tick categorizes by save_path the moment it sees
    COMPLETED — the status flip must come AFTER _resolve_save_path, or a
    single-file torrent gets categorized against the placeholder path."""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    out = tmp_path / "out"
    out.mkdir()
    (out / "movie.mkv").write_bytes(b"x")
    lines = [
        f"FILE: {out / 'movie.mkv'}\n",
        "[#a 100MiB/100MiB(100%) CN:5]\n",
    ]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=0))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(out / "x"))

    status_at_resolve = []
    orig = torrent.TorrentDownloader._resolve_save_path

    def spy(self, out_dir, top):
        status_at_resolve.append(self.t.status)
        return orig(self, out_dir, top)

    monkeypatch.setattr(torrent.TorrentDownloader, "_resolve_save_path", spy)
    torrent.TorrentDownloader(t).run()
    assert t.status == T.COMPLETED
    assert t.save_path == str(out / "movie.mkv")
    assert status_at_resolve == [T.DOWNLOADING]   # resolved before the flip


def test_run_reports_error_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    lines = ["errorCode=1 metadata fetch failed\n"]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=1))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.ERROR
    assert "torrent failed" in t.error


def test_run_cancel_terminates(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    proc = _FakeProc(["[#a 10MiB/100MiB(10%)]\n"], rc=0)
    proc._rc = None                        # never finishes on its own
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: proc)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    t.request_cancel()                     # cancel before the control loop runs
    torrent.TorrentDownloader(t).run()
    assert t.status == T.CANCELLED
    assert proc.terminated


# ---- torrent file listing (drawer Files tab) ----
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
