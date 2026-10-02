"""The category a download is listed under matches the folder it is filed in.

A torrent is filed by what it holds (torrent.sort_into_category), but every
list judged it by its name - and a torrent's name is a folder name, with no
extension and often no hint. The bundled Sintel sample is saved under Video
and was listed under Other: missing from the Video filter, from the Video
count, from "category:video" in search, and the same in the web client and in
history.

utils.category_of(task) is the one answer now. For a torrent the app filed,
it is the category folder it sits in. For everything else it is what the name
says, as before - a media page waits in Other until it finishes, but it is a
video the moment its name says so.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T
import utils

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def _torrent(base, folder="Video", name="Sintel", entry="magnet_.bin", sort_base=True):
    """A torrent task sitting in base/folder. entry is its save_path's last
    part: the placeholder while it runs, the payload's own name once done."""
    where = os.path.join(str(base), folder) if folder else str(base)
    t = T.DownloadTask(MAGNET, os.path.join(where, entry), filename=name)
    t.sort_base = str(base) if sort_base is True else sort_base
    return t


# ---- the one answer ------------------------------------------------------------

def test_a_torrent_is_listed_under_the_folder_it_was_filed_in(tmp_path):
    assert utils.category_for("Sintel") == "Other", "the name alone says nothing"
    assert utils.category_of(_torrent(tmp_path)) == "Video"
    assert utils.category_of(_torrent(tmp_path, entry="Sintel")) == "Video", "finished"
    assert utils.category_of(_torrent(tmp_path, folder="Music")) == "Music"
    assert utils.category_of(_torrent(tmp_path, folder="Other", name="Show.1080p")) == "Other", \
        "filed by its contents: the keywords in its name do not overrule that"


def test_a_torrent_waiting_for_its_metadata_goes_by_its_name(tmp_path):
    """It sits in the download folder itself; nothing is known but the name."""
    assert utils.category_of(_torrent(tmp_path, folder="", name="Show.S01.1080p")) == "Video"
    assert utils.category_of(_torrent(tmp_path, folder="", name="Sintel")) == "Other"


def test_a_file_download_goes_by_its_name_even_while_it_waits_in_other(tmp_path):
    """A media page has no name when it is added and waits in Other until it
    finishes. Once its name is known it is a video, wherever it is waiting."""
    t = T.DownloadTask("https://x.example/watch", str(tmp_path / "Other" / "watch.bin"),
                       filename="Clip.mp4")
    t.sort_base = str(tmp_path)
    assert utils.category_of(t) == "Video"


def test_a_folder_the_user_chose_does_not_decide(tmp_path):
    """Put there by hand, or from before the app recorded who chose: the folder
    may be named anything, so the name is all there is to go by."""
    assert utils.category_of(_torrent(tmp_path, folder="Music", sort_base="")) == "Other"
    assert utils.category_of(_torrent(tmp_path, folder="Music", sort_base=None)) == "Other"


def test_only_a_category_folder_directly_under_the_sorted_folder_counts(tmp_path):
    assert utils.category_of(_torrent(tmp_path, folder="Stuff")) == "Other"
    assert utils.category_of(_torrent(tmp_path, folder=os.path.join("x", "Video"))) == "Other"


def test_the_folders_name_is_matched_whatever_its_case(tmp_path):
    assert utils.category_of(_torrent(tmp_path, folder="video")) == "Video"


# ---- every list uses it ----------------------------------------------------------

def test_the_sidebar_counts_it_under_its_folder(tmp_path):
    from gui2.app import DownloadAppV2
    web = T.DownloadTask("https://x.example/a.zip", str(tmp_path / "Compressed" / "a.zip"),
                         filename="a.zip")
    win = types.SimpleNamespace(queue=types.SimpleNamespace(tasks=[_torrent(tmp_path), web]))
    counts = DownloadAppV2._counts(win)
    assert (counts["Video"], counts["Other"], counts["Compressed"], counts["All"]) == (1, 0, 1, 2)


def test_the_sidebar_filter_lists_it_under_its_folder(tmp_path):
    from gui2 import app
    t = _torrent(tmp_path)
    assert app._in_filter(t, "Video")
    assert not app._in_filter(t, "Other")
    assert app._in_filter(t, "All")
    t.status = T.PAUSED
    assert app._in_filter(t, "Paused") and not app._in_filter(t, "Active")


def test_search_by_category_finds_it(tmp_path):
    from gui2 import search
    t = _torrent(tmp_path)
    assert search.filter_tasks([t], "category:video") == [t]
    assert search.filter_tasks([t], "category:other") == []


def test_the_web_client_is_told_the_same(tmp_path, monkeypatch):
    from api_server import create_app
    from test_web_api import _Queue, _get, _login
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "appdata"))
    q = _Queue()
    q.tasks.append(_torrent(tmp_path))
    app = create_app(q, str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()
    _login(c)
    row = _get(c, "/api/downloads").get_json()["downloads"][0]
    assert row["category"] == "Video"
    assert _get(c, "/api/stats").get_json()["byCategory"] == {"Video": 1}


def test_history_remembers_the_category_it_was_filed_in(tmp_path, monkeypatch):
    import history
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "appdata"))
    t = _torrent(tmp_path, entry="Sintel")
    t.status = T.COMPLETED
    history.record(t)
    assert history.load()[-1]["category"] == "Video"
