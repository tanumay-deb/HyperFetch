"""Resolve magnet names/sizes while a torrent is only queued or paused.

Without this a queue of magnets is a list of identical placeholders — you
cannot tell one row from another until it starts.

Measured on a real library before building it: engine-start to metadata
resolved was 2s at best, 134s median, one case 1h47m. aria2 never abandons a
metadata fetch on its own, so this needs BOTH a concurrency cap and a deadline;
without the deadline one dead magnet holds a slot forever whatever the cap is.
"""
import os
import time

import pytest

import task as T
import torrent
import utils


class _Daemon:
    """Records adds; hands back metadata only for the infohashes told to."""

    def __init__(self, resolves=()):
        self.resolves = set(resolves)
        self.added = []
        self.removed = []

    def ensure(self):
        return True

    def call(self, method, *a, **kw):
        if method == "aria2.addUri":
            self.added.append({"uris": a[0], "opts": a[1] if len(a) > 1 else {}})
            return "gid%d" % len(self.added)
        if method == "aria2.tellStatus":
            return {"infoHash": "a" * 40}
        if method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
            self.removed.append(a[0] if a else None)
            return "OK"
        return {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(utils, "TORRENT_RPC", True, raising=False)
    return tmp_path


def _magnet(n=1, status=T.QUEUED):
    t = T.DownloadTask(f"magnet:?xt=urn:btih:{'a' * 40}&dn=Show{n}", f"C:/dl/x{n}")
    t.status = status
    t.total_size = 0
    t.id = f"task{n}"
    return t


def _prefetcher(tasks, daemon, monkeypatch):
    import aria2d
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    return torrent.MetadataPrefetcher(lambda: tasks)


def test_it_fetches_metadata_only(env, monkeypatch):
    """Downloading the payload is exactly what this must not do."""
    d = _Daemon()
    tasks = [_magnet()]
    _prefetcher(tasks, d, monkeypatch)._tick()
    assert d.added, "never asked the daemon for anything"
    opts = d.added[0]["opts"]
    assert opts.get("bt-metadata-only") == "true", opts
    assert opts.get("bt-save-metadata") == "true", opts
    assert opts.get("dir") == torrent.metadata_dir(), \
        "metadata must not land in the user's download folder"


def test_only_queued_and_paused_are_touched(env, monkeypatch):
    """A running torrent resolves its own metadata; a finished one has it."""
    d = _Daemon()
    tasks = [_magnet(1, T.DOWNLOADING), _magnet(2, T.COMPLETED),
             _magnet(3, T.QUEUED), _magnet(4, T.PAUSED)]
    _prefetcher(tasks, d, monkeypatch)._tick()
    assert len(d.added) == 2, f"picked up {len(d.added)} tasks, expected the queued + paused"


def test_a_torrent_that_already_knows_its_size_is_skipped(env, monkeypatch):
    d = _Daemon()
    t = _magnet()
    t.total_size = 1234
    _prefetcher([t], d, monkeypatch)._tick()
    assert d.added == []


def test_the_concurrency_cap_holds(env, monkeypatch):
    d = _Daemon()
    tasks = [_magnet(i) for i in range(12)]
    _prefetcher(tasks, d, monkeypatch)._tick()
    assert len(d.added) == torrent.META_MAX, (
        f"{len(d.added)} concurrent metadata fetches — aria2 serves RPC from one "
        "thread and this is what starved it before")


def test_it_gives_up_and_says_so(env, monkeypatch):
    """A dead magnet must stop looking busy and free the slot."""
    d = _Daemon()
    t = _magnet()
    p = _prefetcher([t], d, monkeypatch)
    p._tick()
    assert t.meta_fetching is True
    # pretend the deadline passed
    gid, _ = p._active[t.id]
    p._active[t.id] = (gid, time.time() - torrent.META_TIMEOUT - 1)
    p._reap()
    assert t.meta_failed is True, "no failure state, so the card looks busy forever"
    assert t.meta_fetching is False
    assert t.id not in p._active, "held the slot after giving up"
    assert d.removed, "left the dead entry in the daemon"
    assert t.meta_retry_after > time.time(), "no backoff, so it retries immediately"


def test_a_failed_torrent_is_not_retried_immediately(env, monkeypatch):
    d = _Daemon()
    t = _magnet()
    t.meta_failed = True
    t.meta_retry_after = time.time() + torrent.META_RETRY
    _prefetcher([t], d, monkeypatch)._tick()
    assert d.added == [], "retried a torrent that just failed"


def test_saved_metadata_is_used_without_touching_the_swarm(env, monkeypatch):
    """We already keep .torrent files by infohash — no reason to refetch."""
    ih = "a" * 40
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                       "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")
    if not os.path.isfile(src):
        pytest.skip("sample torrent absent")
    import shutil
    os.makedirs(torrent.metadata_dir(), exist_ok=True)
    shutil.copy2(src, os.path.join(torrent.metadata_dir(), ih + ".torrent"))

    d = _Daemon()
    t = _magnet()
    _prefetcher([t], d, monkeypatch)._tick()
    assert d.added == [], "went to the swarm for metadata already on disk"
    assert t.total_size > 0, "did not apply the saved metadata"


def test_nothing_happens_without_the_shared_daemon(env, monkeypatch):
    monkeypatch.setattr(utils, "TORRENT_RPC", False, raising=False)
    d = _Daemon()
    _prefetcher([_magnet()], d, monkeypatch)._tick()
    assert d.added == []
