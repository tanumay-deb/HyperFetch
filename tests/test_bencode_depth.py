"""A .torrent is anybody's bytes (a web link, a tracker's scrape reply).
Lists nested a few thousand deep - 6 KB - raised RecursionError out of the
readers instead of "cannot be read"."""
import threading

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


def _ends(fn, *args):
    """What fn(*args) gave back or raised - and that it came back at all."""
    out = {}

    def run():
        try:
            out["value"] = fn(*args)
        except Exception as e:
            out["error"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(5)
    assert not th.is_alive(), "%s never returned" % fn.__name__
    return out


def test_a_negative_string_length_reads_as_unreadable(tmp_path):
    """int() takes "-3": the length stepped the reader back, and it went round
    for ever - the window, on a .torrent opened or dragged in; a thread at full
    speed, on a tracker's scrape reply (sweep 4, 2026-11-25)."""
    bad = b"d4:infod4:name-3:xyz12:piece lengthi1e6:pieces0:ee"
    p = tmp_path / "negative.torrent"
    p.write_bytes(bad)
    assert _ends(torrent.infohash_of, bad) == {"value": ""}
    assert _ends(torrent.trackers_in, b"d8:announce-3:xyze") == {"value": []}
    assert _ends(torrent.parse_torrent_files, str(p)) == {"value": []}
    assert _ends(torrent.torrent_top_name, str(p)) == {"value": ""}
    # what scrape.http_scrape reads a tracker's reply with
    assert isinstance(_ends(torrent._bdecode, b"d5:files-9:xe").get("error"), ValueError)
    # a length with a space in it is no length either (int() takes " 3")
    assert isinstance(_ends(torrent._bdecode, b" 3:abc").get("error"), ValueError)
