"""A big download starts at once.

The engine makes its temp file the full size before the first byte, so every
connection can write at its own offset. It did that with truncate(), and on
Windows truncate() extends a file by WRITING zeros, four kilobytes at a time.
Seen while testing something else (2026-10-05): a 64 GiB download stood at
"Downloading, 0 bytes" for more than 44 seconds on an NVMe drive, writing 64
GiB it was about to write again. On a hard disk that is minutes.

On NTFS the file is now made sparse: a part nobody has written is not stored,
and is not filled in when something lands beyond it. Just setting the length
would not do - NTFS then zero-fills up to the first write of the last
connection instead, which is the same wait a moment later.
"""
import os
import stat
import sys

import pytest

import task as T
import utils
from downloader import Downloader

GIB = 1024 ** 3
MIB = 1024 ** 2


def _on_disk(path):
    """Bytes the file really occupies, as opposed to how long it says it is."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCompressedFileSizeW.restype = ctypes.c_ulong
        high = ctypes.c_ulong(0)
        low = k32.GetCompressedFileSizeW(ctypes.c_wchar_p(path), ctypes.byref(high))
        return (high.value << 32) + low
    return os.stat(path).st_blocks * 512


def _is_sparse(path):
    return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_SPARSE_FILE)


def test_a_big_downloads_temp_file_is_not_written_out_first(tmp_path):
    t = T.DownloadTask("https://example.test/big.iso", str(tmp_path / "big.iso"))
    t.total_size, t.supports_range = GIB, True
    eng = Downloader(t, segments=8)
    temp = utils.temp_download_path(t.id)
    try:
        eng._build_segments()
        assert os.path.getsize(temp) == GIB
        assert len(t.segments) == 8
        assert _on_disk(temp) < MIB, (
            "%d MiB written to disk before the first byte arrived" % (_on_disk(temp) // MIB))
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def test_what_is_written_reads_back_and_the_rest_reads_as_nothing(tmp_path):
    p = str(tmp_path / "x.hfdownload")
    utils.allocate_file(p, 64 * MIB)
    with open(p, "r+b") as f:
        f.seek(60 * MIB)
        f.write(b"A" * 4096)            # the last connection lands first
        f.seek(0)
        f.write(b"B" * 4096)
    with open(p, "rb") as f:
        assert f.read(4096) == b"B" * 4096
        f.seek(30 * MIB)
        assert f.read(4096) == b"\0" * 4096
        f.seek(60 * MIB)
        assert f.read(4096) == b"A" * 4096
    assert os.path.getsize(p) == 64 * MIB


def test_a_file_made_again_starts_empty(tmp_path):
    p = str(tmp_path / "x.hfdownload")
    with open(p, "wb") as f:
        f.write(b"old " * 1000)
    utils.allocate_file(p, MIB)
    assert os.path.getsize(p) == MIB
    assert open(p, "rb").read(4000) == b"\0" * 4000


def test_nothing_is_a_file_of_no_length(tmp_path):
    p = str(tmp_path / "x.hfdownload")
    utils.allocate_file(p, 0)
    assert os.path.getsize(p) == 0


def test_where_the_disk_cannot_leave_gaps_the_file_is_still_made(tmp_path, monkeypatch):
    """FAT and exFAT have no sparse files. There it is made the old way: slow
    for a big file, and no worse than it was."""
    monkeypatch.setattr(utils, "_make_sparse", lambda f: False)
    p = str(tmp_path / "x.hfdownload")
    utils.allocate_file(p, 8 * MIB)
    assert os.path.getsize(p) == 8 * MIB
    with open(p, "rb") as f:
        f.seek(7 * MIB)
        assert f.read(16) == b"\0" * 16


@pytest.mark.skipif(sys.platform != "win32", reason="the sparse flag is an NTFS attribute")
def test_the_finished_file_is_an_ordinary_one(media_server, make_payload, tmp_path):
    """The flag is for the temp file. Left on the finished download it does no
    harm, and is still one more thing that file is that the user did not ask
    for; once every byte is written there is nothing sparse about it."""
    data = media_server.put("files/a.bin", make_payload(2 * MIB))
    t = T.DownloadTask(media_server.url("files/a.bin"), str(tmp_path / "a.bin"))
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == data
    assert not _is_sparse(t.save_path)
    assert _on_disk(t.save_path) >= 2 * MIB
