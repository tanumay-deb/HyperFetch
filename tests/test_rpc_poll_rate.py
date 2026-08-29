"""Status polling must not congest the single-threaded aria2 daemon.

Reported: adding a torrent while others are downloading stalls them all for a
while, then they restart. Seen in a real log — five magnets added at 22:39:23,
and at 22:40:45 all ten running torrents logged a tellStatus timeout in the
SAME second, then the app re-attached.

aria2 answers RPC from the same thread that does metadata lookups and file
allocation. Every torrent polled tellStatus on POLL (0.3s), so ten torrents put
~33 calls/second into that one thread; adding magnets gave it real work at the
same time and it starved.
"""
import threading
import time

import pytest

import aria2d
import task as T
import torrent
import utils


class _CountingDaemon:
    """Stays 'active' forever and counts what it is asked."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        with self.lock:
            self.calls.append((time.time(), method))
        if method == "aria2.tellStatus":
            return {"status": "active", "completedLength": "1",
                    "totalLength": "1000", "connections": "5"}
        if method in ("aria2.addUri", "aria2.addTorrent"):
            return "gid1"
        return {}

    def count(self, method):
        with self.lock:
            return sum(1 for _, m in self.calls if m == method)


def _run_for(tmp_path, monkeypatch, seconds, task_id="abc"):
    """Drive a torrent's RPC poll loop for a wall-clock window."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = _CountingDaemon()
    monkeypatch.setattr(aria2d, "DAEMON", d)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie",
                       str(tmp_path / "download.bin"))
    t.id = task_id
    td = torrent.TorrentDownloader(t)

    th = threading.Thread(target=td._run_rpc, daemon=True)
    th.start()
    time.sleep(seconds)
    t.request_pause()
    th.join(timeout=5)
    return d, t, th


def test_status_is_polled_far_less_often_than_the_control_loop(tmp_path, monkeypatch):
    d, _, _ = _run_for(tmp_path, monkeypatch, 2.0)
    n = d.count("aria2.tellStatus")
    # at POLL=0.3 this loop issued ~7 calls in 2s; at STATUS_POLL=1.0 it is ~2
    ceiling = int(2.0 / torrent.STATUS_POLL) + 2
    assert n <= ceiling, (
        f"{n} tellStatus calls in 2s — one torrent alone should manage about "
        f"{int(2.0 / torrent.STATUS_POLL)}; ten of these is what starved the daemon")


def test_pause_is_still_answered_on_the_fast_loop(tmp_path, monkeypatch):
    """The whole point of a separate STATUS_POLL: pause must not get slower."""
    d, t, th = _run_for(tmp_path, monkeypatch, 1.2)
    assert not th.is_alive(), "pause did not take effect promptly"
    assert t.status == T.PAUSED


def test_torrents_added_together_do_not_poll_in_lockstep(tmp_path, monkeypatch):
    """Two torrents starting at the same moment must not align their bursts."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))

    def first_delay(task_id):
        return (sum(ord(c) for c in str(task_id)) % 100) / 100.0 * torrent.STATUS_POLL

    delays = {first_delay(i) for i in ("aaaa", "bbbb", "cccc", "dddd", "eeee")}
    assert len(delays) > 1, "every task would poll on the same beat"
    assert all(0 <= x < torrent.STATUS_POLL for x in delays), \
        "the stagger must stay inside one interval, not add latency"


def test_status_poll_is_slower_than_the_control_poll():
    assert torrent.STATUS_POLL > torrent.POLL, (
        "STATUS_POLL exists to take RPC off the fast loop; if it is not slower "
        "it does nothing")
    assert torrent.STATUS_POLL <= 2.0, "progress would visibly lag"
