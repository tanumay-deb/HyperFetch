"""Where a download is filed, and when.

Every download the app sorts itself (New Download -> Category "Auto", with
sorting switched on) remembers the folder its category folders live under:
task.sort_base. A torrent is filed from its metadata - the biggest file it is
going to fetch decides - before any of it is downloaded, so the payload is
written where it will stay. What cannot be classified goes to Other.

sort_base says who chose the folder:
  a folder   the app sorts this download under it;
  ""         the user picked the place - it is never moved;
  None       a download from before this existed - filed as it used to be.
"""
import os
import urllib.parse

import task as T
import torrent
import utils


def _bencode(v):
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, list):
        return b"l" + b"".join(_bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(v[k]) for k in sorted(v)) + b"e"
    raise TypeError(v)


def make_torrent(path, name, files=None, length=0, private=False, trackers=()):
    """Write a .torrent. files: [(relative path, size)] for a multi-file one."""
    info = {b"name": name.encode(), b"piece length": 16384, b"pieces": b"\0" * 20}
    if files is not None:
        info[b"files"] = [{b"path": [p.encode() for p in rel.split("/")], b"length": size}
                          for rel, size in files]
    else:
        info[b"length"] = length
    if private:
        info[b"private"] = 1
    meta = {b"info": info}
    if trackers:
        meta[b"announce"] = trackers[0].encode()
        meta[b"announce-list"] = [[t.encode()] for t in trackers]
    with open(path, "wb") as f:
        f.write(_bencode(meta))
    return str(path)


# ---- the field ---------------------------------------------------------------

def test_a_new_task_has_not_been_decided_and_the_choice_survives_a_restart():
    t = T.DownloadTask("https://x.example/a.zip", "a.zip")
    assert t.sort_base is None
    for value in ("D:\\Downloads", "", None):
        t.sort_base = value
        assert T.DownloadTask.from_dict(t.to_dict()).sort_base == value


def test_a_list_saved_before_this_existed_loads_as_not_decided():
    d = T.DownloadTask("https://x.example/a.zip", "a.zip").to_dict()
    d.pop("sort_base", None)
    assert T.DownloadTask.from_dict(d).sort_base is None


# ---- what a torrent is, from its metadata ------------------------------------

def test_the_biggest_file_decides_a_torrents_category(tmp_path):
    """A release folder is full of samples, subtitles and nfos; the one large
    file is what the download is."""
    show = make_torrent(tmp_path / "s.torrent", "Show.S01", files=[
        ("Show.S01E01.mkv", 5000), ("Sample/sample.mkv", 50), ("readme.nfo", 10),
        ("Subs/en.srt", 20)])
    assert torrent.torrent_category(show) == "Video"
    album = make_torrent(tmp_path / "a.torrent", "Album",
                         files=[("01.flac", 300), ("cover.jpg", 20)])
    assert torrent.torrent_category(album) == "Music"


def test_a_single_file_torrent_is_its_file(tmp_path):
    p = make_torrent(tmp_path / "x.torrent", "setup.exe", length=10)
    assert torrent.torrent_category(p) == "Programs"


def test_a_torrent_nobody_can_classify_is_other(tmp_path):
    iso = make_torrent(tmp_path / "i.torrent", "disc",
                       files=[("disc.iso", 900), ("readme.txt", 5)])
    assert torrent.torrent_category(iso) == "Other"
    assert torrent.torrent_category(str(tmp_path / "missing.torrent")) == "Other"


def test_only_the_files_being_fetched_count(tmp_path):
    """Untick the film and keep the soundtrack: it is a Music download."""
    p = make_torrent(tmp_path / "m.torrent", "Film", files=[
        ("film.mkv", 9000), ("ost/01.flac", 300), ("ost/02.flac", 200)])
    assert torrent.torrent_category(p) == "Video"
    assert torrent.torrent_category(p, "2,3") == "Music"
    assert torrent.torrent_category(p, "2-3") == "Music"
    assert torrent.torrent_category(p, "nonsense") == "Video", "an unreadable choice means all"


def test_the_private_flag_is_read_from_the_info_dict(tmp_path):
    assert torrent.is_private_torrent(make_torrent(tmp_path / "p.torrent", "p", length=1, private=True))
    assert not torrent.is_private_torrent(make_torrent(tmp_path / "q.torrent", "q", length=1))
    assert not torrent.is_private_torrent(str(tmp_path / "missing.torrent"))
    (tmp_path / "junk.torrent").write_bytes(b"not bencode")
    assert not torrent.is_private_torrent(str(tmp_path / "junk.torrent"))


