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
