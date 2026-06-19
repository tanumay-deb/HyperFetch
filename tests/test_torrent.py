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
