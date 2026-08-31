"""What a site account may consume.

Three limits that exist for different reasons, so the tests keep them apart:
free space protects the machine, quota protects the other accounts, retention
bounds what accumulates when nobody is watching.
"""
import os
import time

import pytest

import site_limits as L
import task as T
import utils


GB = 1024 ** 3


@pytest.fixture
def dl(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return str(d)


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def _task(owner="", status=T.COMPLETED, completed_at=None, added=None):
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    t.owner = owner
    t.status = status
    if completed_at is not None:
        t.completed_at = completed_at
    if added is not None:
        t.added = added
    return t


# ---- usage -----------------------------------------------------------------
def test_an_account_with_nothing_uses_nothing(dl):
    assert L.usage_bytes(dl, "tanumay") == 0


def test_usage_counts_everything_under_the_folder(dl):
    _write(os.path.join(dl, "tanumay", "a.bin"), 1000)
    _write(os.path.join(dl, "tanumay", "sub", "b.bin"), 2500)
    assert L.usage_bytes(dl, "tanumay") == 3500


def test_one_account_does_not_count_against_another(dl):
    _write(os.path.join(dl, "aaa", "a.bin"), 4000)
    _write(os.path.join(dl, "bbb", "b.bin"), 1000)
    assert L.usage_bytes(dl, "aaa") == 4000
    assert L.usage_bytes(dl, "bbb") == 1000


def test_admin_is_not_metered(dl):
    """Admin's downloads live in the category folders and are not the site's
    business to measure."""
    _write(os.path.join(dl, "Video", "film.mkv"), 9999)
    assert L.usage_bytes(dl, "") == 0


def test_usage_is_measured_from_disk_not_from_task_records(dl):
    """A task list can disagree with the filesystem after a crash or a manual
    delete. The files are what actually fill the disk."""
    _write(os.path.join(dl, "tanumay", "real.bin"), 777)
    assert L.usage_bytes(dl, "tanumay") == 777


def test_an_unsafe_username_measures_zero_rather_than_escaping(dl):
    assert L.usage_bytes(dl, "../..") == 0


# ---- free space ------------------------------------------------------------
def test_free_space_is_read_from_the_volume(dl):
    assert L.free_bytes(dl) > 0


def test_free_space_on_a_missing_path_is_zero_not_a_crash():
    assert L.free_bytes(os.path.join("Z:\\", "nope", "nope")) == 0


def test_a_nearly_full_disk_stops_everyone(dl, monkeypatch):
    """Including admin. A full disk breaks the desktop app just as surely as it
    breaks the site, so this one is not about fairness."""
    monkeypatch.setattr(L, "free_bytes", lambda p: 1 * GB)
    for owner in ("", "tanumay"):
        why = L.refusal(dl, owner)
        assert "disk space" in why, owner


def test_the_disk_message_comes_before_the_quota_message(dl, monkeypatch):
    """Otherwise someone goes off deleting their own files when the real
    problem is the machine."""
    monkeypatch.setattr(L, "free_bytes", lambda p: 1 * GB)
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 99 * GB)
    assert "disk space" in L.refusal(dl, "tanumay", quota=1 * GB)


# ---- quota -----------------------------------------------------------------
def test_an_account_under_quota_may_download(dl):
    _write(os.path.join(dl, "tanumay", "a.bin"), 1000)
    assert L.refusal(dl, "tanumay", quota=2 * GB) == ""


def test_an_account_at_its_quota_is_refused(dl, monkeypatch):
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 2 * GB)
    why = L.refusal(dl, "tanumay", quota=2 * GB)
    assert why and "space" in why
    assert L.over_quota(dl, "tanumay", quota=2 * GB) is True


def test_a_download_that_would_not_fit_is_refused_up_front(dl, monkeypatch):
    """Better to say so before spending an hour fetching it."""
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 1 * GB)
    why = L.refusal(dl, "tanumay", quota=2 * GB, want_bytes=int(1.5 * GB))
    assert why and "left of your" in why


def test_a_download_that_just_fits_is_allowed(dl, monkeypatch):
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 1 * GB)
    assert L.refusal(dl, "tanumay", quota=2 * GB, want_bytes=1 * GB) == ""


def test_admin_has_no_quota(dl, monkeypatch):
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 500 * GB)
    assert L.refusal(dl, "", quota=1) == ""
    assert L.over_quota(dl, "", quota=1) is False


