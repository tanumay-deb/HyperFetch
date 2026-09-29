"""Which queued torrent starts next, and whether a stuck one has anyone to give
way to (queue_manager.py with swarm.py).

When the next task in line is a torrent, the healthiest ready torrent of the
same queue starts in its place: known seeders (most first), then unknown, then
known to have none - ahead of the priority setting, which breaks ties. Web
downloads keep their place in line.
"""
import threading
import time

import queue_manager
import task as T
from queue_manager import QueueManager


def _tor(name, prio=0, seeds=None, queue="Main"):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + name.ljust(40, "0"), name,
                       filename=name, priority=prio, queue_name=queue)
    if seeds is not None:
        t.swarm_seeds, t.swarm_at = seeds, time.time()
    return t


def _web(name, prio=0):
    return T.DownloadTask("https://x.example/%s.zip" % name, name, filename=name, priority=prio)


def _qm(queues=None):
    return QueueManager(queues=queues or [{"name": "Main", "max_concurrent": 1}])


def _pick(*tasks, queues=None):
    q = _qm(queues)
    try:
        for t in tasks:
            queue_manager.heapq.heappush(q._heap, t)
        ready, _ = q._next_ready()
        return ready
    finally:
        q.shutdown()


def test_seeders_beat_priority():
    dead_first = _tor("dead", prio=0, seeds=0)
    alive_later = _tor("alive", prio=5, seeds=12)
    assert _pick(dead_first, alive_later) is alive_later


def test_most_seeders_first():
    few = _tor("few", prio=0, seeds=2)
    many = _tor("many", prio=5, seeds=30)
    assert _pick(few, many) is many


def test_unknown_before_known_dead():
    dead = _tor("dead", prio=0, seeds=0)
    unknown = _tor("unknown", prio=5)
    assert _pick(dead, unknown) is unknown


def test_ties_go_to_priority_then_order():
    a, b = _tor("a", prio=0), _tor("b", prio=0)          # both unknown, a added first
    assert _pick(b, a) is a
    c, d = _tor("c", prio=3, seeds=5), _tor("d", prio=0, seeds=5)
    assert _pick(c, d) is d


def test_a_web_download_first_in_line_still_goes_first():
    web = _web("web", prio=0)
    alive = _tor("alive", prio=5, seeds=40)
    assert _pick(web, alive) is web


def test_each_queue_ranks_only_its_own_torrents():
    queues = [{"name": "Main", "max_concurrent": 1}, {"name": "Night", "max_concurrent": 1}]
    dead_main = _tor("dead", prio=0, seeds=0, queue="Main")
    alive_night = _tor("alive", prio=5, seeds=40, queue="Night")
    assert _pick(dead_main, alive_night, queues=queues) is dead_main


def test_a_backed_off_torrent_is_not_a_candidate():
    dead = _tor("dead", prio=0, seeds=0)
    waiting = _tor("alive", prio=5, seeds=40)
    waiting.retry_after = time.time() + 3600
    assert _pick(dead, waiting) is dead


def test_the_picked_one_leaves_the_queue_and_the_rest_stay():
    q = _qm()
    try:
        dead, alive = _tor("dead", seeds=0), _tor("alive", prio=5, seeds=9)
        for t in (dead, alive):
            queue_manager.heapq.heappush(q._heap, t)
        ready, _ = q._next_ready()
        assert ready is alive
        assert q._heap == [dead]
    finally:
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
        first = _tor("first", prio=0, seeds=3)
        behind = _tor("behind", prio=5)                    # unknown: alive enough
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
