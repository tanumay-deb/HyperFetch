"""Force Recheck: verify a finished torrent, then get out of the way.

Reported as "I force-rechecked 5 files just to verify and now they're stuck".
The verification itself worked — a live log showed all five reporting
"verification finished (N of N present)" with no bad pieces. What followed is
the problem: a torrent that is already 100% goes straight into seeding, and the
poll loop then spins on `continue` forever, so the worker thread and its queue
slot are never released.
"""
import threading
import time

import pytest

import aria2d
import task as T
import torrent
import utils


class _RecheckDaemon:
    """Replays a recheck: verifying, then verified, then seeding forever."""

    def __init__(self, total=1000):
        self.total = total
        self.calls = []
        self.opts = None
        self.dropped = []
        self._n = 0

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        self.calls.append(method)
        if method in ("aria2.addUri", "aria2.addTorrent"):
            self.opts = params[-1] if params else None
            return "gid1"
        if method == "aria2.removeDownloadResult":
            self.dropped.append(params[0] if params else None)
            return "OK"
        if method == "aria2.tellStatus":
            self._n += 1
            base = {"totalLength": str(self.total), "completedLength": str(self.total),
                    "connections": "0", "files": [{"path": "x"}]}
            if self._n == 1:                       # mid-verify
                return dict(base, status="active", verifiedLength=str(self.total // 2),
                            verifyIntegrityPending="true")
            # verified, complete, now seeding: aria2 keeps it "active" forever
            return dict(base, status="active", seeder="true")
        return {}


def _drive(tmp_path, monkeypatch, daemon, task, seconds=1.0, seed=True):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    # _RecheckDaemon seeds forever, which aria2 only does when asked to seed.
    # With seeding off it announces "stopped" to its trackers and completes —
    # the case test_with_seeding_off_a_verified_torrent_finishes covers.
    monkeypatch.setattr(utils, "SEED_ENABLED", seed, raising=False)
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.01)
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    td = torrent.TorrentDownloader(task)
    th = threading.Thread(target=td._run_rpc, daemon=True)
    th.start()
    time.sleep(seconds)
    return th


def _task(tmp_path):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Movie",
                       str(tmp_path / "download.bin"))
    t.force_recheck = True
    return t


def test_recheck_asks_aria2_to_check_integrity(tmp_path, monkeypatch):
    """The flag has to reach aria2, or Force Recheck silently does nothing."""
    d = _RecheckDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.4)
    t.request_pause(); th.join(timeout=3)
    assert d.opts and d.opts.get("check-integrity") == "true", \
        f"check-integrity never reached the add call: {d.opts}"


def test_recheck_drops_the_old_registration_first(tmp_path, monkeypatch):
    """Re-attaching would hand back the OLD options, so the recheck would not
    happen — which is what "Force Recheck does nothing" looked like."""
    d = _RecheckDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.4)
    t.request_pause(); th.join(timeout=3)
    assert "aria2.tellActive" in d.calls, "never looked for an existing entry"


def test_the_verifying_flag_is_raised_then_cleared(tmp_path, monkeypatch):
    """With nothing shown, a recheck is indistinguishable from a hang."""
    d = _RecheckDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5)
    t.request_pause(); th.join(timeout=3)
    assert t.verifying is False, "left the task stuck showing 'verifying'"
    assert any(e.get("message") == "Verifying downloaded data" for e in t.events)


def test_a_verified_torrent_reports_completed(tmp_path, monkeypatch):
    d = _RecheckDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5)
    status_while_seeding, seeding = t.status, t.seeding
    t.request_pause(); th.join(timeout=3)
    assert status_while_seeding == T.COMPLETED, (
        f"a fully verified torrent showed {status_while_seeding}, so it reads as "
        "stuck rather than done")
    assert seeding is True


# ---- what a run says about itself ends with the run -------------------------
# "Seeding" and "rechecking" describe a run that is alive. Left standing after
# the run had ended, "seeding" told the NEXT run that the torrent had already
# been marked complete: it skipped doing so, and a recheck that passed stayed
# Paused, or Downloading at 100%, holding a download slot. Reported as "Force
# Recheck leaves it paused; Resume moves it to active and it gets stuck".

def test_a_run_that_has_ended_is_not_seeding(tmp_path, monkeypatch):
    d = _RecheckDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5)
    assert t.seeding, "the test needs it seeding first"
    t.request_pause(); th.join(timeout=3)
    assert not th.is_alive()
    assert t.seeding is False, "a paused torrent still says it is seeding"


