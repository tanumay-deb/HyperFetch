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


# ---- the app itself ------------------------------------------------------------
# conftest.py only looks after the tests. The app had no such care: with a
# window open, the collector ran on whichever download, torrent or server thread
# happened to be allocating, and could tear a Qt object down from there.

def _run(code):
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run([sys.executable, "-c", code % root], capture_output=True,
                          text=True, timeout=120, env=env)


def test_the_app_collects_on_the_gui_thread_and_nowhere_else():
    out = _run(r"""
import gc, sys, threading
sys.path.insert(0, %r)
from PySide6.QtCore import QCoreApplication, QTimer
from gui2 import gc_guard

app = QCoreApplication([])
ran_on = []
gc.callbacks.append(lambda phase, info: phase == "start"
                    and ran_on.append(threading.current_thread().name))
gc_guard.EVERY_MS = 20
timer = gc_guard.keep_on_gui_thread(app)

def churn():                            # cycles, enough to trip any threshold
    for _ in range(300000):
        a = []
        a.append(a)

worker = threading.Thread(target=churn, name="worker")
worker.start()
QTimer.singleShot(700, app.quit)
app.exec()
worker.join()
print("RAN_ON", sorted(set(ran_on)), "AUTOMATIC", gc.isenabled())
""")
    assert "RAN_ON ['MainThread'] AUTOMATIC False" in out.stdout, out.stdout + out.stderr


def test_everything_is_collected_now_and_then_not_only_the_young():
    out = _run(r"""
import gc, sys
sys.path.insert(0, %r)
from PySide6.QtCore import QCoreApplication, QTimer
from gui2 import gc_guard

app = QCoreApplication([])
generations = []
gc.callbacks.append(lambda phase, info: phase == "start"
                    and generations.append(info["generation"]))
gc_guard.EVERY_MS = 10
gc_guard.FULL_EVERY = 4
timer = gc_guard.keep_on_gui_thread(app)
QTimer.singleShot(400, app.quit)
app.exec()
print("FULL", 2 in generations, "YOUNG", 1 in generations)
""")
    assert "FULL True YOUNG True" in out.stdout, out.stdout + out.stderr


def test_the_window_asks_for_it_and_the_headless_server_does_not():
    import inspect
    import api_server
    from gui2 import app
    assert "gc_guard.keep_on_gui_thread(" in inspect.getsource(app.run_v2)
    assert "gc_guard" not in inspect.getsource(api_server), (
        "the headless server has no Qt objects to protect")

