"""Asking trackers how many seeders a torrent has (scrape.py, Part A).

The client speaks UDP (BEP 15: connect, then scrape) and HTTP (the announce
URL's "scrape" twin, bencoded reply). SwarmScout asks about queued torrents in
the background and writes what it hears onto them; the queue ranks by it.
Both trackers here are fakes running in the test process.
"""
import http.server
import socket
import struct
import threading
import time
import urllib.parse

import pytest

import scrape
import task as T
import torrent

H1, H2, H3 = bytes(range(20)), bytes(range(20, 40)), bytes(range(40, 60))


# ---- fakes -------------------------------------------------------------------

class _FakeUdpTracker(threading.Thread):
    """A UDP tracker that knows some swarms (BEP 15)."""

    CONN = 0x1122334455667788

    def __init__(self, swarms, garbage=False):
        super().__init__(daemon=True)
        self.swarms, self.garbage = swarms, garbage
        self.batches = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.url = "udp://127.0.0.1:%d/announce" % self.sock.getsockname()[1]
        self._halt = False
        self.start()

    def run(self):
        while not self._halt:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                continue
            if self.garbage:
                self.sock.sendto(b"not a tracker", addr)
                continue
            if len(data) == 16 and struct.unpack(">Q", data[:8])[0] == scrape.PROTOCOL_ID:
                _, tid = struct.unpack(">II", data[8:16])
                self.sock.sendto(struct.pack(">IIQ", 0, tid, self.CONN), addr)
            elif len(data) >= 16:
                conn, action, tid = struct.unpack(">QII", data[:16])
                if action == 2 and conn == self.CONN:
                    hashes = [data[16 + 20 * i:36 + 20 * i] for i in range((len(data) - 16) // 20)]
                    self.batches.append(len(hashes))
                    body = b"".join(struct.pack(">III", *self.swarms.get(h, (0, 0, 0)))
                                    for h in hashes)
                    self.sock.sendto(struct.pack(">II", 2, tid) + body, addr)

    def close(self):
        self._halt = True
        self.sock.close()


def _bencode(v):
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, list):
        return b"l" + b"".join(_bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(v[k]) for k in sorted(v)) + b"e"
    raise TypeError(v)


class _FakeHttpTracker:
    def __init__(self, swarms):
        swarms_ = swarms

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path != "/scrape":
                    self.send_response(404); self.end_headers(); return
                asked = [urllib.parse.unquote_to_bytes(p[len("info_hash="):])
                         for p in query.split("&") if p.startswith("info_hash=")]
                files = {h: {b"complete": swarms_[h][0], b"downloaded": swarms_[h][1],
                             b"incomplete": swarms_[h][2]} for h in asked if h in swarms_}
                body = _bencode({b"files": files})
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/announce" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---- the client --------------------------------------------------------------

def test_udp_scrape_reads_several_swarms():
    tr = _FakeUdpTracker({H1: (12, 300, 40), H2: (0, 5, 3)})
    try:
        got = scrape.udp_scrape(tr.url, [H1, H2], timeout=2)
        assert got == {H1: (12, 300, 40), H2: (0, 5, 3)}
    finally:
        tr.close()


def test_udp_scrape_splits_large_batches():
    many = [bytes([i]) * 20 for i in range(scrape.BATCH + 5)]
    tr = _FakeUdpTracker({h: (1, 1, 1) for h in many})
    try:
        got = scrape.udp_scrape(tr.url, many, timeout=2)
        assert len(got) == len(many)
        assert tr.batches == [scrape.BATCH, 5], "a tracker takes at most BATCH hashes per packet"
    finally:
        tr.close()


def test_udp_garbage_or_silence_is_an_error():
    tr = _FakeUdpTracker({}, garbage=True)
    try:
        with pytest.raises(scrape.ScrapeError):
            scrape.udp_scrape(tr.url, [H1], timeout=1)
    finally:
        tr.close()
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    try:
        t0 = time.monotonic()
        with pytest.raises(scrape.ScrapeError):
            scrape.udp_scrape("udp://127.0.0.1:%d" % silent.getsockname()[1], [H1], timeout=0.5)
        assert time.monotonic() - t0 < 3, "the timeout was not honoured"
    finally:
        silent.close()


def test_http_scrape_reads_the_files_dict():
    tr = _FakeHttpTracker({H1: (7, 90, 20), H3: (2, 4, 6)})
    try:
        got = scrape.http_scrape(tr.url, [H1, H2, H3], timeout=2)
        assert got == {H1: (7, 90, 20), H3: (2, 4, 6)}, "a hash it does not know is left out"
    finally:
        tr.close()


def test_the_scrape_address_of_an_announce_address():
    assert scrape.scrape_url("http://t.example/announce") == "http://t.example/scrape"
    assert scrape.scrape_url("https://t.example/x/announce.php?k=1") == "https://t.example/x/scrape.php?k=1"
    assert scrape.scrape_url("http://t.example/a") is None, "no 'announce' - it cannot scrape"


def _magnet(*trackers, ih="a" * 40):
    return "magnet:?xt=urn:btih:" + ih + "".join(
        "&tr=" + urllib.parse.quote(tr, safe="") for tr in trackers)


def test_trackers_for_a_magnet_puts_its_own_first():
    public = torrent.PUBLIC_TRACKERS
    t = T.DownloadTask(_magnet("udp://own.example:6969", "udp://OWN.example:6969",
                               public[2].upper(), "wss://tracker.example/socket",
                               "http://noscrape.example/a"), "x")
    got = scrape.trackers_for(t)
    assert got[0] == "udp://own.example:6969"
    assert got[1] == public[2].upper(), "its own copy of a public tracker keeps its place"
    assert got[2:] == [p for p in public if p != public[2]]
    assert len({g.lower() for g in got}) == len(got), "duplicates were asked twice"


def test_trackers_for_a_torrent_file_reads_its_announce_list(tmp_path):
    info = {b"name": b"x", b"length": 1, b"piece length": 16384, b"pieces": b"\0" * 20}
    meta = {b"announce": b"http://file.example/announce",
            b"announce-list": [[b"http://file.example/announce"], [b"udp://tier2.example:80"]],
            b"info": info}
    path = tmp_path / "x.torrent"
    path.write_bytes(_bencode(meta))
    t = T.DownloadTask(str(path), "x", filename="x.torrent")
    assert scrape.trackers_for(t)[:2] == ["http://file.example/announce", "udp://tier2.example:80"]
    assert scrape.task_infohash(t) == bytes.fromhex(torrent.torrent_infohash(str(path)))


def test_a_private_torrent_is_asked_about_only_at_its_own_trackers(tmp_path, monkeypatch):
    """BEP 27: a private torrent talks to the trackers in its file and nobody
    else, so its infohash must not go to the public ones."""
    info = {b"name": b"x", b"length": 1, b"piece length": 16384, b"pieces": b"\0" * 20,
            b"private": 1}
    path = tmp_path / "p.torrent"
    path.write_bytes(_bencode({b"announce": b"https://private.example/pk/announce",
                               b"info": info}))
    t = T.DownloadTask(str(path), "p", filename="p.torrent")
    assert scrape.trackers_for(t) == ["https://private.example/pk/announce"]
    # a magnet whose metadata arrived and says private
    monkeypatch.setattr(torrent, "metadata_dir", lambda: str(tmp_path))
    ih = torrent.torrent_infohash(str(path))
    (tmp_path / (ih + ".torrent")).write_bytes(path.read_bytes())
    m = T.DownloadTask(_magnet("udp://own.example:80", ih=ih), "m")
    assert scrape.trackers_for(m) == ["udp://own.example:80"]


def test_infohashes_in_either_spelling():
    assert scrape.infohash_bytes("00" * 20) == bytes(20)
    assert scrape.infohash_bytes("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == bytes(20)   # base32
    assert scrape.infohash_bytes("") is None and scrape.infohash_bytes("xyz") is None


# ---- the scout ---------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 50_000.0

    def __call__(self):
        return self.t


def _tor(n, status=T.QUEUED):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + ("%02x" % n) * 20, "x%d" % n, filename="x%d" % n)
    t.status = status
    return t


class _Answers:
    """A scrape function standing in for the network, per tracker."""

    def __init__(self, table=None, failing=()):
        self.table, self.failing, self.calls = table or {}, set(failing), []

    def __call__(self, tracker, hashes):
        self.calls.append((tracker, sorted(hashes)))
        if tracker in self.failing:
            raise scrape.ScrapeError("down")
        return {h: self.table[(tracker, h)] for h in hashes if (tracker, h) in self.table}


def _scout(tasks, fn, clock, enabled=True, trackers=("udp://a", "udp://b")):
    s = scrape.SwarmScout(lambda: tasks, enabled_fn=lambda: enabled, scrape_fn=fn, clock=clock)
    s.trackers_fn = lambda t: list(trackers)
    return s


def test_only_waiting_torrents_are_asked_about():
    clock = _Clock()
    queued, paused, running = _tor(1), _tor(2, T.PAUSED), _tor(3, T.DOWNLOADING)
    web = T.DownloadTask("https://x.example/a.zip", "a")
    web.status = T.QUEUED
    fn = _Answers()
    _scout([queued, paused, running, web], fn, clock).run_once()
    asked = {h for _, hs in fn.calls for h in hs}
    assert asked == {scrape.infohash_bytes(queued.url.split(":")[-1]),
                     scrape.infohash_bytes(paused.url.split(":")[-1])}


def test_the_most_seeders_any_tracker_reports_wins_and_is_kept():
    clock = _Clock()
    t = _tor(1)
    ih = scrape.infohash_bytes(t.url.split(":")[-1])
    fn = _Answers({("udp://a", ih): (3, 0, 10), ("udp://b", ih): (8, 0, 4)})
    _scout([t], fn, clock).run_once()
    assert (t.swarm_seeds, t.swarm_peers, t.swarm_at) == (8, 10, clock.t)
    assert fn.calls == [("udp://a", [ih]), ("udp://b", [ih])] or \
        sorted(fn.calls) == [("udp://a", [ih]), ("udp://b", [ih])], "one request per tracker"


def test_nobody_answering_keeps_what_was_known_and_waits_its_turn():
    clock = _Clock()
    t = _tor(1)
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 5, 9, clock.t - 3600
    s = _scout([t], _Answers(), clock)
    s.run_once()
    assert (t.swarm_seeds, t.swarm_at) == (5, clock.t - 3600), "unknown overwrote a real count"
    fn2 = _Answers()
    s._scrape_fn = fn2
    clock.t += 60
    s.run_once()
    assert fn2.calls == [], "asked again after a minute - the refresh is every 15 minutes"
    clock.t += scrape.EVERY
    s.run_once()
    assert fn2.calls, "never asked again"


def test_a_tracker_that_keeps_failing_is_rested_for_an_hour():
    clock = _Clock()
    tasks = [_tor(1)]
    fn = _Answers(failing={"udp://a"})
    s = _scout(tasks, fn, clock)
    for _ in range(scrape.FAIL_LIMIT):
        s.run_once()
        clock.t += scrape.EVERY
    fn.calls.clear()
    s.run_once()
    assert all(tr != "udp://a" for tr, _ in fn.calls), "a dead tracker is still being asked"
    clock.t += scrape.FAIL_SKIP
    fn.calls.clear()
    s.run_once()
    assert any(tr == "udp://a" for tr, _ in fn.calls), "the rest never ended"


def test_at_most_eight_trackers_and_a_resting_one_makes_room():
    clock = _Clock()
    many = ["udp://t%02d" % i for i in range(12)]
    fn = _Answers(failing={many[0]})
    s = _scout([_tor(1)], fn, clock, trackers=many)
    s.run_once()
    assert sorted(tr for tr, _ in fn.calls) == many[:scrape.MAX_TRACKERS]
    for _ in range(scrape.FAIL_LIMIT - 1):
        clock.t += scrape.EVERY
        s.run_once()
    fn.calls.clear()
    clock.t += scrape.EVERY
    s.run_once()
    assert sorted(tr for tr, _ in fn.calls) == many[1:scrape.MAX_TRACKERS + 1], \
        "the next tracker in line should stand in for the resting one"


def test_a_tracker_that_never_heard_of_it_is_not_a_count_of_zero():
    clock = _Clock()
    t = _tor(1)
    ih = scrape.infohash_bytes(t.url.split(":")[-1])
    fn = _Answers({("udp://a", ih): (0, 0, 0), ("udp://b", ih): (0, 0, 0)})
    _scout([t], fn, clock).run_once()
    assert t.swarm_seeds is None, "all zeros means unknown, and unknown is not dead"
    t2 = _tor(2)
    ih2 = scrape.infohash_bytes(t2.url.split(":")[-1])
    fn2 = _Answers({("udp://a", ih2): (0, 0, 0), ("udp://b", ih2): (0, 31, 2)})
    _scout([t2], fn2, clock).run_once()
    assert (t2.swarm_seeds, t2.swarm_peers) == (0, 2), "known to the tracker, and nobody seeds"


def test_a_count_from_before_a_restart_is_not_asked_for_again_at_once():
    clock = _Clock()
    t = _tor(1)
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 4, 4, clock.t - 60
    fn = _Answers()
    _scout([t], fn, clock).run_once()
    assert fn.calls == []


def test_the_scout_thread_starts_and_stops(monkeypatch):
    monkeypatch.setattr(scrape, "TICK", 0.05)
    clock = _Clock()
    fn = _Answers()
    s = _scout([_tor(1)], fn, clock)
    s.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not fn.calls:
            time.sleep(0.02)
        assert fn.calls, "the thread never asked"
    finally:
        s.stop()
    s._thread.join(2)
    assert not s._thread.is_alive()


def test_switched_off_it_asks_nobody():
    clock = _Clock()
    fn = _Answers()
    _scout([_tor(1)], fn, clock, enabled=False).run_once()
    assert fn.calls == []


# ---- what the card says ------------------------------------------------------

def _card_text(t):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    return card.sub.toolTip()          # the whole line; text() is elided to the width


def test_a_queued_card_says_what_the_trackers_said():
    t = _tor(1)
    t.total_size = 1000
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 12, 40, time.time() - 180
    assert "Queued · 12 seeds · 40 peers · checked 3 min ago" in _card_text(t)
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 1, 1, time.time() - 5
    assert "Queued · 1 seed · 1 peer · checked just now" in _card_text(t)


def test_a_card_says_nothing_of_a_count_it_does_not_have():
    t = _tor(1)
    t.total_size = 1000
    assert _card_text(t).endswith("Queued")
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 9, 9, time.time() - 2 * 24 * 3600
    assert "seeds" not in _card_text(t), "a two-day-old count is not news"


def test_a_backed_off_card_still_says_why_first():
    t = _tor(1)
    t.total_size = 1000
    t.retry_after, t.yield_reason = time.time() + 90, "no seeders"
    t.swarm_seeds, t.swarm_peers, t.swarm_at = 0, 3, time.time() - 60
    assert "no seeders, retrying in" in _card_text(t)


# ---- the switch --------------------------------------------------------------

def test_settings_torrents_carries_the_switch_on_unless_turned_off():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    common = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
                  theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
                  sched_stop="06:00")
    on = SettingsDialogV2(None, extras={}, **common)
    assert on.values()["scrape_trackers"] is True
    off = SettingsDialogV2(None, extras={"scrape_trackers": False}, **common)
    assert off.values()["scrape_trackers"] is False
    off.scrape_trackers.setChecked(True)
    assert off.values()["scrape_trackers"] is True
    on.deleteLater(); off.deleteLater()


def test_the_app_asks_only_while_the_switch_is_on():
    from gui2.app import DownloadAppV2

    class _Win:
        _extras = {}
    win = _Win()
    assert DownloadAppV2._scrape_enabled(win) is True
    win._extras = {"scrape_trackers": False}
    assert DownloadAppV2._scrape_enabled(win) is False
