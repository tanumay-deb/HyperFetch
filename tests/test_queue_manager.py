"""QueueManager scheduling — deterministic, no network (worker is faked)."""
import time
import threading

import pytest

import task as T
import queue_manager
from queue_manager import QueueManager


class FakeDownloader:
    """Stand-in for the real Downloader: simulates a ~0.6s download that honors
    pause/cancel, so we can test scheduling without touching the network."""
    instances = []

    def __init__(self, task, segments=8):
        self.t = task
        FakeDownloader.instances.append(task)

    def run(self):
        self.t.status = T.DOWNLOADING
        for _ in range(12):
            if self.t.cancel_requested:
                self.t.status = T.CANCELLED
                return
            if self.t.pause_requested:
                self.t.status = T.PAUSED
                return
            time.sleep(0.05)
        self.t.status = T.COMPLETED


@pytest.fixture
def fake_worker(monkeypatch):
    FakeDownloader.instances = []
    monkeypatch.setattr(queue_manager, "Downloader", FakeDownloader)
    yield FakeDownloader


def _wait(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_concurrency_cap(fake_worker):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 2}])
    peak = [0]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak[0] = max(peak[0], q.active)
            time.sleep(0.01)
    s = threading.Thread(target=sampler, daemon=True)
    s.start()

    tasks = [q.add_task(T.DownloadTask("u", f"p{i}")) for i in range(6)]
    assert _wait(lambda: all(t.status == T.COMPLETED for t in tasks))
    stop.set()
    s.join(timeout=1)
    q.shutdown()
    assert peak[0] <= 2


def test_pause_queued_never_runs(fake_worker):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    block = q.add_task(T.DownloadTask("u", "block"))
    later = q.add_task(T.DownloadTask("u", "later"))
    q.pause_task(later)              # pause before it gets a slot
    assert _wait(lambda: block.status == T.COMPLETED)
    time.sleep(0.3)
    assert later.status == T.PAUSED
    assert later not in fake_worker.instances
    q.shutdown()


def test_resume_runs_to_completion(fake_worker):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 2}])
    t = q.add_task(T.DownloadTask("u", "r"))
    q.pause_task(t)
    time.sleep(0.1)
    q.resume_task(t)
    assert _wait(lambda: t.status == T.COMPLETED)
    q.shutdown()


def test_cancel_queued(fake_worker):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    block = q.add_task(T.DownloadTask("u", "block"))
    victim = q.add_task(T.DownloadTask("u", "victim"))
    q.cancel_task(victim)
    assert _wait(lambda: block.status == T.COMPLETED)
    assert victim.status == T.CANCELLED
    q.shutdown()


def test_get_task(fake_worker):
    q = QueueManager()
    t = q.add_task(T.DownloadTask("u", "p"))
    assert q.get_task(t.id) is t
    assert q.get_task("nonexistent") is None
    q.shutdown()


def test_remove_finished(fake_worker):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 4}])
    done = q.add_task(T.DownloadTask("u", "d"))
    assert _wait(lambda: done.status == T.COMPLETED)
    paused = T.DownloadTask("u", "p")
    paused.status = T.PAUSED
    q.add_task(paused, start=False)
    q.remove_finished()
    assert done not in q.tasks and paused in q.tasks
    q.shutdown()


def test_wait_active_drains_paused_workers(fake_worker):
    """closeEvent uses wait_active to block until in-flight workers actually
    return, so the last chunk gets flushed instead of dying with the process."""
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 2}])
    a = q.add_task(T.DownloadTask("u", "a"))
    b = q.add_task(T.DownloadTask("u", "b"))
    assert _wait(lambda: a.status == T.DOWNLOADING and b.status == T.DOWNLOADING)
    q.pause_task(a); q.pause_task(b)
    drained = q.wait_active(timeout=5.0)
    assert drained, "wait_active should return True when workers finish in time"
    assert q.active == 0
    q.shutdown()


