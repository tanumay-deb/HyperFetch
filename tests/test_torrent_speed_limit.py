"""Torrents keep to the download speed limit.

    the queue    works out the torrents' part from what is running, and leaves
                 the rest to this app's own connections
    the engine   tells aria2 its part, and each torrent its own limit, whenever
                 either changes

What aria2 does with them was measured, not assumed (aria2 1.37.0, two aria2c
and a tracker on this machine, 2026-10-05):

  * changeGlobalOption(max-overall-download-limit) and
    changeOption(gid, max-download-limit) both take hold on a torrent that is
    already running, up and down, and read back. Neither restarts it: the same
    gid, its progress kept, and no stopped/started announce.
  * A limit is an average over about ten seconds. From a source far faster
    than the limit the data comes in bursts.
"""
import threading
import time

import pytest

import aria2d
import queue_manager
import task as T
import torrent
import utils
from queue_manager import QueueManager

L = 1_000_000
MAGNET = "magnet:?xt=urn:btih:%s&dn=Movie"


def _magnet(n=0):
    return T.DownloadTask(MAGNET % ("%040x" % (n + 1)), "p%d" % n)


def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def limit(monkeypatch):
    """The app's bucket, set to 1 MB/s for the test and no one else."""
    rl = utils.RateLimiter()
    monkeypatch.setattr(utils, "global_limiter", rl)
    rl.set_limit(L)
    return rl


# ---- the queue ------------------------------------------------------------
class Runs:
    """Downloads until told to stop. A torrent reports the speed it is given."""

    speed = {}

    def __init__(self, task, segments=8):
        self.t = task

    def run(self):
        t = self.t
        t.status = T.DOWNLOADING
        t.tor_download = Runs.speed.get(t.id, 0)
        while not (t.cancel_requested or t.pause_requested):
            time.sleep(0.01)
        t.tor_download = 0
        t.status = T.CANCELLED if t.cancel_requested else T.PAUSED


@pytest.fixture
def queue(monkeypatch, limit):
    monkeypatch.setattr(queue_manager, "Downloader", Runs)
    Runs.speed = {}
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 8}])
    yield q
    for t in list(q.tasks):
        t.request_cancel()
    q.shutdown()
    q.wait_active(5)


def _running(q, *tasks):
    for t in tasks:
        q.add_task(t)
    assert _wait(lambda: all(t.status == T.DOWNLOADING for t in tasks))


def test_a_torrent_alone_may_use_the_whole_limit(queue, limit):
    _running(queue, _magnet())
    assert queue.torrent_speed_limit() == L
    assert limit.rate == L


def test_with_another_download_running_each_has_its_part(queue, limit):
    tor, web = _magnet(), T.DownloadTask("https://example.test/a.zip", "a.zip")
    Runs.speed[tor.id] = 500_000                # using all of its half
    _running(queue, tor, web)
    assert queue.torrent_speed_limit() == 500_000
    assert limit.rate == 500_000


def test_a_torrent_doing_nothing_holds_nothing_back(queue, limit):
    tor, web = _magnet(), T.DownloadTask("https://example.test/a.zip", "a.zip")
    _running(queue, tor, web)                   # no seeders: 0 bytes a second
    assert queue.torrent_speed_limit() == 500_000
    assert limit.rate == L


def test_only_torrents_that_are_downloading_count(queue, limit):
    web = T.DownloadTask("https://example.test/a.zip", "a.zip")
    _running(queue, web)
    seeding, waiting, paused = _magnet(1), _magnet(2), _magnet(3)
    for t, status in ((seeding, T.COMPLETED), (waiting, T.QUEUED), (paused, T.PAUSED)):
        queue.add_task(t, start=False)
        t.status = status
    seeding.seeding = True
    assert queue.torrent_speed_limit() == L
    assert limit.rate == L


def test_with_no_limit_there_are_no_parts(queue, limit):
    limit.set_limit(0)
    _running(queue, _magnet(), T.DownloadTask("https://example.test/a.zip", "a.zip"))
    assert queue.torrent_speed_limit() == 0
    assert limit.rate == 0


def test_when_the_last_torrent_stops_the_rest_has_the_limit_back(queue, limit):
    tor, web = _magnet(), T.DownloadTask("https://example.test/a.zip", "a.zip")
    Runs.speed[tor.id] = 500_000
    _running(queue, tor, web)
    queue.torrent_speed_limit()
    assert limit.rate == 500_000

    queue.pause_task(tor)                       # nothing polls aria2 any more

    assert _wait(lambda: limit.rate == L, timeout=5), (
        f"an ordinary download is still held to {limit.rate}")


def test_a_running_download_can_ask_for_the_torrents_part(queue, limit):
    tor = _magnet()
    _running(queue, tor)
    assert tor._speed_share() == L

    queue.pause_task(tor)

    assert _wait(lambda: tor.status == T.PAUSED and tor._speed_share is None)


