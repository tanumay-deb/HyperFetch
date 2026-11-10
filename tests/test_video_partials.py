"""A video given up takes yt-dlp's partial files along (2026-11-04, list #1).

Until a video is done, yt-dlp keeps it as Title.mp4.part, Title.mp4.ytdl,
fragments and the parts of a merge. Cancel, Remove and "Delete from disk"
took only the app's own temp file, so a half-done video stayed in the folder.
"""
import os

import pytest

import task as T
from queue_manager import QueueManager

LEFT = ["Clip.mp4.part", "Clip.mp4.ytdl", "Clip.f137.mp4.part", "Clip.f140.m4a",
        "Clip.mp4.part-Frag3.part", "Clip.temp.mp4"]
MINE = ["Clip.fr.srt", "Clip.txt", "Other.mp4.part"]


def _video(folder, status=T.PAUSED, named=True):
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", str(folder / "Clip.mp4"))
    t.use_ytdlp, t.yt_named, t.status = True, named, status
    for n in LEFT + MINE:
        (folder / n).write_bytes(b"x")
    return t


def _there(folder):
    return sorted(os.listdir(folder))


@pytest.fixture
def dl(tmp_path):
    d = tmp_path / "dl"             # tmp_path holds the test's app-data too
    d.mkdir()
    return d


def test_a_waiting_video_cancelled_leaves_none_of_its_partials(dl):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    t = _video(dl)
    q.tasks.append(t)
    q.cancel_task(t)
    assert t.status == T.CANCELLED
    assert _there(dl) == sorted(MINE)


def test_a_failed_video_removed_from_the_list_leaves_none(dl):
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    t = _video(dl, status=T.ERROR)
    q.tasks.append(t)
    q.remove_task(t)
    assert t not in q.tasks
    assert _there(dl) == sorted(MINE)


def test_it_looks_where_the_video_is_written(tmp_path):
    work, home = tmp_path / "unfinished", tmp_path / "home"
    work.mkdir(), home.mkdir()
    t = _video(work)
    t.save_path = str(home / "Clip.mp4")
    t.unfinished_in = str(work)
    QueueManager().cancel_task(t)
    assert _there(work) == sorted(MINE)


def test_nothing_goes_for_a_name_yt_dlp_never_gave_nor_for_a_finished_one(dl):
    q = QueueManager()
    for status, named in ((T.PAUSED, False), (T.COMPLETED, True)):
        t = _video(dl, status=status, named=named)
        q.remove_task(t)
        assert _there(dl) == sorted(LEFT + MINE), status


def test_restart_of_a_half_done_video_starts_over(dl):
    """Restart dropped the app's own temp file only: yt-dlp found its .part
    again and went on from it, so nothing was restarted (2026-11-06)."""
    t = _video(dl)
    t.reset_progress()
    assert _there(dl) == sorted(MINE)


def test_restart_of_a_finished_video_takes_nothing(dl):
    t = _video(dl, status=T.COMPLETED)
    t.reset_progress()
    assert _there(dl) == sorted(LEFT + MINE)
