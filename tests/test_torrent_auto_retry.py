"""A torrent the swarm or the engine let down is tried again by itself.

From a real log (2026-08-25 .. 10-16): of 178 downloads that ended paused,
108 were torrents waiting for somebody to press Resume - 45 "No data received
for 30 minutes - the swarm has no active seeders right now", 25 "No peers
found yet", 38 "Torrent engine restarted - Resume to continue" (30-31 Aug and
5 Oct, when the daemon was replaced and every torrent it held was dropped).
None of them needed the user: their bytes and .aria2 control files were on
disk, and a later run picks up from those.

Now:
- aria2 giving up on a quiet swarm without a word (its --bt-stop-timeout) is
  a yield like any other: the slot goes back, and the torrent is queued again
  after the stall backoff, longer each time. A message from aria2, or a port
  that will not open, still waits for the user: retrying does not fix those.
- The engine losing a torrent - replaced, stopped answering, or not starting
  - queues it again after ENGINE_RETRY (5 s, 30 s, 2 min), then waits for
  Resume with the reason on its card. A poll that works starts the count again.
"""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import aria2d
import task as T
import torrent
import utils
from test_aria2d import _FakeDaemon, _FlakyDaemon, _drive, _drive_with, _task


def _payload(tmp_path):
    p = str(tmp_path / "Movie.mkv")
    open(p, "w").close()
    return p


# ---- the swarm --------------------------------------------------------------------
def test_a_swarm_quiet_for_half_an_hour_is_tried_again_by_itself(tmp_path, monkeypatch):
    p = _payload(tmp_path)
    states = [{"status": "active", "completedLength": "50", "totalLength": "100",
               "files": [{"path": p}]},
              {"status": "error", "completedLength": "50", "totalLength": "100",
               "errorMessage": "", "errorCode": "", "files": [{"path": p}]}]
    before = time.time()
    t, fake = _drive(tmp_path, monkeypatch, states)

    assert t.status == T.QUEUED, t.error
    assert t._stall_yield and t.yield_reason == "no seeders" and t.error == ""
    assert before + torrent.STALL_BACKOFF[0] - 1 <= t.retry_after <= time.time() + torrent.STALL_BACKOFF[0]
    assert any("No data received" in e["message"] for e in t.events), "the log lost why"
    assert any(c[0] == "aria2.forceRemove" for c in fake.calls), "left in the daemon"


def test_a_magnet_that_found_no_peers_is_tried_again_by_itself(tmp_path, monkeypatch):
    states = [{"status": "error", "completedLength": "0", "totalLength": "0",
               "errorMessage": "", "files": [{"path": "[METADATA]x"}]}]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.QUEUED and t.yield_reason == "no peers"
    assert any("No peers found yet" in e["message"] for e in t.events)


def test_the_wait_grows_with_each_try(tmp_path, monkeypatch):
    states = [{"status": "error", "completedLength": "0", "totalLength": "0",
               "errorMessage": "", "files": [{"path": "[METADATA]x"}]}]
    t = _task(tmp_path)
    t.stall_count = 1                                 # it has been here before
    t, _ = _drive(tmp_path, monkeypatch, states, task=t)
    assert t.retry_after - time.time() > torrent.STALL_BACKOFF[0]
    assert t.stall_count == 2


@pytest.mark.parametrize("says", ["Errors occurred while binding port",
                                  "Invalid magnet URI"])
def test_what_retrying_cannot_fix_still_waits_for_the_user(tmp_path, monkeypatch, says):
    states = [{"status": "error", "completedLength": "0", "totalLength": "0",
               "errorMessage": says, "files": [{"path": "[METADATA]x"}]}]
    t, _ = _drive(tmp_path, monkeypatch, states)
    assert t.status == T.PAUSED and not getattr(t, "_stall_yield", False)