def test_seeding_left_over_from_an_earlier_run_does_not_hold_this_one_back(tmp_path, monkeypatch):
    d = _RecheckDaemon()
    t = _task(tmp_path)
    t.seeding = True                # what a run that ended any other way left
    released = []
    t._release_slot = lambda: released.append(True)

    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5)
    status = t.status
    t.request_pause(); th.join(timeout=3)

    assert status == T.COMPLETED, (
        f"a recheck that passed left the torrent {status}, not Completed")
    assert released, "and it kept its download slot"


class _StuckChecking(_RecheckDaemon):
    """A recheck that is still going when the run is stopped."""

    def call(self, method, *params, **kw):
        if method == "aria2.tellStatus":
            self.calls.append(method)
            return {"status": "active", "totalLength": str(self.total),
                    "completedLength": "0", "connections": "0",
                    "verifiedLength": str(self.total // 2),
                    "files": [{"path": "x"}]}
        return super().call(method, *params, **kw)


def test_a_run_stopped_mid_check_stops_saying_rechecking(tmp_path, monkeypatch):
    d = _StuckChecking()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.4)
    assert t.verifying, "the test needs it rechecking first"
    t.request_pause(); th.join(timeout=3)
    assert not th.is_alive()
    assert t.verifying is False, "a paused torrent still says Rechecking"
    assert t.verified_pct == 0 and t.verified_bytes == 0


def test_seeding_hands_the_queue_slot_back(tmp_path, monkeypatch):
    """The reported "stuck": five rechecked torrents all entered seeding and
    stayed there. Five of the six had uploaded 0 MB with no peers, so the ratio
    target is unreachable — they seed forever, and used to hold a download slot
    the whole time.

    The worker thread deliberately keeps running (pause, remove and the live
    upload figure need it); only the slot is given back.
    """
    d = _RecheckDaemon()
    t = _task(tmp_path)
    released = []
    t._release_slot = lambda: released.append(True)

    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5)
    still_polling = th.is_alive()
    t.request_pause(); th.join(timeout=3)

    assert released, "seeding never released the download slot — the queue stays blocked"
    assert len(released) == 1, "released the same slot more than once"
    assert still_polling, ("the poll loop must stay alive while seeding, or pause "
                           "and the upload figure stop working")


class _GoodbyeDaemon(_RecheckDaemon):
    """A recheck with seeding off, as aria2 really does it: the verified torrent
    stays "active" while it tells its trackers it is leaving, then completes.
    forceRemove skips the telling."""

    forced = False

    def call(self, method, *params, **kw):
        if method == "aria2.forceRemove":
            self.calls.append(method)
            self.forced = True
            return "gid1"
        if method == "aria2.tellStatus" and self.forced:
            self.calls.append(method)
            return {"status": "complete", "totalLength": str(self.total),
                    "completedLength": str(self.total), "files": [{"path": "x"}]}
        return super().call(method, *params, **kw)


def test_with_seeding_off_a_verified_torrent_finishes(tmp_path, monkeypatch):
    """Rechecking a finished torrent with seeding off has to end it. Left in
    the goodbye window it read as seeding: parked at 100% with its worker still
    polling, and never filed into its category folder."""
    d = _GoodbyeDaemon()
    t = _task(tmp_path)
    th = _drive(tmp_path, monkeypatch, d, t, seconds=0.5, seed=False)
    finished = not th.is_alive()
    t.request_pause(); th.join(timeout=3)
    assert "aria2.forceRemove" in d.calls, "waited out the tracker goodbyes"
    assert finished, "the worker kept polling a torrent that was done"
    assert t.status == T.COMPLETED
    assert not t.seeding


# ---- while it checks, nothing else is true of the torrent --------------------
# Measured on aria2 1.37.0 by rechecking a torrent that had been seeding: for
# the WHOLE check it goes on reporting what its control file said before -
#
#     status=active  completedLength == totalLength  seeder=true  verifiedLength=<so far>
#
# - and the same again, minus verifiedLength, once the check is over. Taken at
# its word the task was marked Completed the moment its recheck began; with
# seeding off the download was removed as finished before a piece was read.

