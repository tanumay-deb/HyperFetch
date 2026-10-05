"""A task is run by one worker at a time.

Reported as: "Force Recheck moves the torrent to Paused and rechecks; when the
recheck passes it stays Paused; Resume moves it to Active and it gets stuck."
The saved timelines of the five torrents it happened to all read the same:

    Completed, Seeding            the first run: finished, sharing, thread alive
    Force recheck requested
    Paused, Queued, Downloading   a SECOND run, started beside the first
    Paused                        the first, knocked off its torrent, wrote this
    Torrent engine restarted      ...over the second, which was still rechecking
    Verification finished
    Queued, Downloading           Resume: a THIRD run, on the second's torrent

A seeding torrent reads Completed and has handed its download slot back while
its thread goes on polling, so neither its status nor its slot says that a
worker is alive. Nothing else did either, and whatever started the task again
started it beside the worker it already had.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import types

import pytest

import aria2d
import queue_manager
import task as T
import torrent
import utils
from gui2.app_actions import ActionsMixin
from queue_manager import QueueManager

HASH = "a" * 40
MAGNET = "magnet:?xt=urn:btih:" + HASH + "&dn=Movie"

_lock = threading.Lock()
_live = {}          # task id -> workers running it right now
_peak = {}          # task id -> the most there ever were at once
_runs = {}          # task id -> runs started
_asked = {}         # task id -> whether each run was asked to recheck


class Engine:
    """Stands in for the real engines. A torrent finishes, seeds and gives its
    slot back, then polls until it is told to stop; anything else just runs
    until it is told to stop. Counts the workers on each task."""

    linger = 0.0    # how long the thread lives on after it has written its status

    def __init__(self, task, segments=8):
        self.t = task

    def run(self):
        t = self.t
        with _lock:
            _live[t.id] = _live.get(t.id, 0) + 1
            _peak[t.id] = max(_peak.get(t.id, 0), _live[t.id])
            _runs[t.id] = _runs.get(t.id, 0) + 1
            _asked.setdefault(t.id, []).append(bool(t.force_recheck))
        t.force_recheck = False
        try:
            t.status = T.DOWNLOADING
            time.sleep(0.03)
            if t.url.startswith("magnet:"):
                t.status = T.COMPLETED
                t.seeding = True
                release = getattr(t, "_release_slot", None)
                if release:
                    release()
            while not (t.cancel_requested or t.pause_requested):
                time.sleep(0.01)
            t.seeding = False
            t.status = T.CANCELLED if t.cancel_requested else T.PAUSED
            time.sleep(self.linger)
        finally:
            with _lock:
                _live[t.id] -= 1


def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def queues():
    """Makes queues, and ends every worker they started before the next test:
    a worker left over from a test that failed would be counted in the next."""
    made = []

    def make(limit=3):
        q = QueueManager(queues=[{"name": "Main", "max_concurrent": limit}])
        made.append(q)
        return q

    yield make
    for q in made:
        for t in list(q.tasks):
            t.request_cancel()
        q.shutdown()
    for q in made:
        q.wait_active(5)
    _wait(lambda: not any(_live.values()), timeout=5)


@pytest.fixture
def engine(monkeypatch, queues):
    for d in (_live, _peak, _runs, _asked):
        d.clear()
    monkeypatch.setattr(Engine, "linger", 0.0)
    monkeypatch.setattr(queue_manager, "Downloader", Engine)
    return queues


# ---- the queue ------------------------------------------------------------
def test_run_again_starts_the_next_run_only_after_the_live_one_has_ended(engine):
    q = engine()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding), "never reached seeding"

    q.run_again(t)

    assert _wait(lambda: _runs[t.id] == 2), "it never ran again"
    assert _peak[t.id] == 1, "two workers ran the task at once"


def test_the_next_run_is_not_stopped_by_what_stopped_the_last(engine):
    """The live run is asked to stop with the pause flag. Left set, it would
    pause the run that follows the moment it started."""
    q = engine()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding)

    q.run_again(t)

    assert _wait(lambda: _runs[t.id] == 2 and t.seeding)
    time.sleep(0.2)
    assert _live[t.id] == 1 and t.status == T.COMPLETED, (
        f"the second run did not stay up: status {t.status}")


def test_while_it_waits_its_turn_it_reads_queued(engine):
    """The run that was stopped writes Paused on its way out. The task is not
    paused: it is waiting to run again, and Paused offers a Resume button."""
    q = engine(limit=1)
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding)                 # its slot is free again
    blocker = q.add_task(T.DownloadTask("https://example.test/big.zip", "big.zip"))
    assert _wait(lambda: blocker.status == T.DOWNLOADING)

    q.run_again(t)

    assert _wait(lambda: _live[t.id] == 0), "the live run was never stopped"
    assert _wait(lambda: t.status == T.QUEUED, timeout=3), (
        f"reads {t.status} while it waits for the only slot")
    assert _runs[t.id] == 1, "started past the queue's limit"

    q.pause_task(blocker)
    assert _wait(lambda: _runs[t.id] == 2), "never got the slot once it was free"


def test_pausing_it_while_it_waits_calls_the_run_again_off(engine, monkeypatch):
    monkeypatch.setattr(Engine, "linger", 0.3)      # the old worker is still going
    q = engine()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding)

    q.run_again(t)
    q.pause_task(t)

    assert _wait(lambda: _live[t.id] == 0)
    time.sleep(0.3)
    assert _runs[t.id] == 1, "ran again although it was paused first"
    assert t.status == T.PAUSED

    q.resume_task(t)                                # and it is not lost for good
    assert _wait(lambda: _runs[t.id] == 2), "Resume no longer starts it"


def test_the_slot_count_is_right_afterwards(engine):
    q = engine()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding)
    q.run_again(t)
    assert _wait(lambda: _runs[t.id] == 2 and t.seeding)

    t.request_pause()

    assert _wait(lambda: _live[t.id] == 0)
    assert _wait(lambda: q.active == 0, timeout=3), f"active is {q.active}"
    assert q.queues["Main"].active == 0


@pytest.mark.parametrize("start", ["resume_task", "force_start"])
def test_a_start_does_not_overlap_a_worker_that_is_still_ending(engine, monkeypatch, start):
    """An engine writes Paused and then unwinds: closes files, tells aria2,
    returns. A Resume in that moment used to start the next worker beside it,
    and the first one's goodbye then wiped what the second had just set up."""
    monkeypatch.setattr(Engine, "linger", 0.3)
    q = engine()
    t = q.add_task(T.DownloadTask("https://example.test/big.zip", "big.zip"))
    assert _wait(lambda: t.status == T.DOWNLOADING)
    q.pause_task(t)
    assert _wait(lambda: t.status == T.PAUSED)
    assert _live[t.id] == 1, "the test needs the first worker still ending"

    getattr(q, start)(t)

    assert _wait(lambda: _runs[t.id] == 2), "it never started again"
    assert _peak[t.id] == 1, "started beside the worker that was ending"


