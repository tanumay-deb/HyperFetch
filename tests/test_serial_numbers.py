"""Download-card serial numbers (#1, #2, ...).

A card's number identifies it, so it is ranked by when the download was ADDED
and must not shift when the list is re-sorted or filtered. `_sl_map` only reads
`self.queue.tasks`, so it can be exercised without constructing the window.
"""
import json
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


# ---- state load/save safety: a failed READ must never become data loss ----
def _app(tmp_path, monkeypatch):
    """A DownloadAppV2 stub with just the state plumbing under test."""
    import os
    import utils
    from gui2.app import DownloadAppV2
    stub = types.SimpleNamespace(
        _state_path=str(tmp_path / "downloads.json"),
        queue=None,
    )
    # A real queue: restore() lives in QueueManager now, and a stub would
    # be a second copy of its contract.
    from queue_manager import QueueManager
    stub.queue = QueueManager()
    return stub, DownloadAppV2


def test_transient_read_failure_does_not_wipe_the_list(tmp_path, monkeypatch):
    """The real bug: utils.load_json returns its default on ANY OSError, so a
    file briefly locked by a concurrent save read as 'no downloads' — and the
    next save wrote [] over it."""
    import utils
    from gui2.app import DownloadAppV2
    p = tmp_path / "downloads.json"
    good = [T.DownloadTask("https://x/a.zip", str(tmp_path / "a.zip"),
                           filename="a.zip").to_dict()]
    utils.save_json(str(p), good)

    stub, cls = _app(tmp_path, monkeypatch)
    calls = {"n": 0}
    real_open = open

    def locked(path, *a, **k):
        # deny every read of the state file, as a concurrent save would
        if str(path) == str(p):
            calls["n"] += 1
            raise PermissionError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cls._load_state(stub)
    assert stub._state_load_failed is True
    assert calls["n"] > 1                      # it retried before giving up

    # and the guard must block the save that would have destroyed the file
    stub.refresh = lambda: None
    cls._save_state(stub)
    assert json.loads(p.read_text(encoding="utf-8")) == good     # untouched


def test_read_succeeding_on_retry_is_used(tmp_path, monkeypatch):
    import utils
    from gui2.app import DownloadAppV2
    p = tmp_path / "downloads.json"
    good = [T.DownloadTask("https://x/a.zip", str(tmp_path / "a.zip"),
                           filename="a.zip").to_dict()]
    utils.save_json(str(p), good)
    stub, cls = _app(tmp_path, monkeypatch)

    state = {"n": 0}
    real_open = open

    def flaky(path, *a, **k):
        if str(path) == str(p):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError("locked")   # first read blocked
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", flaky)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cls._load_state(stub)
    assert stub._state_load_failed is False
    assert len(stub.queue.tasks) == 1


def test_backup_is_rotated_and_used_for_recovery(tmp_path, monkeypatch):
    import utils
    from gui2.app import DownloadAppV2
    p = tmp_path / "downloads.json"
    good = [T.DownloadTask("https://x/a.zip", str(tmp_path / "a.zip"),
                           filename="a.zip").to_dict()]
    utils.save_json(str(p), good)
    utils.save_json(str(p), good, keep_backup=True)      # rotates the .bak
    assert (tmp_path / "downloads.json.bak").is_file()

    stub, cls = _app(tmp_path, monkeypatch)
    real_open = open

    def only_main_fails(path, *a, **k):
        if str(path) == str(p):
            raise PermissionError("locked")              # main copy unreadable
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", only_main_fails)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cls._load_state(stub)
    assert len(stub.queue.tasks) == 1                    # recovered from .bak
    assert stub._state_load_failed is False              # saving stays enabled


def test_genuinely_absent_file_is_not_a_failure(tmp_path, monkeypatch):
    """A first run has no file at all — that must stay a normal empty list."""
    from gui2.app import DownloadAppV2
    stub, cls = _app(tmp_path, monkeypatch)
    cls._load_state(stub)
    assert stub._state_load_failed is False
    assert stub.queue.tasks == []