class _Checking(_RecheckDaemon):
    """Hash-checks until `checking` is cleared, reporting as aria2 does."""

    def __init__(self, total=1000, whole=True, verified=None):
        super().__init__(total)
        self.checking = True
        self.whole = whole              # was the torrent whole before the check?
        self.verified = total // 2 if verified is None else verified
        self.removed = False

    def call(self, method, *params, **kw):
        if method == "aria2.forceRemove":
            self.calls.append(method)
            self.removed = True
            return "gid1"
        if method != "aria2.tellStatus":
            return super().call(method, *params, **kw)
        self.calls.append(method)
        st = {"totalLength": str(self.total), "connections": "0",
              "files": [{"path": "x"}]}
        if self.removed:
            return dict(st, status="complete", completedLength=str(self.total))
        if self.checking:
            have = self.total if self.whole else 0
            return dict(st, status="active", completedLength=str(have),
                        seeder="true" if self.whole else "false",
                        verifiedLength=str(self.verified))
        return dict(st, status="active", completedLength=str(self.total), seeder="true")


def _start(tmp_path, monkeypatch, daemon, task, seed=True):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(utils, "SEED_ENABLED", seed, raising=False)
    monkeypatch.setattr(torrent, "POLL", 0.01)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0.01)
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    task.status = T.DOWNLOADING                 # what run() sets before the engine
    td = torrent.TorrentDownloader(task)
    th = threading.Thread(target=td._run_rpc, daemon=True)
    th.start()
    return th


def _until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_a_torrent_being_checked_is_not_completed_yet(tmp_path, monkeypatch):
    d = _Checking()
    t = _task(tmp_path)
    released = []
    t._release_slot = lambda: released.append(True)
    th = _start(tmp_path, monkeypatch, d, t)
    try:
        assert _until(lambda: t.verifying), "the check never showed"
        time.sleep(0.3)
        assert t.status == T.DOWNLOADING, (
            f"reads {t.status} while its recheck is still running")
        assert not t.seeding and not released

        d.checking = False                      # the check ends, nothing missing

        assert _until(lambda: t.status == T.COMPLETED and t.seeding), (
            f"a recheck that passed left it {t.status}")
        assert released == [True]
    finally:
        t.request_pause(); th.join(timeout=3)


def test_a_check_that_has_read_nothing_yet_is_still_a_check(tmp_path, monkeypatch):
    """aria2 says a download is being checked by listing verifiedLength at
    all; the first reading of it is 0."""
    d = _Checking(verified=0)
    t = _task(tmp_path)
    th = _start(tmp_path, monkeypatch, d, t)
    try:
        assert _until(lambda: len(d.calls) > 8)
        assert t.verifying, "a check at 0 bytes was not seen as a check"
        assert t.status == T.DOWNLOADING and not t.seeding
    finally:
        t.request_pause(); th.join(timeout=3)


def test_with_seeding_off_the_check_is_not_cut_short(tmp_path, monkeypatch):
    d = _Checking()
    t = _task(tmp_path)
    th = _start(tmp_path, monkeypatch, d, t, seed=False)
    try:
        assert _until(lambda: t.verifying)
        time.sleep(0.3)
        assert "aria2.forceRemove" not in d.calls, (
            "removed as finished while its recheck was still running")
        assert t.status == T.DOWNLOADING

        d.checking = False

        assert _until(lambda: t.status == T.COMPLETED), f"left {t.status}"
        th.join(timeout=3)
        assert not th.is_alive(), "the worker kept polling a torrent that was done"
    finally:
        t.request_pause(); th.join(timeout=3)


def test_a_long_check_is_not_a_dead_swarm(tmp_path, monkeypatch):
    """No peer is talked to until the check is over. Three minutes of that used
    to read as "no peers, no progress": the torrent gave its slot back and the
    check was thrown away, to start from nothing on the next try."""
    monkeypatch.setattr(torrent, "STALL_YIELD", 0.15)
    d = _Checking(whole=False)                  # nothing counted until it is read
    t = _task(tmp_path)
    th = _start(tmp_path, monkeypatch, d, t)
    try:
        assert _until(lambda: t.verifying)
        time.sleep(0.6)
        assert th.is_alive() and not t._stall_yield, (
            "gave its slot back as stalled in the middle of a check")
        assert "aria2.forceRemove" not in d.calls
        assert t.status == T.DOWNLOADING
    finally:
        t.request_pause(); th.join(timeout=3)
