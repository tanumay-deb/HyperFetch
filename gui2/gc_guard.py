"""Python's garbage collector, kept to the GUI thread.

A Qt object has to be destroyed on the thread it lives on. Python's cyclic
collector runs on whichever thread is allocating when a threshold trips, and in
this app that is usually a download segment, a torrent's poll or the local
server answering the browser. Something Qt held only by a reference cycle - a
closed dialog and the lambdas on its buttons - could be torn down from there.
That crashed the test run's interpreter (see tests/conftest.py); the app had
the same exposure and no such care.

So automatic collection is switched off and a timer on the GUI thread collects
instead. Measured with the app's modules and yt-dlp's 1751 extractors loaded:
a full collection takes 6-8 ms, the young generations next to nothing.
"""
import gc

from PySide6.QtCore import QTimer

EVERY_MS = 2000         # generations 0 and 1: what the last two seconds left
FULL_EVERY = 15         # every 15th time, so each half minute: everything


def keep_on_gui_thread(parent):
    """Collect from a timer owned by `parent`, and nowhere else. Call it from
    the GUI thread. Returns the timer; `parent` keeps it alive."""
    gc.disable()
    ticks = [0]

    def collect():
        ticks[0] += 1
        gc.collect(2 if ticks[0] % FULL_EVERY == 0 else 1)

    timer = QTimer(parent)
    timer.timeout.connect(collect)
    timer.start(EVERY_MS)
    return timer
