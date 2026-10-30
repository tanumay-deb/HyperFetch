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
    payload = os.urandom(3 * 1024 * 1024 + 12345)          # not a whole piece
    yield from _swarm(tmp_path, "Payload.bin", payload)


def _swarm(tmp_path, name, payload, files=None):
    """A seeding aria2c and a tracker for one file, or for a folder of
    `files` [(name, bytes)] whose bytes make up `payload`."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    if files:
        (seed_dir / name).mkdir()
        for fname, data in files:
            (seed_dir / name / fname).write_bytes(data)
    else:
        (seed_dir / name).write_bytes(payload)

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
    info = {"name": name, "piece length": PIECE,
            "pieces": b"".join(hashlib.sha1(payload[i:i + PIECE]).digest()
                               for i in range(0, len(payload), PIECE)),
            "private": 1}
    if files:
        info["files"] = [{"length": len(data), "path": [fname]} for fname, data in files]
    else:
        info["length"] = len(payload)
    tpath = tmp_path / (os.path.splitext(name)[0] + ".torrent")
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
               "seed_port": seed_port, "announce": announce}
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


# ---- the unfinished-downloads folder (utils.INCOMPLETE_DIR) ---------------------
def _wait(cond, limit=60):
    end = time.monotonic() + limit
    while time.monotonic() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_it_is_written_in_the_unfinished_folder_and_finishes_in_its_own(
        swarm, daemon, tmp_path, monkeypatch):
    unfinished, home = tmp_path / "unfinished", tmp_path / "downloads"
    home.mkdir()
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(unfinished))
    t = T.DownloadTask(swarm["torrent"], str(home / "Payload.torrent"))
    seen = {}

    def watch():                                   # where are the bytes while it runs?
        if _wait(lambda: t.downloaded > 0, 60):
            seen["unfinished"] = (unfinished / "Payload.bin").exists()
            seen["home"] = (home / "Payload.bin").exists()

    was = utils.global_limiter.limit_bps
    utils.global_limiter.set_limit(512 * 1024)     # slow enough to look while it runs
    try:
        threading.Thread(target=watch, daemon=True).start()
        _run(t)
    finally:
        utils.global_limiter.set_limit(was)
    assert seen == {"unfinished": True, "home": False}, seen
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (home / "Payload.bin").read_bytes() == swarm["payload"]
    assert t.save_path == str(home / "Payload.bin") and t.unfinished_in == ""
    left = [f for _, _, fs in os.walk(unfinished) for f in fs]
    assert left == [], "left in the unfinished folder: %s" % left


def test_one_that_seeds_is_moved_and_seeds_from_its_own_folder(
        swarm, daemon, tmp_path, monkeypatch):
    unfinished, home = tmp_path / "unfinished", tmp_path / "downloads"
    home.mkdir()
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(unfinished))
    monkeypatch.setattr(utils, "SEED_ENABLED", True)
    monkeypatch.setattr(utils, "SEED_RATIO", 0.0)
    monkeypatch.setattr(utils, "SEED_MINUTES", 60)
    t = T.DownloadTask(swarm["torrent"], str(home / "Payload.torrent"))
    done = threading.Event()
    threading.Thread(target=lambda: (torrent.TorrentDownloader(t).run(), done.set()),
                     daemon=True).start()
    try:
        assert _wait(lambda: t.seeding and not t.verifying
                     and (home / "Payload.bin").exists(), 90), (t.status, t.error)
        assert (home / "Payload.bin").read_bytes() == swarm["payload"]
        st = daemon.call("aria2.tellStatus", t.gid, ["dir", "status", "seeder"])
        assert os.path.normcase(st["dir"]) == os.path.normcase(str(home)), st
        assert st["status"] == "active" and st["seeder"] == "true", st
        assert not (unfinished / "Payload.bin").exists()
        assert t.status == T.COMPLETED and t.unfinished_in == ""
    finally:
        t.request_pause()
        done.wait(20)


def test_a_magnets_metadata_record_outlives_its_payload_and_is_not_reattached(
        swarm, daemon, tmp_path):
    """What a stall yield leaves in aria2 for a magnet (reported 2026-10-28:
    six magnets restarting about once a second, for as long as the app ran)."""
    ih = torrent.torrent_infohash(swarm["torrent"])
    magnet = "magnet:?xt=urn:btih:%s&tr=%s" % (ih, urllib.parse.quote(swarm["announce"], safe=""))
    meta = daemon.call("aria2.addUri", [magnet], {"dir": str(tmp_path), "pause-metadata": "true"})
    assert _wait(lambda: daemon.call("aria2.tellStatus", meta).get("followedBy"), 60)
    payload = daemon.call("aria2.tellStatus", meta)["followedBy"][0]
    stopped = lambda: daemon.call("aria2.tellStopped", 0, 100)  # noqa: E731
    # dropped as _yield_slot and _hand_over drop it; a held (paused) download
    # leaves no result behind - measured: its gid is "not found" at once
    daemon.call("aria2.forceRemove", payload)
    with pytest.raises(aria2d.Aria2Error, match="is not found"):
        daemon.call("aria2.tellStatus", payload)
    left = [r for r in stopped() if r["gid"] == meta]
    assert left and left[0]["status"] == "complete" and left[0]["followedBy"] == [payload], left

    t = T.DownloadTask(magnet, str(tmp_path / "magnet_.bin"))
    assert torrent.TorrentDownloader(t)._rpc_find_existing(daemon) == ""
    assert not [r for r in stopped() if r["gid"] == meta], "the record was not cleared"


def test_a_torrent_moved_half_way_goes_on_in_its_new_folder(swarm, daemon, tmp_path):
    """Move to… on a paused torrent (asked 2026-10-29): what it has written,
    and aria2's control file, go along, and it goes on from them there."""
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    t = T.DownloadTask(swarm["torrent"], str(old / "Payload.torrent"))
    t.sort_base = ""                                  # where the user put it
    seen = []

    def pause_half_way():
        _wait(lambda: t.downloaded >= 4 * PIECE)
        t.request_pause()

    def first_reading():                             # after the move: from 0, or from the bytes?
        if _wait(lambda: t.downloaded > 0, 60):
            seen.append(t.downloaded)

    was = utils.global_limiter.limit_bps
    utils.global_limiter.set_limit(256 * 1024)       # slow enough to catch it half way
    try:
        threading.Thread(target=pause_half_way, daemon=True).start()
        _run(t)
        assert t.status == T.PAUSED, (t.status, t.error)
        assert 4 * PIECE <= t.downloaded < len(swarm["payload"])

        assert torrent.move_unfinished(t, str(new))
        assert sorted(p.name for p in new.iterdir()) == ["Payload.bin", "Payload.bin.aria2"]
        t.save_path = str(new / os.path.basename(t.save_path))   # what the window does
        t.downloaded = 0
        t.clear_pause()
        t.status = T.QUEUED
        threading.Thread(target=first_reading, daemon=True).start()
        _run(t)
    finally:
        utils.global_limiter.set_limit(was)
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert seen and seen[0] >= 4 * PIECE, "it started over: first read %s" % seen
    assert (new / "Payload.bin").read_bytes() == swarm["payload"]
    assert not (old / "Payload.bin").exists()


