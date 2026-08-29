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


def _drive(tmp_path, monkeypatch, daemon, task, seconds=1.0):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
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
    status_while_seeding = t.status
    t.request_pause(); th.join(timeout=3)
    assert status_while_seeding == T.COMPLETED, (
        f"a fully verified torrent showed {status_while_seeding}, so it reads as "
        "stuck rather than done")
    assert t.seeding is True


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
