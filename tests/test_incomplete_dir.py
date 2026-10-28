"""Unfinished downloads in one folder, finished ones moved to theirs.

Asked 2026-10-28: Settings -> General -> "Keep unfinished downloads in".
Off ("") by default. With a folder picked, a download's unfinished data is
written there, and the finished download goes to its own folder.

This file: the setting, and the ordinary and HLS downloads, whose temp file
(<id>.hfdownload) goes in the picked folder rather than the system temp
folder - still in this installation's own HyperFetch-<id> folder inside it,
so two installations pointed at one folder do not sweep each other's files.
"""
import inspect
import os
import tempfile
import time

import task as T
import utils
from downloader import Downloader


def test_off_unless_a_folder_is_picked():
    from gui2.dialogs import settings_pages
    assert utils.INCOMPLETE_DIR == ""
    assert 'ex.get("incomplete_dir", "")' in inspect.getsource(settings_pages)


def test_temp_files_go_in_the_picked_folder(tmp_path, monkeypatch):
    picked = tmp_path / "unfinished"
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(picked))
    path = utils.temp_download_path("abc")
    assert os.path.dirname(os.path.dirname(path)) == str(picked)
    assert os.path.basename(os.path.dirname(path)).startswith("HyperFetch-")


def test_a_paused_download_goes_on_where_its_bytes_are(tmp_path, monkeypatch):
    """Picked while a download was paused: its partial is in the temp folder,
    and the resume must find it there, not start again in the new folder."""
    before = utils.temp_download_path("abc")
    with open(before, "wb") as f:
        f.write(b"x" * 10)
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unfinished"))
    assert utils.temp_download_path("abc") == before


def test_the_sweep_covers_both_places(tmp_path, monkeypatch):
    old = utils.temp_download_path("gone1")
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unfinished"))
    new = utils.temp_download_path("gone2")
    for p in (old, new):
        with open(p, "wb") as f:
            f.write(b"x")
    later = time.time() + utils.ORPHAN_AGE + 60
    removed = utils.sweep_orphan_temps(set(), now=later)
    assert sorted(removed) == sorted([old, new])


def test_a_download_is_written_there_and_finishes_in_its_folder(tmp_path, monkeypatch,
                                                                 media_server, make_payload):
    picked = tmp_path / "unfinished"
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(picked))
    data = media_server.put("f.bin", make_payload(3 * 1024 * 1024))
    dest = tmp_path / "Downloads" / "f.bin"
    dest.parent.mkdir()
    t = T.DownloadTask(media_server.url("f.bin"), str(dest))
    assert str(picked) in utils.temp_download_path(t.id)
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert dest.read_bytes() == data
    left = [f for _, _, fs in os.walk(picked) for f in fs]
    assert left == [], "left in the unfinished folder: %s" % left


# ---- video pages (yt-dlp) ----------------------------------------------------------
from test_ytdlp_parallel import SIZE, TITLE, real_ytdlp, video_page  # noqa: E402,F401


def test_a_video_is_written_there_and_filed_in_its_folder(real_ytdlp, video_page, media_server,
                                                          make_payload, tmp_path, monkeypatch):
    unfinished = tmp_path / "unfinished"
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(unfinished))
    data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    page = video_page("/watch/7", media_server.url("media/clip.mp4"))
    base = str(tmp_path / "dl")
    os.makedirs(base)
    folder, sort_base = utils.place_download(base, page, "7.bin")
    t = T.DownloadTask(page, utils.unique_path(folder, "7.bin"), filename="7.bin")
    t.sort_base, t.use_ytdlp = sort_base, True
    moves = []
    real_move = utils.move_with_progress

    def move(src, dst, progress=lambda n: None, **kw):    # where it was, where it goes
        moves.append((src, dst))
        return real_move(src, dst, progress, **kw)

    monkeypatch.setattr(utils, "move_with_progress", move)
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    (src, dst), = moves
    assert os.path.dirname(src) == str(unfinished), src
    # its name is yt-dlp's (the generic extractor says "A video (1)")
    assert dst == t.save_path == os.path.join(base, "Video", os.path.basename(src))
    assert os.path.basename(dst).startswith(TITLE) and dst.endswith(".mp4"), dst
    assert open(t.save_path, "rb").read() == data
    assert t.unfinished_in == ""
    left = [f for _, _, fs in os.walk(unfinished) for f in fs]
    assert left == [], "left in the unfinished folder: %s" % left


def test_a_video_begun_in_its_own_folder_goes_on_there(tmp_path, monkeypatch):
    import yt_dl
    home = tmp_path / "dl" / "Video"
    home.mkdir(parents=True)
    (home / "Clip.mp4.part").write_bytes(b"x" * 10)        # began before the folder was picked
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unfinished"))
    t = T.DownloadTask("https://www.youtube.com/watch?v=x", str(home / "Clip.mp4"), filename="Clip.mp4")
    t.yt_named = True
    y = yt_dl.YtDlpDownloader(t)
    assert y._work_dir() == str(home) and t.unfinished_in == ""