def test_one_with_bytes_in_its_own_folder_goes_on_there(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unfinished"))
    t = T.DownloadTask("magnet:?xt=urn:btih:%040d" % 1, str(tmp_path / "dl" / "x"))
    t.downloaded = 1234                               # started before the folder was picked
    td = torrent.TorrentDownloader(t)
    td._final_dir = str(tmp_path / "dl")
    assert td._work_dir() == str(tmp_path / "dl") and t.unfinished_in == ""


# ---- a file unticked while paused (reported 2026-10-29) -------------------------
# A paused torrent stays in the daemon, and Resume re-attached to it as it was:
# the choice made while it was paused was saved but never reached aria2, which
# fetched the unticked file anyway.
@pytest.fixture
def pack(tmp_path):
    first, second = os.urandom(4 * PIECE), os.urandom(4 * PIECE)    # no piece in both
    yield from _swarm(tmp_path, "Pack", first + second,
                      [("first.bin", first), ("second.bin", second)])


def test_a_file_unticked_while_paused_is_not_fetched(pack, daemon, tmp_path):
    out = tmp_path / "downloads"
    out.mkdir()
    t = T.DownloadTask(pack["torrent"], str(out / "Pack.torrent"))

    def pause_when_started():
        end = time.monotonic() + 60
        while time.monotonic() < end and t.downloaded < PIECE:
            time.sleep(0.05)
        t.request_pause()

    was = utils.global_limiter.limit_bps
    utils.global_limiter.set_limit(256 * 1024)
    try:
        threading.Thread(target=pause_when_started, daemon=True).start()
        _run(t)
    finally:
        utils.global_limiter.set_limit(was)
    assert t.status == T.PAUSED, (t.status, t.error)

    t.selected_files = "1"              # what unticking second.bin records while paused
    t.clear_pause()
    t.status = T.QUEUED
    _run(t)
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (out / "Pack" / "first.bin").read_bytes() == pack["payload"][:4 * PIECE]
    assert t.total_size == 4 * PIECE, "aria2 fetched the unticked file too"


# ---- files ticked after it finished (reported 2026-10-30) -----------------------
# Five of ten files down and the torrent Completed; ticking the other five
# recorded the choice and nothing more. aria2 dropped the control file as the
# torrent completed, so the files on disk are taken back by a hash check and
# only the rest is fetched, in the folder the rest already is in.
def test_files_ticked_after_it_finished_are_fetched(pack, daemon, tmp_path):
    from queue_manager import QueueManager
    out = tmp_path / "downloads"
    out.mkdir()
    t = T.DownloadTask(pack["torrent"], str(out / "Pack.torrent"))
    t.selected_files = "1"
    _run(t)
    assert t.status == T.COMPLETED, (t.status, t.error)
    first = out / "Pack" / "first.bin"
    assert first.read_bytes() == pack["payload"][:4 * PIECE]

    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    q.shutdown()
    q.tasks.append(t)
    q.change_torrent_files(t.id, "1,2")             # second.bin ticked
    assert t.status == T.QUEUED, t.status
    _run(t)
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert (out / "Pack" / "second.bin").read_bytes() == pack["payload"][4 * PIECE:]
    assert first.read_bytes() == pack["payload"][:4 * PIECE]
    assert t.total_size == 8 * PIECE