# ---- the engine ----------------------------------------------------------------------
def test_an_engine_restart_queues_the_torrent_again_at_once(tmp_path, monkeypatch):
    daemon = _FlakyDaemon([], misses=1, error="GID abc is not found")
    t = _task(tmp_path)
    t.stall_count = 2
    t = _drive_with(tmp_path, monkeypatch, daemon, task=t)

    assert t.status == T.QUEUED and t._stall_yield and t.yield_reason == "engine"
    assert t.retry_after - time.time() <= torrent.ENGINE_RETRY[0]
    assert t.engine_retries == 1
    assert t.stall_count == 2, "the swarm was blamed for the engine"
    assert not getattr(t, "yielded_at", 0), "a notification for every engine hiccup"


def test_a_daemon_that_stopped_answering_queues_it_again_too(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent, "RPC_RETRY_GRACE", 0.0)
    daemon = _FlakyDaemon([], misses=torrent.RPC_RETRIES + 5)
    t = _drive_with(tmp_path, monkeypatch, daemon)
    assert t.status == T.QUEUED and t.yield_reason == "engine"


def test_an_engine_that_will_not_start_is_tried_again_by_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c.exe")

    class _Dead:
        def ensure(self):
            raise aria2d.Aria2Error("aria2 daemon busy: refused")

    monkeypatch.setattr(aria2d, "DAEMON", _Dead())
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(tmp_path / "download.bin"))
    torrent.TorrentDownloader(t).run()
    assert t.status == T.QUEUED and t.yield_reason == "engine"


@pytest.mark.parametrize("how", ["lost", "silent", "dead"])
def test_after_the_retries_are_spent_it_waits_for_resume(tmp_path, monkeypatch, how):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "RPC_RETRY_GRACE", 0.0)
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c.exe")
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(tmp_path / "download.bin"))
    t.engine_retries = len(torrent.ENGINE_RETRY)
    if how == "dead":
        class _Dead:
            def ensure(self):
                raise aria2d.Aria2Error("aria2 daemon busy: refused")
        monkeypatch.setattr(aria2d, "DAEMON", _Dead())
        torrent.TorrentDownloader(t).run()
    else:
        daemon = (_FlakyDaemon([], misses=1, error="GID abc is not found") if how == "lost"
                  else _FlakyDaemon([], misses=torrent.RPC_RETRIES + 5))
        t = _drive_with(tmp_path, monkeypatch, daemon, task=t)
    assert t.status == T.PAUSED and "Resume" in t.error
    assert t.engine_retries == 0, "the user's Resume would not get its own tries"


def test_an_engine_that_answers_again_clears_the_count(tmp_path, monkeypatch):
    p = _payload(tmp_path)
    t = _task(tmp_path)
    t.engine_retries = 2
    t, _ = _drive(tmp_path, monkeypatch, [{"status": "complete", "completedLength": "10",
                                           "totalLength": "10", "files": [{"path": p}]}],
                  task=t)
    assert t.status == T.COMPLETED and t.engine_retries == 0


# ---- the queue runs it again, nobody presses anything --------------------------------
def test_the_queue_starts_it_again_and_it_finishes(tmp_path, monkeypatch):
    from queue_manager import QueueManager
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torrent, "ENGINE_RETRY", (0.2, 0.2, 0.2))
    runs = []

    def run(self):
        runs.append(1)
        self.t.status = T.DOWNLOADING
        if len(runs) == 1:                       # the daemon was replaced under it
            self._engine_retry("Torrent engine restarted")
            return
        self.t.status = T.COMPLETED

    monkeypatch.setattr(torrent.TorrentDownloader, "run", run)
    q = QueueManager()
    try:
        t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(tmp_path / "download.bin"))
        q.add_task(t)
        end = time.monotonic() + 10
        while t.status != T.COMPLETED and time.monotonic() < end:
            time.sleep(0.05)
    finally:
        q.shutdown()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert len(runs) == 2


def test_the_card_says_the_engine_is_coming_back():
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", "C:/dl/x", filename="Movie")
    t.status = T.QUEUED
    t.total_size = 100
    t.yield_reason, t.retry_after = "engine", time.time() + 25
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert "Torrent engine restarted — continuing in" in card.sub.toolTip()
