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


def test_run_metadata_phase_failure_stays_resumable(tmp_path, monkeypatch):
    """A non-zero exit BEFORE any payload is known means the swarm was cold, not
    that the torrent is broken — it must stay resumable rather than go red.
    (A non-zero exit after the payload is known is still an error: see
    test_real_failure_still_errors.)"""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path / "app"))
    lines = ["errorCode=1 metadata fetch failed\n"]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=1))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.PAUSED
    assert "No peers found yet" in t.error


def test_run_cancel_terminates(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    proc = _FakeProc(["[#a 10MiB/100MiB(10%)]\n"], rc=0)
    proc._rc = None                        # never finishes on its own
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: proc)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    t.request_cancel()                     # cancel before the control loop runs
    torrent.TorrentDownloader(t).run()
    assert t.status == T.CANCELLED
    # stopped gracefully: aria2 is signalled, not TerminateProcess'd, so it can
    # still write out its DHT routing table on the way down
    assert proc.signalled is not None or proc.terminated


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


def test_cancelled_run_cleans_up(tmp_path, monkeypatch):
    """A cancelled torrent must not leave its control file behind."""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path / "app"))
    out = tmp_path / "out"; out.mkdir()
    payload = out / "x"
    ctl = out / "x.aria2"
    ctl.write_bytes(b"control")
    proc = _FakeProc(["[#a 10MiB/100MiB(10%)]\n"], rc=0)
    proc._rc = None
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: proc)
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "b" * 40, str(payload))
    t.request_cancel()
    torrent.TorrentDownloader(t).run()
    assert t.status == T.CANCELLED
    assert not ctl.exists()


# ---- engine configuration: peer discovery ----
def _cmd(tmp_path, monkeypatch, url="magnet:?xt=urn:btih:abc"):
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path))
    t = T.DownloadTask(url, str(tmp_path / "out.bin"))
    return torrent.TorrentDownloader(t)._build_cmd("aria2c", str(tmp_path))


def test_dht_bootstrap_nodes_passed(tmp_path, monkeypatch):
    """aria2 ships no built-in entry point: without these a cold routing table
    can never join the DHT, so magnets sit at CN:0 forever."""
    cmd = _cmd(tmp_path, monkeypatch)
    eps = [a for a in cmd if a.startswith("--dht-entry-point=")]
    assert len(eps) == len(torrent.DHT_ENTRY_POINTS) >= 1
    assert any("router.bittorrent.com:6881" in a for a in eps)
    assert any(a.startswith("--dht-entry-point6=") for a in cmd)


def test_dht_table_persisted_in_app_data(tmp_path, monkeypatch):
    """aria2's default dht.dat dir does not exist, so it failed to save every
    run and re-bootstrapped from scratch each time."""
    cmd = _cmd(tmp_path, monkeypatch)
    paths = [a for a in cmd if a.startswith("--dht-file-path")]
    assert len(paths) == 2
    assert all(str(tmp_path) in a for a in paths)
    assert os.path.isdir(torrent.dht_dir())        # created up front


def test_listen_port_always_bound(tmp_path, monkeypatch):
    """The port must always be explicit so it matches what UPnP forwards."""
    monkeypatch.setattr(torrent.utils, "LISTEN_PORT", 0)
    cmd = _cmd(tmp_path, monkeypatch)
    assert f"--listen-port={torrent.DEFAULT_LISTEN_PORT}" in cmd
    assert f"--dht-listen-port={torrent.DEFAULT_LISTEN_PORT}" in cmd
    # an explicit user setting still wins
    monkeypatch.setattr(torrent.utils, "LISTEN_PORT", 6899)
    assert "--listen-port=6899" in _cmd(tmp_path, monkeypatch)


def test_saved_metadata_reused_and_stall_timeout_generous(tmp_path, monkeypatch):
    cmd = _cmd(tmp_path, monkeypatch)
    assert "--bt-load-saved-metadata=true" in cmd
    assert "--enable-dht6=true" in cmd
    stall = next(a for a in cmd if a.startswith("--bt-stop-timeout="))
    assert int(stall.split("=")[1]) >= 900        # metadata phase shares this timer


def test_no_peers_ends_paused_not_error(tmp_path, monkeypatch):
    """A cold swarm is not a failure: it is the case that starts working later,
    so it must stay resumable instead of going red."""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path / "app"))
    lines = ["[#a 0B/0B CN:0 SD:0 DL:0B]\n"]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=1))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Cold", str(tmp_path / "out" / "x"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.PAUSED
    assert "No peers found yet" in t.error


def test_real_failure_still_errors(tmp_path, monkeypatch):
    """Once the payload is known, a non-zero exit is a genuine failure."""
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    monkeypatch.setattr(torrent.utils, "app_data_dir", lambda: str(tmp_path / "app"))
    out = tmp_path / "out"; out.mkdir()
    lines = [f"FILE: {out / 'movie.mkv'}\n", "[#a 5MiB/100MiB(5%) CN:3 SD:1]\n",
             "errorCode=2 something broke\n"]
    monkeypatch.setattr(torrent.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, rc=1))
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(out / "x"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.ERROR
    assert "torrent failed" in t.error
