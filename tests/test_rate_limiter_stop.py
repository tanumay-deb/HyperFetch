"""A Pause is not held up by the speed limit (utils.RateLimiter.wait)."""
import threading
import time

import utils


def test_a_wait_under_a_low_limit_ends_when_asked_to_stop():
    """One 64 KiB chunk at 8 KB/s waited 8 s, and a Pause with it."""
    lim = utils.RateLimiter()
    lim.set_limit(8000)
    lim.wait(8000)                              # empty the bucket
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    lim.wait(65536, stop.is_set)
    assert time.monotonic() - t0 < 1.5
    t0 = time.monotonic()
    lim.wait(8000)                              # and it still limits afterwards
    assert time.monotonic() - t0 > 0.5
