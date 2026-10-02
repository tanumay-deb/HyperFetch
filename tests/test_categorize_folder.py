"""Filing a completed torrent into its category folder.

A multi-file torrent resolves save_path to a FOLDER, and the old code returned
early on `not os.path.isfile(path)` — so torrents were never categorised at all.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T
import utils
from gui2.app import DownloadAppV2 as A


def _mk(root, rel, size):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\0" * size)
    return p


def test_the_biggest_file_decides_the_category(tmp_path):
    """A release folder is full of samples, subtitles and nfos; the one large
    file is what the download actually is."""
    d = tmp_path / "Show.S01.1080p"
    _mk(str(d), "Show.S01E01.mkv", 5000)
    _mk(str(d), "Sample/sample.mkv", 50)
    _mk(str(d), "readme.nfo", 10)
    _mk(str(d), "Subs/en.srt", 20)
    assert A._folder_category(str(d)) == utils.category_for("x.mkv")


def test_an_empty_folder_is_other(tmp_path):
    d = tmp_path / "Empty"
    d.mkdir()
    assert A._folder_category(str(d)) == "Other"


def test_a_missing_folder_does_not_raise(tmp_path):
    assert A._folder_category(str(tmp_path / "nope")) == "Other"


def _stub(extras=None):
    """Carries the real _folder_category, since _maybe_categorize calls it
    through self — a bare SimpleNamespace would not have it."""
    saved = []
    return types.SimpleNamespace(
        _extras=dict(extras or {"categorize": True}),
        _save_state=lambda: saved.append(1),
        _folder_category=staticmethod(A._folder_category),
        _saved=saved,
    )


def test_a_torrent_folder_is_moved_into_its_category(tmp_path):
    d = tmp_path / "Show.S01.1080p"
    _mk(str(d), "Show.S01E01.mkv", 5000)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(d), filename="Show.S01.1080p")
    t.status = T.COMPLETED
    stub = _stub()
    A._maybe_categorize(stub, t)
    cat = utils.category_for("x.mkv")
    assert os.path.isdir(tmp_path / cat / "Show.S01.1080p")
    assert not d.exists()
    assert t.save_path == str(tmp_path / cat / "Show.S01.1080p")


def test_a_seeding_torrent_is_left_alone(tmp_path):
    """aria2 still has the payload open to share it — moving the folder would
    break the seed, and on Windows would likely fail outright."""
    d = tmp_path / "Show.S01.1080p"
    _mk(str(d), "Show.S01E01.mkv", 5000)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(d), filename="Show.S01.1080p")
    t.status = T.COMPLETED
    t.seeding = True
    A._maybe_categorize(_stub(), t)
    assert d.exists(), "moved a folder that was still being seeded"


def test_an_already_filed_folder_is_not_moved_again(tmp_path):
    cat = utils.category_for("x.mkv")
    d = tmp_path / cat / "Show.S01"
    _mk(str(d), "ep.mkv", 5000)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(d), filename="Show.S01")
    t.status = T.COMPLETED
    A._maybe_categorize(_stub(), t)
    assert d.exists()
    assert not (tmp_path / cat / cat).exists()


def test_categorising_can_be_turned_off(tmp_path):
    d = tmp_path / "Show.S01"
    _mk(str(d), "ep.mkv", 5000)
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", str(d), filename="Show.S01")
    t.status = T.COMPLETED
    A._maybe_categorize(_stub({"categorize": False}), t)
    assert d.exists()


def test_a_single_file_download_still_works(tmp_path):
    f = _mk(str(tmp_path), "movie.mkv", 100)
    t = T.DownloadTask("https://x/movie.mkv", f, filename="movie.mkv")
    t.status = T.COMPLETED
    A._maybe_categorize(_stub(), t)
    cat = utils.category_for("movie.mkv")
    assert os.path.isfile(tmp_path / cat / "movie.mkv")


# ---- downloads that say who chose their folder (task.sort_base) ---------------

def _done_task(path, name, sort_base):
    t = T.DownloadTask("https://x/" + name, path, filename=name)
    t.status = T.COMPLETED
    t.sort_base = sort_base
    return t


def test_a_late_named_download_moves_from_other_to_its_real_category(tmp_path):
    """A media page has no name to go by when it is added, so it waits in
    Other; its real name is known when it finishes. It goes to the category
    beside Other - not into a folder inside it."""
    f = _mk(str(tmp_path / "Other"), "watch.mp4", 100)
    t = _done_task(f, "watch.mp4", str(tmp_path))
    A._maybe_categorize(_stub(), t)
    assert os.path.isfile(tmp_path / "Video" / "watch.mp4")
    assert not (tmp_path / "Other" / "Video").exists()
    assert t.save_path == str(tmp_path / "Video" / "watch.mp4")


def test_a_finished_file_nobody_can_classify_ends_in_other(tmp_path):
    f = _mk(str(tmp_path), "thing.xyz", 100)
    t = _done_task(f, "thing.xyz", str(tmp_path))
    A._maybe_categorize(_stub(), t)
    assert t.save_path == str(tmp_path / "Other" / "thing.xyz")
    assert os.path.isfile(t.save_path)


def test_a_download_whose_folder_the_user_chose_is_never_moved(tmp_path):
    """Picked "Music" for a video on purpose: it stays in Music."""
    f = _mk(str(tmp_path / "Music"), "concert.mkv", 100)
    t = _done_task(f, "concert.mkv", "")
    A._maybe_categorize(_stub(), t)
    assert t.save_path == f and os.path.isfile(f)
    assert not (tmp_path / "Music" / "Video").exists()


def test_a_torrent_filed_before_it_started_is_not_filed_again(tmp_path):
    d = tmp_path / "Video" / "Show.S01"
    _mk(str(d), "ep.mkv", 5000)
    t = _done_task(str(d), "Show.S01", str(tmp_path))
    stub = _stub()
    A._maybe_categorize(stub, t)
    assert t.save_path == str(d) and d.is_dir()
    assert stub._saved == [], "nothing changed, so nothing to save"


def test_a_download_from_before_this_existed_is_filed_as_it_used_to_be(tmp_path):
    """sort_base None: a known type goes into a category folder where it lies,
    and an unknown one stays put."""
    known = _mk(str(tmp_path), "old.mkv", 100)
    unknown = _mk(str(tmp_path), "old.xyz", 100)
    a, b = _done_task(known, "old.mkv", None), _done_task(unknown, "old.xyz", None)
    A._maybe_categorize(_stub(), a)
    A._maybe_categorize(_stub(), b)
    assert a.save_path == str(tmp_path / "Video" / "old.mkv")
    assert b.save_path == unknown, "an old download was moved into Other"
