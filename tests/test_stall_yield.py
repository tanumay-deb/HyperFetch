"""A stalled torrent gives its queue slot back.

With a concurrency of 1, a torrent whose swarm is dead used to hold the only
slot forever while healthy torrents queued behind it. It now stands aside after
a grace period and retries on a growing delay.
"""
import threading
import time

import pytest

import aria2d
import queue_manager
import task as T
import torrent
import utils
from queue_manager import QueueManager
from test_aria2d import _FakeDaemon, _drive_with


# ---------------------------------------------------------------- detection
def _td(tmp_path):
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(tmp_path / "out"))
    return torrent.TorrentDownloader(t)


def test_no_peers_and_no_progress_eventually_yields(tmp_path, monkeypatch):
    td = _td(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(torrent.time, "time", lambda: clock[0])
    assert td._note_stall(0, 0) is False          # starts the clock
    clock[0] += torrent.STALL_YIELD - 1
    assert td._note_stall(0, 0) is False
    clock[0] += 2
    assert td._note_stall(0, 0) is True


def test_peers_but_slow_is_not_stalled(tmp_path, monkeypatch):
    """A torrent trickling from one seeder is alive. Taking its slot away would
    be worse than letting it finish."""
    td = _td(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(torrent.time, "time", lambda: clock[0])
    for _ in range(10):
        clock[0] += torrent.STALL_YIELD
        assert td._note_stall(3, 0) is False      # peers present, no new bytes


def test_progress_resets_the_clock(tmp_path, monkeypatch):
    td = _td(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(torrent.time, "time", lambda: clock[0])
    td._note_stall(0, 0)
    clock[0] += torrent.STALL_YIELD - 1
    assert td._note_stall(0, 500) is False        # a byte arrived
    clock[0] += torrent.STALL_YIELD - 1
    assert td._note_stall(0, 500) is False        # clock restarted


def test_recovery_forgives_the_stall_record(tmp_path, monkeypatch):
    """Otherwise a torrent that stalls once an hour ends up on a 15 minute
    backoff forever."""
    td = _td(tmp_path)
    td.t.stall_count = 2
    td._note_stall(5, 1000)
    assert td.t.stall_count == 0


def test_the_backoff_grows_then_levels_off(tmp_path, monkeypatch):
    td = _td(tmp_path)
    monkeypatch.setattr(torrent.time, "time", lambda: 0.0)
    delays = []
    for _ in range(5):
        td._yield_slot(None, None, str(tmp_path))
        delays.append(td.t.retry_after)
    assert delays == sorted(delays)                       # never shrinks
    assert delays[0] == torrent.STALL_BACKOFF[0]
    assert delays[-1] == torrent.STALL_BACKOFF[-1]        # caps


def test_a_yield_leaves_the_task_queued_not_failed(tmp_path, monkeypatch):
    td = _td(tmp_path)
    td._yield_slot(None, None, str(tmp_path))
    assert td.t.status == T.QUEUED
    assert td.t._stall_yield is True
    assert td.t.error == ""


# ------------------------------------------------------------- queue effect
class _StallingDownloader:
    """A torrent whose swarm is dead yields; everything else completes.

    Substituted for the real Downloader so queue_manager's OWN _execute runs -
    an earlier version of this test reimplemented _execute and therefore proved
    nothing about the code actually shipping.
    """

    def __init__(self, task, segments=8):
        self.t = task

    def run(self):
        self.t.status = T.DOWNLOADING
        time.sleep(0.05)
        if self.t.filename == "dead":
            self.t._stall_yield = True
            self.t.retry_after = time.time() + 60
            self.t.status = T.QUEUED
        else:
            self.t.status = T.COMPLETED


def test_a_dead_torrent_stops_blocking_the_queue(monkeypatch):
    """The whole point: concurrency 1, a dead torrent first in line, and the
    healthy one behind it must still run."""
    monkeypatch.setattr(queue_manager, "Downloader", _StallingDownloader)
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    try:
        dead = T.DownloadTask("magnet:?xt=urn:btih:dead", "dead", filename="dead")
        good = T.DownloadTask("magnet:?xt=urn:btih:good", "good", filename="good")
        q.add_task(dead)
        q.add_task(good)
        deadline = time.time() + 8
        while time.time() < deadline and good.status != T.COMPLETED:
            time.sleep(0.02)
        assert good.status == T.COMPLETED, "healthy torrent stayed blocked"
        # and the dead one is waiting its turn again, not failed
        assert dead.status == T.QUEUED
        assert dead.retry_after > time.time()
    finally:
        q.shutdown()


def test_the_stalled_torrent_runs_again_once_its_delay_expires(monkeypatch):
    """Yielding must not mean giving up - it has to come back."""
    monkeypatch.setattr(queue_manager, "Downloader", _StallingDownloader)
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    try:
        dead = T.DownloadTask("magnet:?xt=urn:btih:dead", "dead", filename="dead")
        q.add_task(dead)
        deadline = time.time() + 5
        while time.time() < deadline and dead.status != T.QUEUED:
            time.sleep(0.02)
        assert dead.status == T.QUEUED
        dead.retry_after = time.time() + 0.2          # shorten the wait
        with q.cond:
            q.cond.notify_all()
        ran_again = False
        deadline = time.time() + 5
        while time.time() < deadline:
            if dead.status == T.DOWNLOADING:
                ran_again = True
                break
            time.sleep(0.02)
        assert ran_again, "a yielded torrent was never retried"
    finally:
        q.shutdown()


def test_a_backed_off_task_is_not_picked_up_early():
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 4}])
    try:
        t = T.DownloadTask("magnet:?xt=urn:btih:abc", "x", filename="x")
        t.retry_after = time.time() + 3600
        queue_manager.heapq.heappush(q._heap, t)
        ready, wait = q._next_ready()
        assert ready is None
        assert wait and wait > 0, "scheduler would sleep forever instead of waking"
    finally:
        q.shutdown()


def test_resume_overrides_the_backoff():
    """Clicking Resume on a stalled row must do something visible."""
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 4}])
    try:
        t = T.DownloadTask("magnet:?xt=urn:btih:abc", "x", filename="x")
        t.status = T.QUEUED
        t.retry_after = time.time() + 3600
        t.stall_count = 3
        queue_manager.heapq.heappush(q._heap, t)
        q.resume_task(t)
        assert t.retry_after == 0.0 and t.stall_count == 0
        ready, _ = q._next_ready()
        assert ready is t
    finally:
        q.shutdown()


def test_the_card_says_why_it_is_queued():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", "x", filename="x",
                       total_size=1000)
    t.status = T.QUEUED
    t.retry_after = time.time() + 90
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert "Stalled" in card.sub.text()
    assert "retrying in" in card.sub.text()
