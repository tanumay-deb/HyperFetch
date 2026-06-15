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
    q = QueueManager(max_concurrent=2)
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
    q = QueueManager(max_concurrent=1)
    block = q.add_task(T.DownloadTask("u", "block"))
    later = q.add_task(T.DownloadTask("u", "later"))
    q.pause_task(later)              # pause before it gets a slot
    assert _wait(lambda: block.status == T.COMPLETED)
    time.sleep(0.3)
    assert later.status == T.PAUSED
    assert later not in fake_worker.instances
    q.shutdown()


def test_resume_runs_to_completion(fake_worker):
    q = QueueManager(max_concurrent=2)
    t = q.add_task(T.DownloadTask("u", "r"))
    q.pause_task(t)
    time.sleep(0.1)
    q.resume_task(t)
    assert _wait(lambda: t.status == T.COMPLETED)
    q.shutdown()


def test_cancel_queued(fake_worker):
    q = QueueManager(max_concurrent=1)
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
    q = QueueManager(max_concurrent=4)
    done = q.add_task(T.DownloadTask("u", "d"))
    assert _wait(lambda: done.status == T.COMPLETED)
    paused = T.DownloadTask("u", "p")
    paused.status = T.PAUSED
    q.add_task(paused, start=False)
    q.remove_finished()
    assert done not in q.tasks and paused in q.tasks
    q.shutdown()


def test_scheduler_not_busy_spin(fake_worker):
    q = QueueManager()
    c0 = time.process_time()
    time.sleep(1.5)                  # idle queue
    cpu = time.process_time() - c0
    q.shutdown()
    assert cpu < 0.5, f"idle scheduler burned {cpu:.2f}s CPU (busy spin)"
