"""What "also delete from disk" actually removes.

save_path alone is wrong for torrents: it stays at the placeholder the task was
created with (…\Downloads\download.bin) until the download COMPLETES, so for
anything paused or unfinished it names a file that never existed — and the real
payload, a folder beside it named after the torrent, was left on disk while the
app reported success.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T
from gui2.app_shortcuts import ShortcutsMixin as S


def _task(save_path, filename):
    t = T.DownloadTask("magnet:?xt=urn:btih:abc", save_path, filename=filename)
    return t


def test_the_torrent_payload_folder_is_found(tmp_path):
    """The real case from the user's machine."""
    payload = tmp_path / "Hannibal.S01.1080p"
    payload.mkdir()
    (payload / "ep1.mkv").write_bytes(b"x" * 10)
    t = _task(str(tmp_path / "download.bin"), "Hannibal.S01.1080p")
    assert S._payload_paths(t) == [str(payload)]


def test_a_plain_file_download_is_found(tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    t = _task(str(f), "a.zip")
    assert S._payload_paths(t) == [str(f)]


def test_nothing_on_disk_returns_nothing(tmp_path):
    t = _task(str(tmp_path / "download.bin"), "Never.Downloaded")
    assert S._payload_paths(t) == []


def test_placeholder_names_are_never_joined(tmp_path):
    """"torrent" is the engine's own placeholder — a folder of that name in the
    download dir would not be this download's payload."""
    (tmp_path / "torrent").mkdir()
    t = _task(str(tmp_path / "download.bin"), "torrent")
    assert S._payload_paths(t) == []


def test_the_download_folder_itself_is_never_a_target(tmp_path):
    """One bad join here would take every download the user has."""
    t = _task(str(tmp_path / "download.bin"), ".")
    assert S._payload_paths(t) == []
    t2 = _task(str(tmp_path / "download.bin"), "..")
    assert S._payload_paths(t2) == []


def test_a_name_escaping_the_folder_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    t = _task(str(tmp_path / "download.bin"), os.path.join("..", "elsewhere"))
    assert S._payload_paths(t) == []


def test_both_are_returned_when_both_exist(tmp_path):
    f = tmp_path / "download.bin"
    f.write_bytes(b"x")
    payload = tmp_path / "Show.S01"
    payload.mkdir()
    t = _task(str(f), "Show.S01")
    assert sorted(S._payload_paths(t)) == sorted([str(f), str(payload)])


def test_removal_handles_a_directory(tmp_path):
    d = tmp_path / "Show.S01"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "ep.mkv").write_bytes(b"x" * 5)
    assert S._remove_path(str(d)) is True
    assert not d.exists()