def test_wait_active_times_out_cleanly(fake_worker):
    """If a worker won't unwind, wait_active returns False rather than blocking."""
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    t = q.add_task(T.DownloadTask("u", "x"))
    assert _wait(lambda: t.status == T.DOWNLOADING)
    # do NOT pause -> fake worker keeps running ~0.6s; ask for 0.1s
    drained = q.wait_active(timeout=0.1)
    assert drained is False
    q.pause_task(t)
    q.wait_active(timeout=2.0)
    q.shutdown()


def test_scheduler_not_busy_spin(fake_worker):
    q = QueueManager()
    c0 = time.process_time()
    time.sleep(1.5)                  # idle queue
    cpu = time.process_time() - c0
    q.shutdown()
    assert cpu < 0.5, f"idle scheduler burned {cpu:.2f}s CPU (busy spin)"


def test_move_to_queue_wakes_scheduler(fake_worker):
    """Moving a QUEUED task to a queue with free capacity must start it promptly
    (the bare field-write used to leave it parked)."""
    from queue_manager import Queue
    q = QueueManager(queues=[{"name": "A", "max_concurrent": 1},
                             {"name": "B", "max_concurrent": 1}])
    a1 = T.DownloadTask("u", "a1"); a1.queue_name = "A"
    a2 = T.DownloadTask("u", "a2"); a2.queue_name = "A"
    q.add_task(a1); q.add_task(a2)
    assert _wait(lambda: a1.status == T.DOWNLOADING)   # A runs one
    # a2 is stuck behind a1 in queue A (cap 1). Move it to free queue B.
    q.move_to_queue(a2, "B")
    assert _wait(lambda: a2.status == T.DOWNLOADING, timeout=3), \
        "moved task did not start — scheduler not woken"
    q.shutdown()


def test_set_max_concurrent_admits_waiting_tasks(fake_worker):
    """Raising a queue's cap at runtime should admit a waiting task."""
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    t1 = T.DownloadTask("u", "1")
    t2 = T.DownloadTask("u", "2")
    q.add_task(t1); q.add_task(t2)
    assert _wait(lambda: t1.status == T.DOWNLOADING)
    assert t2.status == T.QUEUED                       # capped at 1
    q.set_max_concurrent("Main", 2)
    assert _wait(lambda: t2.status == T.DOWNLOADING, timeout=3), \
        "raising the cap did not admit the waiting task"
    q.shutdown()


def test_move_running_task_does_not_corrupt_slot_accounting(fake_worker):
    """A DOWNLOADING task moved between queues keeps charging its ORIGINAL
    queue's slot (bound at start), so no leak / negative active count."""
    q = QueueManager(queues=[{"name": "A", "max_concurrent": 1},
                             {"name": "B", "max_concurrent": 1}])
    a = T.DownloadTask("u", "a"); a.queue_name = "A"
    q.add_task(a)
    assert _wait(lambda: a.status == T.DOWNLOADING)
    q.move_to_queue(a, "B")                             # move while running
    assert _wait(lambda: a.status == T.COMPLETED, timeout=5)
    # both queues settle at 0 active — no leak in A, no negative in B
    assert q.queues["A"].active == 0
    assert q.queues["B"].active == 0
    q.shutdown()


def test_concurrency_stress_no_deadlock_no_leak(fake_worker):
    """30 tasks across 3 queues drain fully with no deadlock, and every slot
    is released — global active and every per-queue active settle at 0."""
    q = QueueManager(queues=[{"name": "A", "max_concurrent": 3},
                             {"name": "B", "max_concurrent": 2},
                             {"name": "C", "max_concurrent": 4}])
    tasks = []
    for i in range(30):
        t = T.DownloadTask("u", f"s{i}")
        t.queue_name = ["A", "B", "C"][i % 3]
        q.add_task(t)
        tasks.append(t)
    # everything completes within a bounded time -> no deadlock
    assert _wait(lambda: all(t.status == T.COMPLETED for t in tasks), timeout=30), \
        "stress run did not drain — possible deadlock"
    # every release accounted for
    assert q.active == 0
    for name in ("A", "B", "C"):
        assert q.queues[name].active == 0, f"queue {name} leaked a slot"
    q.shutdown()