def test_a_yield_that_was_overruled_is_forgotten(monkeypatch, queues):
    """A torrent hands its slot back by saying so on the task, and its worker
    queues it again on the way out. Paused in that same moment, the pause wins
    - but the note stayed on the task, and the NEXT run, however it ended, was
    read as a yield and queued once more."""
    runs = []

    class Yields:
        def __init__(self, task, segments=8):
            self.t = task

        def run(self):
            runs.append(1)
            if len(runs) == 1:
                self.t.request_pause()          # the user, as it gives way
                self.t._stall_yield = True
                self.t.status = T.QUEUED
            else:
                self.t.status = T.COMPLETED

    monkeypatch.setattr(queue_manager, "Downloader", Yields)
    q = queues()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.status == T.PAUSED), f"the pause did not win: {t.status}"

    q.resume_task(t)

    assert _wait(lambda: t.status == T.COMPLETED)
    time.sleep(0.3)
    assert len(runs) == 2, "a finished torrent was queued again as if it had given way"
    assert t.status == T.COMPLETED


# ---- Force Recheck, the menu action ----------------------------------------
class _Window(ActionsMixin):
    def __init__(self, queue):
        self.queue = queue
        self._toasts = types.SimpleNamespace(show=lambda *a, **k: None)

    def _save_state(self):
        pass

    def refresh(self):
        pass


