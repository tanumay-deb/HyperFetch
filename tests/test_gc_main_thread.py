"""The garbage collector runs on the main thread, never on another.

Qt objects must be destroyed on the GUI thread, but Python's cyclic collector
runs on whichever thread is allocating when a threshold trips. A widget held
only by a reference cycle was therefore torn down now and then by a downloader
worker or a stand-in server's handler, and CI's interpreter segfaulted.
conftest.py turns automatic collection off and collects between tests, on the
main thread; this keeps it that way.
"""
import gc
import threading


def test_a_busy_worker_thread_never_runs_the_collector():
    ran_on = []

    def note(phase, info):
        if phase == "start":
            ran_on.append(threading.current_thread().name)

    def churn():
        for _ in range(20_000):            # cycles, enough to trip any threshold
            a = []
            a.append(a)

    gc.callbacks.append(note)
    try:
        t = threading.Thread(target=churn, name="worker")
        t.start()
        t.join()
    finally:
        gc.callbacks.remove(note)
    assert "worker" not in ran_on