def test_the_top_level_name_is_the_torrents_own(tmp_path):
    multi = make_torrent(tmp_path / "s.torrent", "Show.S01", files=[("ep1.mkv", 5), ("ep2.mkv", 5)])
    assert torrent.torrent_top_name(multi) == "Show.S01"
    assert torrent.torrent_top_name(make_torrent(tmp_path / "x.torrent", "a.zip", length=3)) == "a.zip"
    assert torrent.torrent_top_name(str(tmp_path / "missing.torrent")) == ""


# ---- filing a torrent before it starts ---------------------------------------

def _magnet_task(base, ih="a" * 40, trackers=()):
    url = "magnet:?xt=urn:btih:" + ih + "".join(
        "&tr=" + urllib.parse.quote(tr, safe="") for tr in trackers)
    t = T.DownloadTask(url, os.path.join(str(base), "magnet_.bin"))
    t.sort_base = str(base)
    return t


def test_a_torrent_is_filed_under_its_category_before_it_starts(tmp_path):
    meta = make_torrent(tmp_path / "m.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    t = _magnet_task(tmp_path)
    assert torrent.sort_into_category(t, meta) is True
    assert t.save_path == os.path.join(str(tmp_path), "Video", "magnet_.bin")
    assert os.path.isdir(tmp_path / "Video")
    assert torrent.sort_into_category(t, meta) is False, "filed twice"


def test_an_unclassifiable_torrent_is_filed_under_other(tmp_path):
    meta = make_torrent(tmp_path / "m.torrent", "disc", files=[("disc.iso", 900)])
    t = _magnet_task(tmp_path)
    assert torrent.sort_into_category(t, meta) is True
    assert os.path.dirname(t.save_path) == os.path.join(str(tmp_path), "Other")


def test_one_that_waited_in_other_goes_to_its_real_category(tmp_path):
    """Added without a dialog, a magnet has no name to go by and waits in
    Other; its metadata then says what it is."""
    meta = make_torrent(tmp_path / "m.torrent", "Album", files=[("01.flac", 500)])
    t = _magnet_task(tmp_path)
    t.save_path = os.path.join(str(tmp_path), "Other", "magnet_.bin")
    assert torrent.sort_into_category(t, meta) is True
    assert os.path.dirname(t.save_path) == os.path.join(str(tmp_path), "Music")


def test_a_torrent_whose_place_the_user_chose_is_left_there(tmp_path):
    meta = make_torrent(tmp_path / "m.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    for chosen in ("", None):
        t = _magnet_task(tmp_path)
        t.sort_base = chosen
        assert torrent.sort_into_category(t, meta) is False
        assert t.save_path == os.path.join(str(tmp_path), "magnet_.bin")


def test_a_torrent_already_on_disk_is_not_filed_away_from_its_bytes(tmp_path):
    """One that started before it could be filed has its payload in the old
    folder. Filing it elsewhere would start it again from nothing and leave
    the old copy behind; it is filed when it finishes instead."""
    meta = make_torrent(tmp_path / "m.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    (tmp_path / "Show.S01").mkdir()
    assert torrent.sort_into_category(_magnet_task(tmp_path), meta) is False

    single = make_torrent(tmp_path / "s.torrent", "film.mkv", length=500)
    (tmp_path / "film.mkv.aria2").write_bytes(b"")          # aria2's control file
    assert torrent.sort_into_category(_magnet_task(tmp_path, ih="b" * 40), single) is False

    counted = _magnet_task(tmp_path, ih="c" * 40)
    counted.downloaded = 4096                                # bytes it remembers having
    other = make_torrent(tmp_path / "o.torrent", "Other.Show", files=[("ep1.mkv", 500)])
    assert torrent.sort_into_category(counted, other) is False


def test_metadata_that_cannot_be_read_files_nothing(tmp_path):
    (tmp_path / "junk.torrent").write_bytes(b"not bencode")
    t = _magnet_task(tmp_path)
    assert torrent.sort_into_category(t, str(tmp_path / "junk.torrent")) is False
    assert torrent.sort_into_category(t, "") is False
    assert not (tmp_path / "Other").exists()


def test_the_file_choice_decides_where_it_is_filed(tmp_path):
    meta = make_torrent(tmp_path / "m.torrent", "Film", files=[
        ("film.mkv", 9000), ("ost/01.flac", 300)])
    t = _magnet_task(tmp_path)
    t.selected_files = "2"
    torrent.sort_into_category(t, meta)
    assert os.path.dirname(t.save_path) == os.path.join(str(tmp_path), "Music")


# ---- a private torrent and the public trackers --------------------------------

def test_a_private_torrent_gets_only_its_own_trackers(tmp_path):
    """Measured on aria2 1.37: the daemon's --bt-tracker list is added to a
    private torrent like any other, and announced to when its own tracker
    fails. Only options given with the add keep it off."""
    meta = make_torrent(tmp_path / "p.torrent", "p", length=1, private=True,
                        trackers=["https://private.example/pk/announce"])
    t = T.DownloadTask(meta, str(tmp_path / "p"), filename="p.torrent")
    opts = torrent.tracker_opts(t, meta)
    assert opts["bt-tracker"] == ""
    assert opts["bt-exclude-tracker"].split(",") == torrent.PUBLIC_TRACKERS


def test_a_public_torrent_keeps_the_daemons_public_trackers(tmp_path):
    meta = make_torrent(tmp_path / "q.torrent", "q", length=1)
    t = T.DownloadTask(meta, str(tmp_path / "q"), filename="q.torrent")
    assert torrent.tracker_opts(t, meta) == {}


def test_a_magnet_nobody_has_the_metadata_of_is_treated_as_public(tmp_path):
    assert torrent.tracker_opts(_magnet_task(tmp_path), "") == {}


def test_a_private_magnets_own_copy_of_a_public_tracker_is_kept(tmp_path):
    """The magnet's tr= list is the torrent's own; only what the app would add
    is kept away."""
    meta = make_torrent(tmp_path / "p.torrent", "p", length=1, private=True)
    mine = torrent.PUBLIC_TRACKERS[1]
    t = _magnet_task(tmp_path, trackers=[mine.upper()])
    kept_off = torrent.tracker_opts(t, meta)["bt-exclude-tracker"].split(",")
    assert mine not in kept_off
    assert kept_off == [p for p in torrent.PUBLIC_TRACKERS if p != mine]


# ---- where a new download is put, and who chose -------------------------------

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def test_the_app_puts_a_file_in_its_category_folder_and_remembers_it_chose(tmp_path):
    base = str(tmp_path)
    assert utils.place_download(base, "https://x.example/a.mkv", "a.mkv") == (
        os.path.join(base, "Video"), base)
    assert utils.place_download(base, "https://x.example/a.xyz", "a.xyz") == (
        os.path.join(base, "Other"), base)
    assert os.path.isdir(tmp_path / "Other")


def test_a_torrent_file_says_what_it_holds_so_it_is_filed_at_once(tmp_path):
    base = str(tmp_path / "dl")
    os.makedirs(base)
    src = make_torrent(tmp_path / "s.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    assert utils.place_download(base, src, "s.torrent") == (os.path.join(base, "Video"), base)
    assert os.path.isdir(os.path.join(base, "Video"))


def test_a_magnet_waits_in_the_download_folder_until_its_metadata_says(tmp_path):
    base = str(tmp_path)
    assert utils.place_download(base, MAGNET, "magnet_.bin") == (base, base)
    (tmp_path / "junk.torrent").write_bytes(b"not bencode")
    assert utils.place_download(base, str(tmp_path / "junk.torrent"), "junk.torrent") == (base, base)


def test_a_category_the_user_picked_is_theirs(tmp_path):
    base = str(tmp_path)
    assert utils.place_download(base, "https://x.example/a.mkv", "a.mkv", category="Music") == (
        os.path.join(base, "Music"), "")
    assert utils.place_download(base, MAGNET, "magnet_.bin", category="Video") == (
        os.path.join(base, "Video"), "")


def test_with_sorting_off_everything_stays_where_it_was_put(tmp_path):
    base = str(tmp_path)
    assert utils.place_download(base, "https://x.example/a.mkv", "a.mkv", categorize=False) == (base, "")
    assert utils.place_download(base, MAGNET, "magnet_.bin", categorize=False) == (base, "")
    assert not (tmp_path / "Video").exists()


# ---- the add paths carry the choice onto the task -----------------------------

def _window(base, extras=None):
    """Just enough of the main window to add a download without its dialog."""
    import types
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from gui2.app import DownloadAppV2
    added = []
    queue = types.SimpleNamespace(
        queues={"Main": None}, tasks=[], segments=8,
        add_task=lambda t, start=True: added.append(t))
    win = types.SimpleNamespace(
        save_dir=str(base), segments=8, queue=queue,
        _extras=dict({"skip_new_download_dialog": True}, **(extras or {})),
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _save_state=lambda: None, refresh=lambda: None)
    win._quick_values = lambda *a: DownloadAppV2._quick_values(win, *a)
    return win, added, DownloadAppV2


def test_a_download_added_in_the_window_remembers_who_chose_its_folder(tmp_path):
    win, added, App = _window(tmp_path)
    App._add_download(win, "https://x.example/setup.exe", "setup.exe", None)
    App._add_download(win, "https://x.example/notes.xyz", "notes.xyz", None)
    App._add_download(win, MAGNET, "", None)
    exe, unknown, magnet = added
    assert os.path.dirname(exe.save_path) == str(tmp_path / "Programs")
    assert os.path.dirname(unknown.save_path) == str(tmp_path / "Other")
    assert os.path.dirname(magnet.save_path) == str(tmp_path), "nothing is known about it yet"
    assert [t.sort_base for t in added] == [str(tmp_path)] * 3


def test_with_sorting_switched_off_the_window_marks_the_folder_as_chosen(tmp_path):
    win, added, App = _window(tmp_path, {"categorize": False})
    App._add_download(win, "https://x.example/setup.exe", "setup.exe", None)
    assert os.path.dirname(added[0].save_path) == str(tmp_path)
    assert added[0].sort_base == ""


# ---- aria2's control file goes when the download does --------------------------
#
# aria2 keeps <payload name>.aria2 beside a download it has not finished, and
# deletes it itself when the download completes. Cancelling or deleting an
# unfinished torrent is the app's job to tidy.

def test_cleanup_finds_the_control_file_of_an_unfinished_torrent(tmp_path, monkeypatch):
    """Until it finishes, a torrent's save_path is the placeholder it was added
    with (.../magnet_.bin), but aria2 names the control file after the payload.
    The task's filename - set once the payload is known - says which it is."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    (tmp_path / "Show.S01").mkdir()
    mine = tmp_path / "Show.S01.aria2"
    mine.write_bytes(b"control")
    other = tmp_path / "Other.Show.aria2"
    other.write_bytes(b"another torrent's")
    t = _magnet_task(tmp_path)
    t.filename = "Show.S01"
    torrent.cleanup_artifacts(t)
    assert not mine.exists(), "the control file of a cancelled torrent was left behind"
    assert other.exists(), "another torrent's control file was taken with it"
    assert (tmp_path / "Show.S01").is_dir(), "the payload is not this function's to remove"


def test_cleanup_reads_the_name_from_the_metadata_when_the_task_has_none(tmp_path, monkeypatch):
    """Cancelled before the engine named it: the saved metadata still says what
    aria2 called the payload."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    make_torrent(os.path.join(torrent.metadata_dir(), "a" * 40 + ".torrent"),
                 "Show.S01", files=[("ep1.mkv", 500)])
    ctl = tmp_path / "Show.S01.aria2"
    ctl.write_bytes(b"control")
    t = _magnet_task(tmp_path)
    t.filename = "torrent"
    torrent.cleanup_artifacts(t)
    assert not ctl.exists()


def test_cleanup_never_guesses_from_a_placeholder_name(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    stranger = tmp_path / "torrent.aria2"
    stranger.write_bytes(b"not ours to judge")
    t = _magnet_task(tmp_path)
    t.filename = "torrent"
    torrent.cleanup_artifacts(t)
    assert stranger.exists()


def test_cleanup_of_a_torrent_file_task_removes_our_copy_never_the_users_file(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "app"))
    src = make_torrent(tmp_path / "mine.torrent", "film.mkv", length=500)
    ih = torrent.torrent_infohash(src)
    kept = os.path.join(torrent.metadata_dir(), ih + ".torrent")
    make_torrent(kept, "film.mkv", length=500)
    ctl = tmp_path / "dl" / "film.mkv.aria2"
    ctl.parent.mkdir()
    ctl.write_bytes(b"control")
    t = T.DownloadTask(src, str(tmp_path / "dl" / "torrent"), filename="torrent")
    t.infohash = ih
    torrent.cleanup_artifacts(t)
    assert not ctl.exists()
    assert not os.path.exists(kept), "our copy in app data outlived the task"
    assert os.path.isfile(src), "deleted the user's own .torrent file"
