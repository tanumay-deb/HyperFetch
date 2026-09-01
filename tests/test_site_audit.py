"""Who did what, and the retention clock that deletes without being asked.

The audit log is the only record of what an account did once you have handed
out credentials, and retention is the only thing here that deletes files on a
timer with nobody watching. Both get tested for what they refuse as much as
what they do.
"""
import json
import os
import time

import pytest

import site_audit
import site_limits
import task as T
import utils


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


# ---- writing ---------------------------------------------------------------
def test_an_action_is_recorded_as_one_line():
    site_audit.record("signin", "tanumay", addr="127.0.0.1")
    rows = site_audit.tail()
    assert len(rows) == 1
    assert rows[0]["action"] == "signin"
    assert rows[0]["user"] == "tanumay"
    assert rows[0]["t"] > 0


def test_it_appends_rather_than_rewriting(appdata):
    """A whole-document rewrite per row gets slower as it grows and loses
    everything to a crash mid-write instead of the last line."""
    for i in range(5):
        site_audit.record("add", "tanumay", {"name": "f%d.bin" % i})
    raw = open(site_audit.path(), encoding="utf-8").read().strip().split("\n")
    assert len(raw) == 5
    for line in raw:
        json.loads(line)                 # every line stands alone


def test_the_newest_entry_comes_first():
    site_audit.record("signin", "a")
    site_audit.record("signin", "b")
    assert [r["user"] for r in site_audit.tail()] == ["b", "a"]


def test_an_unknown_action_is_refused_rather_than_written():
    """A mystery row is worse than a missing one, and this way a typo shows up
    in the app log instead of quietly polluting the record."""
    assert site_audit.record("whatever", "tanumay") is False
    assert site_audit.tail() == []


def test_a_failure_to_write_does_not_break_the_caller(monkeypatch):
    """An audit failure must never fail a download."""
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr("builtins.open", boom)
    assert site_audit.record("download", "tanumay") is False


def test_entries_can_be_filtered_to_one_account():
    site_audit.record("add", "aaa")
    site_audit.record("add", "bbb")
    site_audit.record("add", "aaa")
    assert len(site_audit.tail(user="aaa")) == 2


def test_a_damaged_line_does_not_break_the_read(appdata):
    """A log you cannot open because one row is malformed is a log that has
    stopped doing its job."""
    site_audit.record("signin", "aaa")
    with open(site_audit.path(), "a", encoding="utf-8") as f:
        f.write("{not json at all\n")
    site_audit.record("signin", "bbb")
    assert [r["user"] for r in site_audit.tail()] == ["bbb", "aaa"]


def test_the_log_rotates_instead_of_growing_forever(monkeypatch):
    monkeypatch.setattr(site_audit, "MAX_BYTES", 400)
    for i in range(60):
        site_audit.record("add", "tanumay", {"name": "x" * 20, "n": i})
    assert os.path.isfile(site_audit.path())
    assert os.path.isfile(site_audit.path() + ".1"), "nothing was rotated"
    assert os.path.getsize(site_audit.path()) < 4000


def test_rotation_keeps_the_recent_past_rather_than_trimming(monkeypatch):
    monkeypatch.setattr(site_audit, "MAX_BYTES", 300)
    for i in range(40):
        site_audit.record("add", "tanumay", {"n": i})
    old = open(site_audit.path() + ".1", encoding="utf-8").read()
    assert old.strip(), "the rotated file is empty"


def test_a_summary_adds_up_per_account():
    site_audit.record("add", "aaa")
    site_audit.record("download", "aaa", {"size": 1000})
    site_audit.record("download", "aaa", {"size": 500})
    site_audit.record("add", "bbb")
    s = site_audit.summary()
    assert s["aaa"] == {"added": 1, "downloaded": 2, "bytes": 1500}
    assert s["bbb"]["added"] == 1


# ---- retention -------------------------------------------------------------
class _Queue:
    def __init__(self, tasks):
        self.tasks = list(tasks)

    def remove_task(self, t):
        self.tasks.remove(t)


def _finished(tmp_path, owner, name, age_days):
    folder = utils.user_download_dir(str(tmp_path), owner) if owner else str(tmp_path)
    p = os.path.join(folder, name)
    with open(p, "wb") as f:
        f.write(b"x" * 1000)
    t = T.DownloadTask("https://e.test/" + name, p, filename=name)
    t.owner = owner
    t.status = T.COMPLETED
    t.completed_at = time.time() - age_days * 86400
    return t


def test_an_old_download_is_deleted_and_recorded(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    old = _finished(dl, "tanumay", "old.mkv", 40)
    q = _Queue([old])

    removed = site_limits.sweep(q, str(dl))
    assert len(removed) == 1
    assert not os.path.exists(old.save_path), "the file survived retention"
    assert q.tasks == [], "the record survived its file"
    assert site_audit.tail()[0]["action"] == "expire"


def test_a_recent_download_is_left_alone(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    fresh = _finished(dl, "tanumay", "new.mkv", 3)
    q = _Queue([fresh])
    assert site_limits.sweep(q, str(dl)) == []
    assert os.path.exists(fresh.save_path)
    assert q.tasks == [fresh]


def test_retention_never_touches_admin_downloads(tmp_path):
    """Deleting from the desktop app a month on would be a genuinely bad
    surprise. Retention bounds what the site accumulates, nothing else."""
    dl = tmp_path / "downloads"
    dl.mkdir()
    mine = _finished(dl, "", "mine.mkv", 900)
    q = _Queue([mine])
    assert site_limits.sweep(q, str(dl)) == []
    assert os.path.exists(mine.save_path)
    assert q.tasks == [mine]


def test_an_unfinished_download_is_never_swept(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    t = _finished(dl, "tanumay", "busy.mkv", 900)
    t.status = T.DOWNLOADING
    q = _Queue([t])
    assert site_limits.sweep(q, str(dl)) == []
    assert os.path.exists(t.save_path)


def test_the_sweep_cannot_delete_outside_the_owner_s_folder(tmp_path):
    """This is the one thing that deletes on a timer with nobody watching, so
    it gets the same containment check as a request."""
    dl = tmp_path / "downloads"
    dl.mkdir()
    outside = dl / "not-yours.bin"
    outside.write_bytes(b"important")

    t = _finished(dl, "tanumay", "old.mkv", 40)
    t.save_path = str(outside)           # as if the record had been tampered with
    q = _Queue([t])
    site_limits.sweep(q, str(dl))
    assert outside.exists(), "retention deleted a file outside the owner's folder"


def test_one_bad_task_does_not_stop_the_rest(tmp_path, monkeypatch):
    dl = tmp_path / "downloads"
    dl.mkdir()
    a = _finished(dl, "tanumay", "a.mkv", 40)
    b = _finished(dl, "tanumay", "b.mkv", 40)
    q = _Queue([a, b])

    calls = {"n": 0}
    original = q.remove_task

    def flaky(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("nope")
        original(t)
    q.remove_task = flaky

    removed = site_limits.sweep(q, str(dl))
    assert len(removed) == 1, "a single failure aborted the whole sweep"
