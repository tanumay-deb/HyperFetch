"""A half-downloaded torrent still lists its files after a restart.

Reported 2026-10-28: the Files tab of a torrent that was half done came up
empty when the app was opened again, though the app had shown its files and
knew its size. The tab's list after a restart came only from a metadata
file on disk (<infohash>.torrent), looked for in two folders - and that file
goes with any task of the same torrent that is removed or cancelled, sits
in the folder a magnet was sorted FROM, or was never kept by an older
version. Now the list is kept with the task too (task.files, saved), every
time the metadata is read, and the folder it was sorted from is searched.
"""
import json
import os
import shutil

import pytest

import task as T
import torrent

HERE = os.path.dirname(os.path.abspath(__file__))
SINTEL = os.path.join(HERE, "data", "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")
IH = "08ada5a7a6183aae1e09d831df6748d566095a10"


def _magnet_task(tmp_path):
    folder = tmp_path / "dl" / "Video"
    folder.mkdir(parents=True)
    t = T.DownloadTask("magnet:?xt=urn:btih:%s&dn=Sintel" % IH, str(folder / "Sintel"),
                       filename="Sintel")
    t.status, t.total_size, t.downloaded = T.PAUSED, 129241752, 60000000
    t.sort_base = str(tmp_path / "dl")
    return t


def test_the_files_outlive_the_metadata_file(tmp_path):
    t = _magnet_task(tmp_path)
    kept = torrent.kept_torrent(IH)
    shutil.copy(SINTEL, kept)
    first = torrent.list_files(t)
    assert len(first) == 11

    saved = json.loads(json.dumps(t.to_dict()))         # the app is closed
    os.remove(kept)                                     # the metadata file goes
    back = T.DownloadTask.from_dict(saved)              # and opened again
    assert torrent.list_files(back) == first, "the Files tab came up empty"


def test_the_folder_it_was_sorted_from_is_searched(tmp_path):
    """aria2 saved a magnet's metadata in the folder it was added in; the app
    then filed the torrent into Video. A single-file torrent's payload is not a
    folder, so only Video was searched."""
    t = _magnet_task(tmp_path)
    shutil.copy(SINTEL, tmp_path / "dl" / (IH + ".torrent"))
    assert len(torrent.list_files(t)) == 11


def test_reading_the_metadata_remembers_the_files(tmp_path):
    t = _magnet_task(tmp_path)
    assert torrent.apply_metadata(t, SINTEL)
    assert len(t.files) == 11 and all(isinstance(s, int) for _, s in t.files)


def test_a_torrent_of_very_many_files_keeps_only_its_metadata(tmp_path):
    t = _magnet_task(tmp_path)
    torrent.remember_files(t, [("f%d" % i, 1) for i in range(torrent.FILES_KEPT_MAX + 1)])
    assert t.files == []


def test_a_damaged_saved_list_does_not_stop_the_list_loading(tmp_path):
    d = _magnet_task(tmp_path).to_dict()
    d["files"] = [["a.mkv", 10], ["b.mkv", "lots"], "junk", [None], ["c.srt", 3]]
    back = T.DownloadTask.from_dict(d)
    assert back.files == [["a.mkv", 10], ["c.srt", 3]]


def test_a_magnet_whose_file_list_is_lost_is_fetched_again(tmp_path):
    """Saved by an older version: no list kept, and the metadata file gone.
    Its size is known, which used to be enough to skip it."""
    t = _magnet_task(tmp_path)
    scout = torrent.MetadataPrefetcher(lambda: [t])
    assert t in scout._candidates()
    t.files = [["Sintel/Sintel.mp4", 129241752]]
    assert t not in scout._candidates(), "fetched again though its list is kept"
