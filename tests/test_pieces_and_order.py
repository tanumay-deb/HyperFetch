"""uTorrent's pieces strips in the drawer, and "Download in order"
(asked 2026-10-30)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T                 # noqa: E402
import torrent                   # noqa: E402
from gui2.pieces_bar import availability, piece_counts   # noqa: E402

MiB = 1024 * 1024


def test_the_bitfield_is_read_without_its_padding():
    assert torrent.pieces_have("e0", 3) == [True, True, True]
    assert torrent.pieces_have("a8", 5) == [True, False, True, False, True]
    assert torrent.pieces_have("zz", 4) == []


def test_availability_as_utorrent_counts_it():
    assert availability([0, 0, 0]) == 0.0
    assert availability([2, 3, 3, 2]) == 2.5
    seed, half = "f", "c"                         # 4 pieces each
    assert piece_counts([seed, half], 4, torrent.pieces_have) == [2, 2, 1, 1]


def _tor(bits, files, selected=""):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, "C:/dl/x")
    t.files, t.selected_files = files, selected
    t.tor_pieces = (16, 4 * MiB)
    t.tor_bitfield = bits
    return t


def test_the_head_follows_the_biggest_file_from_its_start():
    # 8 MiB file, then a 56 MiB one starting at piece 2; pieces 2-4 are here
    t = _tor("3800", [["a.nfo", 8 * MiB], ["movie.mkv", 56 * MiB]])
    whole, window = torrent.in_order_head(t)
    assert whole == 12 * MiB and window == torrent.IN_ORDER_MIN
    t.selected_files = "1"                        # only the small one
    assert torrent.in_order_head(t)[0] == 0


class _Daemon:
    def __init__(self):
        self.calls = []

    def call(self, method, gid, opts):
        self.calls.append(opts["bt-prioritize-piece"])


def _engine(t):
    e = torrent.TorrentDownloader.__new__(torrent.TorrentDownloader)
    e.t = t
    return e


def test_the_head_is_moved_only_at_the_window_edge_and_given_back_when_off():
    t = _tor("0000", [["movie.mkv", 64 * MiB]])
    t.in_order = True
    e, d = _engine(t), _Daemon()
    e._keep_in_order(d, "g")
    assert d.calls == ["head=%d,tail=%d" % (32 * MiB, torrent.IN_ORDER_TAIL)]
    t.tor_bitfield = "f000"                       # 16 MiB whole: half the window
    e._keep_in_order(d, "g")
    assert len(d.calls) == 2 and d.calls[-1].startswith("head=%d," % (48 * MiB))
    t.tor_bitfield = "f800"                       # 20 MiB: not yet the next edge
    e._keep_in_order(d, "g")
    assert len(d.calls) == 2
    t.in_order = False
    e._keep_in_order(d, "g")
    assert d.calls[-1] == ""
    e._keep_in_order(d, "g")
    assert len(d.calls) == 3


def test_in_order_and_the_pieces_are_saved():
    t = _tor("f000", [["movie.mkv", 64 * MiB]])
    t.in_order = True
    back = T.DownloadTask.from_dict(t.to_dict())
    assert back.in_order and back.tor_bitfield == "f000" and back.tor_pieces == (16, 4 * MiB)
