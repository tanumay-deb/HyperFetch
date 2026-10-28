"""Which waiting download starts next, and whether a stuck torrent has anyone
to give way to (queue_manager.py with swarm.py).

Asked 2026-10-28: the "#" on a card is the order downloads go in - the first
one added is #1 and starts first. One that is resumed goes back to its place
by number (it used to join the end of the line), and a torrent with seeders
no longer starts ahead of a lower number with none (the queue used to rank
torrents by their swarm first). The lowest waiting number whose backoff is
over starts; a running download is never stopped for it.
"""
import threading
import time

import queue_manager
import task as T
from queue_manager import QueueManager


def _tor(name, number=0, seeds=None, queue="Main"):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + name.ljust(40, "0"), name,
                       filename=name, queue_name=queue)
    t.added = number or t.added                           # its "#"
    if seeds is not None:
        t.swarm_seeds, t.swarm_at = seeds, time.time()
    return t


def _web(name, number):
    t = T.DownloadTask("https://x.example/%s.zip" % name, name, filename=name)
    t.added = number
    return t


def _qm(queues=None):
    return QueueManager(queues=queues or [{"name": "Main", "max_concurrent": 1}])


def _line(*tasks):
    """The waiting line after `tasks` are queued in that order, and who starts."""
    q = _qm()
    try:
        with q.cond:
            for t in tasks:
                q._push(t)
            line = [t.filename for t in sorted(q._heap)]
        ready, _ = q._next_ready()
        return line, ready
    finally:
        q.shutdown()


def test_the_lowest_number_starts_first_seeders_or_not():
    dead_1, alive_2 = _tor("one", 1, seeds=0), _tor("two", 2, seeds=40)
    line, ready = _line(alive_2, dead_1)
    assert line == ["one", "two"] and ready is dead_1


def test_a_resumed_download_goes_back_to_its_place():
    one, two, three = _tor("one", 1), _tor("two", 2), _tor("three", 3)
    line, ready = _line(two, three, one)                 # #1 was paused, now resumed
    assert line == ["one", "two", "three"] and ready is one


def test_a_new_download_goes_last():
    assert _line(_tor("one", 1), _tor("two", 2), _tor("three", 3))[0] == ["one", "two", "three"]


def test_web_downloads_and_torrents_share_one_line():
    assert _line(_tor("tor", 2, seeds=40), _web("web", 1))[1].filename == "web"


def test_a_backed_off_torrent_waits_and_the_next_number_runs():
    one, two = _tor("one", 1), _tor("two", 2)
    one.retry_after = time.time() + 3600
    assert _line(one, two)[1] is two


def test_the_picked_one_leaves_the_line_and_the_rest_stay():
    q = _qm()
    try:
        one, two = _tor("one", 1, seeds=0), _tor("two", 2, seeds=9)
        with q.cond:
            q._push(two)
            q._push(one)
        ready, _ = q._next_ready()
        assert ready is one and q._heap == [two]
    finally:
        q.shutdown()


class _Held:
    """Stands in for the engine: a download runs until let go, or paused."""
    started, go = [], {}

    def __init__(self, task, segments=8):
        self.t = task

    def run(self):
        _Held.started.append(self.t.filename)
        self.t.status = T.DOWNLOADING
        go = _Held.go.setdefault(self.t.filename, threading.Event())
        while not go.wait(0.02):
            if self.t.pause_requested:
                self.t.status = T.PAUSED
                return
        self.t.status = T.COMPLETED


def _until(cond, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end and not cond():
        time.sleep(0.02)
    assert cond(), _Held.started


def test_one_paused_and_resumed_later_is_next_when_the_slot_frees(monkeypatch):
    """What was asked: max downloads 1, #1 paused - so #2 starts - and resumed
    later. #2 goes on, and #1 is next when #2 is done, before #3."""
    monkeypatch.setattr(queue_manager, "Downloader", _Held)
    _Held.started, _Held.go = [], {}
    q = _qm()
    try:
        one, two, three = _web("one", 1), _web("two", 2), _web("three", 3)
        for t in (one, two, three):
            q.add_task(t)
        _until(lambda: _Held.started == ["one"])
        q.pause_task(one)
        _until(lambda: _Held.started == ["one", "two"])
        q.resume_task(one)
        time.sleep(0.3)
        assert _Held.started == ["one", "two"], "a running download was stopped for it"
        _Held.go["two"].set()                              # #2 is done
        _until(lambda: len(_Held.started) == 3)
        assert _Held.started == ["one", "two", "one"]
    finally:
        for go in list(_Held.go.values()):
            go.set()
        q.shutdown()


# ---- does a stuck torrent have anyone to give way to? ------------------------

def test_only_a_live_ready_torrent_of_the_same_queue_counts():
    queues = [{"name": "Main", "max_concurrent": 1}, {"name": "Night", "max_concurrent": 1}]
    q = _qm(queues)
    try:
        running = _tor("running", seeds=0)
        assert q._live_candidate_waiting(running, "Main") is False, "nothing waiting"
        for t in (_tor("dead", seeds=0),
                  _tor("later", seeds=9),
                  _tor("night", seeds=9, queue="Night")):
            queue_manager.heapq.heappush(q._heap, t)
        q._heap[[t.filename for t in q._heap].index("later")].retry_after = time.time() + 600
        assert q._live_candidate_waiting(running, "Main") is False, \
            "dead, backed off and other-queue torrents are no reason to give way"
        queue_manager.heapq.heappush(q._heap, _tor("unknown"))
        assert q._live_candidate_waiting(running, "Main") is True
    finally:
        q.shutdown()


class _AsksDownloader:
    """Stands in for the engine: records what a running torrent can ask."""
    seen = []

    def __init__(self, task, segments=8):
        self.t = task

    def run(self):
        hook = getattr(self.t, "_better_waiting", None)
        _AsksDownloader.seen.append((self.t.filename, callable(hook) and hook()))
        self.t.status = T.COMPLETED


def test_a_running_torrent_can_ask_whether_someone_better_waits(monkeypatch):
    monkeypatch.setattr(queue_manager, "Downloader", _AsksDownloader)
    _AsksDownloader.seen = []
    q = _qm()
    try:
        first = _tor("first", seeds=3)
        behind = _tor("behind")                            # unknown: alive enough
        q.add_task(first)
        q.add_task(behind)
        deadline = time.time() + 5
        while time.time() < deadline and len(_AsksDownloader.seen) < 2:
            time.sleep(0.02)
        assert _AsksDownloader.seen[0] == ("first", True), _AsksDownloader.seen
        assert _AsksDownloader.seen[1] == ("behind", False)
        assert getattr(first, "_better_waiting", None) is None, "the hook outlived its run"
    finally:
        q.shutdown()
