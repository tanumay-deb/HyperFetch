"""A finished torrent has to end up in its category folder.

It never did. aria2 reports a finished torrent as "active" (seeder=true) until
it has told every tracker it is leaving, and only then as "complete" — about a
second with a quick tracker, for as long as a slow one stalls, with the files
held open throughout. The engine read that window as seeding even with seeding
turned off: the task went COMPLETED still pointing at its placeholder path
(magnet_.bin, download.bin), and the GUI, which files a completed task exactly
once and skips one that is seeding, never came back to it.
"""
import os
import types

import aria2d
import task as T
import torrent
import utils
from gui2.app import DownloadAppV2 as A


class _Aria2:
    """aria2 as measured against the bundled binary: after the payload is whole
    it stays active with seeder=true for `goodbye_polls` polls (announcing
    "stopped" to trackers), then says complete. forceRemove skips the
    announcing and leaves a result that still reads complete."""

    def __init__(self, payload, size=1000, goodbye_polls=50):
        self.payload, self.size = payload, size
        self.progress = [size // 2]
        self.goodbye = goodbye_polls
        self.forced = False
        self.task = None
        self.snapshots = []          # (status, seeding, save_path) at each poll

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        if method in ("aria2.addUri", "aria2.addTorrent"):
            return "gid1"
        if method == "aria2.forceRemove":
            self.forced = True
            return "gid1"
        if method != "aria2.tellStatus":
            return {}
        t = self.task
        self.snapshots.append((t.status, bool(getattr(t, "seeding", False)), t.save_path))
        whole = {"completedLength": str(self.size), "totalLength": str(self.size),
                 "files": [{"path": self.payload}]}
        if self.progress:
            return {"status": "active", "completedLength": str(self.progress.pop(0)),
                    "totalLength": str(self.size), "seeder": "false",
                    "files": [{"path": self.payload}]}
        if self.forced or self.goodbye <= 0:
            return dict(whole, status="complete")
        self.goodbye -= 1
        return dict(whole, status="active", seeder="true")


def _run(tmp_path, monkeypatch, daemon):
    monkeypatch.setattr(torrent, "POLL", 0)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0)
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Movie", str(tmp_path / "download.bin"))
    daemon.task = t
    torrent.TorrentDownloader(t)._run_rpc()
    return t


def test_with_seeding_off_a_finished_torrent_completes_rather_than_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "SEED_ENABLED", False, raising=False)
    payload = tmp_path / "Movie.mkv"
    payload.write_bytes(b"x")
    daemon = _Aria2(str(payload))
    t = _run(tmp_path, monkeypatch, daemon)
    assert t.status == T.COMPLETED
    assert not t.seeding
    assert t.save_path == str(payload), "finished still pointing at the placeholder"
    # the GUI will not file a task while it reads as seeding
    assert not any(seeding for _, seeding, _ in daemon.snapshots), \
        "reported seeding with seeding turned off"
    assert daemon.forced, "sat out aria2's tracker goodbyes with the files held open"
    assert len(daemon.snapshots) <= 4, "took %d polls to finish" % len(daemon.snapshots)


def test_a_seeding_torrent_points_at_its_payload_while_it_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "SEED_ENABLED", True, raising=False)
    payload = tmp_path / "Movie.mkv"
    payload.write_bytes(b"x")
    daemon = _Aria2(str(payload), goodbye_polls=3)
    t = _run(tmp_path, monkeypatch, daemon)
    seeding = [p for _, s, p in daemon.snapshots if s]
    assert seeding, "never seeded"
    assert set(seeding) == {str(payload)}, \
        "kept the placeholder path while seeding: %r" % sorted(set(seeding))
    assert not daemon.forced, "cut short a torrent the user asked to seed"
    assert t.status == T.COMPLETED and not t.seeding
    assert t.save_path == str(payload)


# ------------------------------------------------------------------ the GUI side
def _app(tasks):
    """Just enough of DownloadAppV2 for _check_completions, quiet on completion."""
    app = types.SimpleNamespace(
        queue=types.SimpleNamespace(tasks=tasks),
        _completed_seen=None, _errored_seen=None, _unfiled=set(),
        _extras={"categorize": True, "when_complete": "Do nothing"},
        _save_state=lambda: None,
        _folder_category=staticmethod(A._folder_category),
        _is_update=lambda t: False,         # none of these is the app's own installer
    )
    app._maybe_categorize = lambda t: A._maybe_categorize(app, t)
    return app


def test_a_torrent_that_completed_while_seeding_is_filed_when_seeding_stops(tmp_path):
    folder = tmp_path / "Show.S01.1080p"
    folder.mkdir()
    (folder / "Show.S01E01.mkv").write_bytes(b"\0" * 5000)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(folder), filename="Show.S01.1080p")
    t.status = T.DOWNLOADING
    app = _app([t])
    A._check_completions(app)                 # first tick only takes stock

    t.status, t.seeding = T.COMPLETED, True
    A._check_completions(app)
    assert folder.exists(), "moved a folder aria2 still had open"

    t.seeding = False                         # seeding over, files released
    A._check_completions(app)
    filed = tmp_path / utils.category_for("x.mkv") / "Show.S01.1080p"
    assert filed.is_dir(), "never filed once the seeding stopped"
    assert t.save_path == str(filed)