def test_the_refusal_is_a_sentence_someone_can_act_on(dl, monkeypatch):
    """A limit that silently does nothing is the worst kind of limit."""
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 2 * GB)
    why = L.refusal(dl, "tanumay", quota=2 * GB)
    assert why.endswith("."), why
    assert "2.0 GB" in why, why


def test_quota_never_kills_a_running_download(dl, monkeypatch):
    """over_quota gates new work only. Cancelling a transfer partway wastes
    everything already fetched, and the overshoot is bounded by one file."""
    monkeypatch.setattr(L, "usage_bytes", lambda b, u: 3 * GB)
    running = _task("tanumay", status=T.DOWNLOADING)
    assert L.over_quota(dl, "tanumay", quota=2 * GB) is True
    assert running.status == T.DOWNLOADING, "the limit touched a live download"


# ---- concurrency -----------------------------------------------------------
def test_active_downloads_are_counted_per_account():
    tasks = [_task("aaa", T.DOWNLOADING), _task("aaa", T.QUEUED),
             _task("bbb", T.DOWNLOADING), _task("aaa", T.COMPLETED),
             _task("", T.DOWNLOADING)]
    assert L.active_count(tasks, "aaa") == 2
    assert L.active_count(tasks, "bbb") == 1
    assert L.active_count(tasks, "") == 1


def test_finished_and_failed_downloads_do_not_hold_a_slot():
    tasks = [_task("aaa", T.COMPLETED), _task("aaa", T.ERROR),
             _task("aaa", T.PAUSED)]
    assert L.active_count(tasks, "aaa") == 0


# ---- retention -------------------------------------------------------------
def test_a_recent_download_is_not_expired():
    now = 1_000_000.0
    t = _task("tanumay", completed_at=now - 5 * 86400)
    assert L.expired([t], now=now) == []


def test_a_download_past_the_window_is_expired():
    now = 1_000_000.0
    t = _task("tanumay", completed_at=now - 31 * 86400)
    assert L.expired([t], now=now) == [t]


def test_admin_downloads_never_expire():
    """Deleting from the desktop app a month on would be a genuinely bad
    surprise. Retention bounds what the site accumulates, nothing else."""
    now = 1_000_000.0
    t = _task("", completed_at=now - 999 * 86400)
    assert L.expired([t], now=now) == []


def test_an_unfinished_download_never_expires():
    now = 1_000_000.0
    t = _task("tanumay", status=T.DOWNLOADING, added=now - 99 * 86400)
    assert L.expired([t], now=now) == []


def test_a_download_with_no_timestamp_is_left_alone():
    """Restored from state written before completed_at existed. Better to keep
    a file forever than delete one on a guess."""
    now = 1_000_000.0
    t = _task("tanumay", completed_at=0)
    t.added = 0
    assert L.expired([t], now=now) == []


def test_the_countdown_is_shown_rather_than_files_vanishing():
    now = 1_000_000.0
    t = _task("tanumay", completed_at=now - 26 * 86400)
    assert L.days_left(t, now=now) == 4


def test_the_countdown_stops_at_zero():
    now = 1_000_000.0
    t = _task("tanumay", completed_at=now - 40 * 86400)
    assert L.days_left(t, now=now) == 0


def test_admin_downloads_have_no_countdown():
    assert L.days_left(_task("", completed_at=1), now=2) is None


# ---- the completion stamp retention depends on ------------------------------
def test_completion_is_stamped_once_and_survives_a_restart():
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    assert t.completed_at == 0.0
    t.status = T.DOWNLOADING
    assert t.completed_at == 0.0, "stamped before it finished"
    t.status = T.COMPLETED
    first = t.completed_at
    assert first > 0

    t.status = T.PAUSED
    t.status = T.COMPLETED
    assert t.completed_at == first, "the clock restarted on a re-complete"
    assert T.DownloadTask.from_dict(t.to_dict()).completed_at == first


def test_state_written_before_the_stamp_existed_still_loads():
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    d = t.to_dict()
    del d["completed_at"]
    assert T.DownloadTask.from_dict(d).completed_at == 0.0


# ---- the queue -------------------------------------------------------------
def test_site_downloads_have_their_own_queue():
    """So site traffic can never take every slot from the desktop app."""
    assert L.WEB_QUEUE == "Web"
    assert L.WEB_QUEUE != "Main"
    assert L.WEB_QUEUE_CONCURRENT >= 1
