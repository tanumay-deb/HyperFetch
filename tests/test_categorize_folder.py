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
