"""Removing a torrent leaves another torrent's control file alone
(2026-11-04, list #15).

Two torrents of one name in one folder share the name of aria2's control
file, <name>.aria2. Removing one deleted the other's, and it had to check
every piece again to go on. aria2 writes the infohash in the file.
"""
import os

import task as T
import torrent

A, B = "a" * 40, "b" * 40


def _control(path, infohash, version=1):
    """An aria2 control file as its format says: version, flags, the
    infohash's length and the infohash, then the rest."""
    order = "big" if version == 1 else "little"
    with open(path, "wb") as f:
        f.write(version.to_bytes(2, "big") + bytes(4) + len(infohash).to_bytes(4, order)
                + infohash + bytes(64))


def _task(folder, ih, name="Movie.mkv"):
    return T.DownloadTask("magnet:?xt=urn:btih:%s&dn=x" % ih, str(folder / "magnet_.bin"),
                          filename=name)


def test_another_torrents_control_file_of_the_same_name_stays(tmp_path):
    ctl = tmp_path / "Movie.mkv.aria2"
    _control(ctl, bytes.fromhex(B))
    torrent.cleanup_artifacts(_task(tmp_path, A))
    assert ctl.exists(), "the other torrent's progress went"
    torrent.cleanup_artifacts(_task(tmp_path, B))
    assert not ctl.exists(), "its own stayed"


def test_one_that_names_no_torrent_or_cannot_be_read_goes_as_before(tmp_path):
    for body in (None, b"", b"\x00\x01"):
        ctl = tmp_path / "Movie.mkv.aria2"
        if body is None:
            _control(ctl, b"", version=0)       # an older file, of no torrent
        else:
            ctl.write_bytes(body)
        torrent.cleanup_artifacts(_task(tmp_path, A))
        assert not ctl.exists(), body


def test_the_infohash_is_read_in_either_byte_order(tmp_path):
    for version in (0, 1):
        ctl = tmp_path / ("v%d.aria2" % version)
        _control(ctl, bytes.fromhex(B), version)
        assert torrent.control_infohash(str(ctl)) == B
    assert torrent.control_infohash(str(tmp_path / "none.aria2")) == ""