# ---- the engine ------------------------------------------------------------
class _Aria:
    """aria2 with a torrent downloading. Remembers what it is told of limits."""

    def __init__(self, speed=250_000):
        self.port, self.secret = 6800, "first"
        self.speed = speed
        self.overall = []           # each max-overall-download-limit it was sent
        self.own = []               # (gid, max-download-limit)
        self.refuse = 0             # how many times to fail before taking one
        self._gids = 0
        self._lock = threading.Lock()

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        with self._lock:
            if method in ("aria2.addUri", "aria2.addTorrent"):
                self._gids += 1
                return "gid%d" % self._gids
            if method == "aria2.changeGlobalOption":
                if self.refuse > 0:
                    self.refuse -= 1
                    raise aria2d.Aria2Error("busy")
                self.overall.append(params[0]["max-overall-download-limit"])
                return "OK"
            if method == "aria2.changeOption":
                self.own.append((params[0], params[1]["max-download-limit"]))
                return "OK"
            if method == "aria2.tellStatus":
                return {"status": "active", "totalLength": "1000000",
                        "completedLength": "1000", "connections": "3",
                        "numSeeders": "2", "downloadSpeed": str(self.speed),
                        "files": [{"path": "x"}]}
            return {}


class _Run:
    """One torrent run on a thread, stopped when the test is done with it."""

    def __init__(self, daemon, task):
        self.t = task
        self.th = threading.Thread(
            target=torrent.TorrentDownloader(task)._run_rpc, daemon=True)
        self.th.start()

    def stop(self):
        self.t.request_pause()
        self.th.join(timeout=3)
        assert not self.th.is_alive()


@pytest.fixture
def aria(tmp_path, monkeypatch, limit):
    d = _Aria()
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.01)
    monkeypatch.setattr(aria2d, "DAEMON", d)
    runs = []

    def start(task=None):
        task = task or T.DownloadTask(MAGNET % ("a" * 40), str(tmp_path / "download.bin"))
        task.status = T.DOWNLOADING
        runs.append(_Run(d, task))
        return task

    d.start = start
    yield d
    for r in runs:
        r.stop()


def _polled(d, t, times=6):
    """Wait until the engine has been round its loop a few times more."""
    seen = []
    t._seen = seen
    first = time.time()
    assert _wait(lambda: t.tor_download == d.speed), "the engine never polled"
    time.sleep(0.01 * times * 3)
    return time.time() - first


def test_aria2_is_told_the_limit_once(aria, limit):
    t = aria.start()
    _polled(aria, t)
    assert aria.overall == [str(L)], "told %d times" % len(aria.overall)


def test_it_is_told_again_when_the_limit_changes(aria, limit):
    t = aria.start()
    _polled(aria, t)
    limit.set_limit(2 * L)
    assert _wait(lambda: aria.overall == [str(L), str(2 * L)]), aria.overall
    limit.set_limit(0)
    assert _wait(lambda: aria.overall[-1] == "0"), "the limit was never lifted"


def test_with_no_limit_it_is_told_so(aria, limit):
    """This daemon may be one an earlier run left behind, limit and all."""
    limit.set_limit(0)
    t = aria.start()
    _polled(aria, t)
    assert aria.overall == ["0"]


def test_the_torrents_part_comes_from_the_queue_when_there_is_one(aria, limit):
    t = T.DownloadTask(MAGNET % ("a" * 40), "p")
    t._speed_share = lambda: 250_000
    aria.start(t)
    _polled(aria, t)
    assert aria.overall == ["250000"]


def test_a_second_torrent_does_not_tell_it_again(aria, limit):
    a = aria.start()
    b = aria.start(T.DownloadTask(MAGNET % ("b" * 40), "p2"))
    _polled(aria, a)
    _polled(aria, b)
    assert aria.overall == [str(L)]


def test_a_daemon_that_took_its_place_is_told_from_the_start(aria, limit):
    t = aria.start()
    _polled(aria, t)
    aria.secret = "second"              # what a daemon started afresh has anew
    assert _wait(lambda: aria.overall == [str(L), str(L)]), aria.overall


def test_a_telling_that_failed_is_tried_again(aria, limit):
    aria.refuse = 2
    t = aria.start()
    assert _wait(lambda: aria.overall == [str(L)]), "gave up after a busy daemon"


def test_a_torrents_own_limit_reaches_aria2(aria, limit):
    t = T.DownloadTask(MAGNET % ("a" * 40), "p", speed_limit=100_000)
    aria.start(t)
    _polled(aria, t)
    assert aria.own == [("gid1", "100000")], aria.own

    t.set_speed_limit(40_000)           # the card's Set Speed Limit, mid-download
    assert _wait(lambda: aria.own[-1] == ("gid1", "40000")), aria.own
    t.set_speed_limit(0)
    assert _wait(lambda: aria.own[-1] == ("gid1", "0")), "its limit was never lifted"


def test_a_torrent_with_no_limit_of_its_own_says_so_once(aria, limit):
    """The daemon's entry may carry one from before it was paused."""
    t = aria.start()
    _polled(aria, t)
    assert aria.own == [("gid1", "0")]


def test_how_fast_a_torrent_is_coming_down_is_kept_for_the_queue(aria, limit):
    t = aria.start()
    _polled(aria, t)
    assert t.tor_download == 250_000
    t.request_pause()
    assert _wait(lambda: t.status == T.PAUSED)
    time.sleep(0.05)
    assert t.tor_download == 0, "a paused torrent still counts as downloading that fast"

