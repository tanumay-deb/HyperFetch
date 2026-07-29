"""Download-card serial numbers (#1, #2, ...).

A card's number identifies it, so it is ranked by when the download was ADDED
and must not shift when the list is re-sorted or filtered. `_sl_map` only reads
`self.queue.tasks`, so it can be exercised without constructing the window.
"""
import types

import task as T
from gui2.app import DownloadAppV2


def _win(tasks):
    stub = types.SimpleNamespace(queue=types.SimpleNamespace(tasks=tasks))
    return DownloadAppV2._sl_map(stub)


def _mk(name, added, size=0):
    t = T.DownloadTask(f"https://x/{name}", f"C:/dl/{name}", filename=name, total_size=size)
    t.added = added
    return t


def test_numbers_follow_added_order_not_list_order():
    a = _mk("zeta.zip", 100)
    b = _mk("alpha.mkv", 200)
    c = _mk("mid.pdf", 300)
    # whatever order the caller happens to hold them in, numbering is by `added`
    sl = _win([c, a, b])
    assert (sl[a.id], sl[b.id], sl[c.id]) == (1, 2, 3)


def test_numbers_unchanged_by_sorting_or_filtering():
    ts = [_mk("zeta.zip", 100), _mk("alpha.mkv", 200), _mk("mid.pdf", 300)]
    baseline = _win(ts)
    # the map is built from the full task list, so re-sorting the VISIBLE list
    # (by name, size, progress...) cannot change any number
    for order in ([ts[1], ts[0], ts[2]], list(reversed(ts)), [ts[2], ts[1], ts[0]]):
        assert _win(order) == baseline


def test_new_download_takes_the_next_number():
    ts = [_mk("a", 100), _mk("b", 200)]
    before = _win(ts)
    ts.append(_mk("c", 300))
    after = _win(ts)
    assert after[ts[0].id] == before[ts[0].id]      # existing keep theirs
    assert after[ts[1].id] == before[ts[1].id]
    assert after[ts[2].id] == 3


def test_ties_broken_deterministically():
    """Two tasks stamped in the same instant must still get stable, distinct
    numbers (seq is the monotonic insertion counter)."""
    a = _mk("a", 100)
    b = _mk("b", 100)
    sl = _win([b, a])
    assert sorted(sl.values()) == [1, 2]
    assert sl == _win([a, b])                        # order-independent


def test_missing_added_stamp_does_not_crash():
    """Legacy records restore with added=0; they simply rank first."""
    legacy = _mk("old", 0)
    fresh = _mk("new", 500)
    sl = _win([fresh, legacy])
    assert sl[legacy.id] == 1 and sl[fresh.id] == 2