def test_force_recheck_on_a_seeding_torrent_runs_one_engine_at_a_time(engine):
    q = engine()
    t = q.add_task(T.DownloadTask(MAGNET, "p"))
    assert _wait(lambda: t.seeding)

    _Window(q)._recheck_torrent(t)

    assert _wait(lambda: _runs[t.id] == 2), "the recheck never started"
    assert _peak[t.id] == 1, "the recheck ran beside the run that was seeding"
    assert _asked[t.id] == [False, True], "the recheck was not asked of the new run"


@pytest.mark.parametrize("status", [T.COMPLETED, T.PAUSED, T.ERROR])
def test_force_recheck_starts_a_torrent_that_is_not_running(engine, status):
    q = engine()
    t = T.DownloadTask(MAGNET, "p")
    q.add_task(t, start=False)
    t.status = status

    _Window(q)._recheck_torrent(t)

    assert _wait(lambda: _runs.get(t.id) == 1), f"a {status} torrent was not rechecked"
    assert _asked[t.id] == [True]


# ---- the whole thing, with the real engine ----------------------------------
class _Daemon:
    """aria2 as the engine sees it: downloads live under a gid until they are
    removed, and a gid that has been removed "is not found"."""

    def __init__(self, folder, total=1000):
        self.folder = folder
        self.total = total
        self.entries = {}
        self.adds = []
        self._n = 0
        self._lock = threading.Lock()

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        with self._lock:
            return self._call(method, *params)

    def _call(self, method, *params):
        if method in ("aria2.addUri", "aria2.addTorrent"):
            opts = dict(params[-1])
            self.adds.append(opts)
            self._n += 1
            gid = "gid%d" % self._n
            self.entries[gid] = {
                "status": "active",
                "checks": 3 if opts.get("check-integrity") == "true" else 0}
            return gid
        if method == "aria2.tellActive":
            return [{"gid": g, "infoHash": HASH, "status": e["status"]}
                    for g, e in self.entries.items()]
        if method in ("aria2.tellWaiting", "aria2.tellStopped"):
            return []
        gid = params[0] if params else ""
        if method in ("aria2.forceRemove", "aria2.remove"):
            self.entries.pop(gid, None)
            return gid
        if method == "aria2.removeDownloadResult":
            return "OK"
        if gid not in self.entries:
            raise aria2d.Aria2Error("GID %s is not found" % gid)
        e = self.entries[gid]
        if method == "aria2.pause":
            e["status"] = "paused"
            return gid
        if method == "aria2.unpause":
            e["status"] = "active"
            return gid
        if method == "aria2.getFiles":
            return []
        if method == "aria2.tellStatus":
            st = {"status": e["status"], "totalLength": str(self.total),
                  "dir": self.folder, "connections": "0", "numSeeders": "0",
                  "files": [{"path": os.path.join(self.folder, "Movie.mkv")}]}
            if e["checks"] > 0:                     # hashing what is on disk
                e["checks"] -= 1
                return dict(st, completedLength="0",
                            verifiedLength=str(self.total // 2))
            return dict(st, completedLength=str(self.total), seeder="true")
        return {}


def test_a_seeding_torrent_comes_back_completed_from_a_recheck(tmp_path, monkeypatch, queues):
    """What was reported, start to finish, with the real torrent engine."""
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "Movie.mkv").write_bytes(b"\0" * 1000)
    d = _Daemon(str(folder))
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(utils, "SEED_ENABLED", True, raising=False)
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c")
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.02)
    monkeypatch.setattr(aria2d, "DAEMON", d)
    q = queues()
    t = q.add_task(T.DownloadTask(MAGNET, str(folder / "download.bin")))
    assert _wait(lambda: t.status == T.COMPLETED and t.seeding), "never seeded"

    _Window(q)._recheck_torrent(t)

    assert _wait(lambda: len(d.adds) == 2, timeout=5), "the recheck never reached aria2"
    assert d.adds[1].get("check-integrity") == "true"
    assert _wait(lambda: t.status == T.COMPLETED and t.seeding, timeout=5), (
        f"left as {t.status} after a recheck that passed "
        f"(error {t.error!r}, timeline {[e['message'] for e in t.events]})")
    assert t.error == ""
    said = [e["message"] for e in t.events]
    assert "Verification finished" in said
    assert not any("engine restarted" in m for m in said), (
        "the recheck knocked a live run off its torrent: %s" % said)
    assert _wait(lambda: q.active == 0, timeout=3), (
        "the recheck kept a download slot after it was done")
