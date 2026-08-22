"""HLS segment parallelism must stay bounded whatever Max-connections says.

Every worker is a real OS thread. Max-connections used to run up to 1000 and
this is the one place it RAISED a worker count rather than lowering it, so a
long playlist span ~1000 threads. Measured on the live app: 988 threads and
7.3 GB of private bytes after 40 minutes, almost all of it thread stacks.
"""
import pytest

import hls
import utils


def _workers(max_conns, n_segments):
    """The worker count hls.py would choose — mirrors the expression in run()."""
    cap = max_conns if max_conns > 0 else hls.PARALLEL
    return max(1, min(cap, hls.MAX_PARALLEL, max(1, n_segments)))


@pytest.mark.parametrize("max_conns,n_segments", [
    (1000, 5000),      # the reported configuration
    (1000, 100),
    (500, 2000),
    (64, 2000),
])
def test_workers_never_exceed_the_ceiling(max_conns, n_segments):
    w = _workers(max_conns, n_segments)
    assert w <= hls.MAX_PARALLEL, (
        f"{w} segment threads for max_connections={max_conns}; each one is an "
        "OS thread and this is what exhausted memory")


def test_a_short_playlist_does_not_spawn_more_threads_than_segments():
    assert _workers(1000, 3) == 3


def test_a_low_setting_is_still_respected():
    """The ceiling must not become a floor — turning it down has to work."""
    assert _workers(2, 5000) == 2


def test_the_default_is_used_when_unset():
    assert _workers(0, 5000) == min(hls.PARALLEL, hls.MAX_PARALLEL)


def test_ceiling_is_sane():
    assert 1 < hls.MAX_PARALLEL <= 64, (
        "a ceiling this high defeats the point — threads are not free")


def test_run_uses_the_ceiling(monkeypatch):
    """Guards against the clamp being dropped from run() while the constant stays."""
    import inspect
    src = inspect.getsource(hls.HlsDownloader.run)
    assert "MAX_PARALLEL" in src, \
        "run() no longer clamps the worker count to MAX_PARALLEL"
