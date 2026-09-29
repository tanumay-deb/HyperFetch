"""Saying when a torrent gave its slot back, and what started instead
(gui2/notify.py YieldNotifier)."""
from types import SimpleNamespace

import task as T
from gui2.notify import YieldNotifier


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _tor(i, name, status=T.QUEUED):
    return SimpleNamespace(id="t%d" % i, filename=name, url="magnet:?xt=urn:btih:%d" % i,
                           status=status, yielded_at=0.0, yield_reason="", retry_after=0.0)


def _yield(t, clock, reason="no seeders", retry=120):
    t.status, t.yielded_at, t.yield_reason = T.QUEUED, clock.t, reason
    t.retry_after = clock.t + retry


def test_one_swap_names_both_torrents():
    clock = _Clock()
    n = YieldNotifier(clock=clock)
    stuck, behind = _tor(1, "Stuck", T.DOWNLOADING), _tor(2, "Behind")
    tasks = [stuck, behind]
    assert n.tick(tasks) is None                 # the first look is not news
    _yield(stuck, clock)
    behind.status = T.DOWNLOADING                # started in its place
    assert n.tick(tasks) is None, "waits a moment for the rest of the story"
    clock.t += YieldNotifier.QUIET
    title, body = n.tick(tasks)
    assert title == 'Paused "Stuck"'
    assert "No seeders" in body and "2 min" in body and 'Started "Behind" instead' in body
    clock.t += 5
    assert n.tick(tasks) is None, "announced twice"


def test_no_peers_says_so():
    clock = _Clock()
    n = YieldNotifier(clock=clock)
    t = _tor(1, "Quiet", T.DOWNLOADING)
    n.tick([t])
    _yield(t, clock, reason="no peers", retry=300)
    n.tick([t])
    clock.t += YieldNotifier.QUIET
    title, body = n.tick([t])
    assert title == 'Paused "Quiet"' and "No peers" in body and "5 min" in body
    assert "instead" not in body, "nothing started in its place"


def test_several_at_once_are_one_notification():
    clock = _Clock()
    n = YieldNotifier(clock=clock)
    a, b = _tor(1, "A", T.DOWNLOADING), _tor(2, "B", T.DOWNLOADING)
    n.tick([a, b])
    _yield(a, clock)
    n.tick([a, b])
    clock.t += 0.5
    _yield(b, clock, reason="no peers")
    n.tick([a, b])
    clock.t += YieldNotifier.QUIET
    title, body = n.tick([a, b])
    assert title == "2 torrents paused"
    assert '"A" (no seeders)' in body and '"B" (no peers)' in body


def test_a_second_yield_of_the_same_torrent_is_news_again():
    clock = _Clock()
    n = YieldNotifier(clock=clock)
    t = _tor(1, "Again", T.DOWNLOADING)
    n.tick([t])
    _yield(t, clock)
    n.tick([t]); clock.t += YieldNotifier.QUIET
    assert n.tick([t])
    clock.t += 600
    t.status = T.DOWNLOADING; n.tick([t])
    _yield(t, clock, retry=300)
    n.tick([t]); clock.t += YieldNotifier.QUIET
    title, body = n.tick([t])
    assert "5 min" in body


def test_a_web_download_starting_meanwhile_is_not_named_the_replacement():
    clock = _Clock()
    n = YieldNotifier(clock=clock)
    stuck = _tor(1, "Stuck", T.DOWNLOADING)
    web = SimpleNamespace(id="w1", filename="setup.exe", url="https://x/setup.exe",
                          status=T.QUEUED, yielded_at=0.0, yield_reason="", retry_after=0.0)
    n.tick([stuck, web])
    _yield(stuck, clock)
    web.status = T.DOWNLOADING
    n.tick([stuck, web])
    clock.t += YieldNotifier.QUIET
    _, body = n.tick([stuck, web])
    assert "setup.exe" not in body, "only a torrent can take a torrent's slot"
