"""The real torrent engine, end to end, on this machine.

Everything else in the suite drives TorrentDownloader against a scripted
stand-in for aria2. This runs the real one: the app's shared daemon
(aria2d), started as the app starts it, fetches a torrent from a seeding
aria2c through a tracker - both here, on loopback. The torrent is private,
so neither aria2 asks DHT, PEX or the public trackers for anybody else.

Skipped where there is no aria2c (CI installs one on Linux; Windows has
bin/aria2c.exe).
"""
import hashlib
import http.server
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

import aria2d
import task as T
import torrent
import utils

EXE = torrent.aria2c_path()
# CI installs aria2 and sets this: there, a missing aria2c is a failure
if os.environ.get("HF_REQUIRE_ENGINES") and not EXE:
    raise RuntimeError("HF_REQUIRE_ENGINES is set and there is no aria2c")
pytestmark = pytest.mark.skipif(not EXE, reason="no aria2c on this machine")

PIECE = 256 * 1024


def _bencode(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(map(_bencode, x)) + b"e"
    return b"d" + b"".join(_bencode(k) + _bencode(x[k]) for k in sorted(x)) + b"e"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Tracker(http.server.ThreadingHTTPServer):
    """Answers every announce with the peers that announced, compact form."""
    daemon_threads = True


@pytest.fixture
def swarm(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    payload = os.urandom(3 * 1024 * 1024 + 12345)          # not a whole piece
    (seed_dir / "Payload.bin").write_bytes(payload)

    peers = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            port = int((q.get("port") or ["0"])[0])
            if port:
                peers[port] = time.time()
            compact = b"".join(socket.inet_aton("127.0.0.1") + struct.pack(">H", p)
                               for p in peers)
            body = _bencode({"interval": 5, "min interval": 1, "peers": compact})
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    tracker = _Tracker(("127.0.0.1", 0), Handler)
    threading.Thread(target=tracker.serve_forever, daemon=True).start()
    announce = "http://127.0.0.1:%d/announce" % tracker.server_address[1]
    info = {"name": "Payload.bin", "length": len(payload), "piece length": PIECE,
            "pieces": b"".join(hashlib.sha1(payload[i:i + PIECE]).digest()
                               for i in range(0, len(payload), PIECE)),
            "private": 1}
    tpath = tmp_path / "Payload.torrent"
    tpath.write_bytes(_bencode({"announce": announce, "info": info}))

    seed_port = _free_port()
    seeder = subprocess.Popen(
        [EXE, "--dir=%s" % seed_dir, "--listen-port=%d" % seed_port,
         "--dht-listen-port=%d" % _free_port(), "--enable-dht=false",
         "--enable-dht6=false", "--bt-enable-lpd=false", "--enable-peer-exchange=false",
         "--check-integrity=true", "--seed-ratio=0.0", "--file-allocation=none",
         "--summary-interval=0", "--console-log-level=warn", "--bt-tracker-interval=2",
         "--rpc-save-upload-metadata=false", "--bt-save-metadata=false", str(tpath)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        yield {"torrent": str(tpath), "payload": payload, "peers": peers,
               "seed_port": seed_port}
    finally:
        seeder.kill()
        seeder.wait(10)
        tracker.shutdown()


@pytest.fixture
def daemon(monkeypatch):
    """The app's daemon as the app starts it - on ports of its own, and with
    nothing to find on the internet."""
    monkeypatch.setattr(utils, "LISTEN_PORT", _free_port())
    monkeypatch.setattr(torrent, "PUBLIC_TRACKERS", [])
    monkeypatch.setattr(torrent, "DHT_ENTRY_POINTS", [])
    monkeypatch.setattr(torrent, "DHT_ENTRY_POINTS6", [])
    # the seeder runs from the same aria2c, and is nobody's to reap
    monkeypatch.setattr(aria2d.Aria2Daemon, "_reap_others", lambda self: None)
    d = aria2d.Aria2Daemon()
    monkeypatch.setattr(aria2d, "DAEMON", d)
    try:
        yield d
    finally:
        d.shutdown(wait=5.0, force=True)


def _run(t, limit=90):
    done = threading.Event()

    def go():
        try:
            torrent.TorrentDownloader(t).run()
        finally:
            done.set()

    threading.Thread(target=go, daemon=True).start()
    if not done.wait(limit):
        t.request_cancel()
        done.wait(15)
        pytest.fail("not done in %ds: %s %s, %d of %d bytes" % (
            limit, t.status, t.error, t.downloaded, t.total_size))


def test_a_torrent_comes_down_whole_through_the_real_engine(swarm, daemon, tmp_path):
    out = tmp_path / "downloads"
    out.mkdir()
    t = T.DownloadTask(swarm["torrent"], str(out / "Payload.torrent"))
    _run(t)
    assert t.status == T.COMPLETED, (t.status, t.error)
    got = out / "Payload.bin"
    assert got.read_bytes() == swarm["payload"]
    assert t.filename == "Payload.bin" and t.downloaded == len(swarm["payload"])
    assert t.infohash and os.path.isfile(torrent.kept_torrent(t.infohash))
    left = sorted(p.name for p in out.iterdir())
    assert left == ["Payload.bin"], "left beside the payload: %s" % left


def test_a_paused_torrent_goes_on_from_where_it_was(swarm, daemon, tmp_path, monkeypatch):
    """Pause mid-way, resume: the control file carries it, nothing is fetched twice."""
    out = tmp_path / "downloads"
    out.mkdir()
    t = T.DownloadTask(swarm["torrent"], str(out / "Payload.torrent"))

    def pause_when_started():
        end = time.monotonic() + 60
        while time.monotonic() < end and t.downloaded < PIECE:
            time.sleep(0.05)
        t.request_pause()

    # slow enough to catch it half done - the app's own limit, which the
    # engine hands aria2 on every poll (torrent.tell_download_limit)
    was = utils.global_limiter.limit_bps
    utils.global_limiter.set_limit(256 * 1024)
    try:
        threading.Thread(target=pause_when_started, daemon=True).start()
        _run(t)
    finally:
        utils.global_limiter.set_limit(was)
    assert t.status == T.PAUSED, (t.status, t.error)
    part = t.downloaded
    assert 0 < part < len(swarm["payload"])

    t.clear_pause()                     # what Resume does before the queue runs it
    t.status = T.QUEUED
    _run(t)
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (out / "Payload.bin").read_bytes() == swarm["payload"]
