"""A .torrent is anybody's bytes (a web link, a tracker's scrape reply).
Lists nested a few thousand deep - 6 KB - raised RecursionError out of the
readers instead of "cannot be read"."""
import torrent


def _deep(tmp_path):
    deep = b"l" * 3000 + b"e" * 3000
    p = tmp_path / "deep.torrent"
    p.write_bytes(b"d4:infod6:lengthi1e4:name1:x12:piece lengthi1e6:pieces0:1:z"
                  + deep + b"ee")
    return str(p)


def test_a_deeply_nested_torrent_reads_as_unreadable(tmp_path):
    p = _deep(tmp_path)
    assert torrent.parse_torrent_files(p) == []
    assert torrent.is_private_torrent(p) is False
    assert torrent.torrent_top_name(p) == ""
