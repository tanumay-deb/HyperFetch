"""A saved download the app cannot read is kept in downloads.json, not dropped.

restore() skipped such a row and the next save wrote only what it had read, so
two saves took it out of the list and its backup. A row that is not an object
at all (null) raised AttributeError out of the window's start."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from queue_manager import QueueManager                  # noqa: E402


def test_an_unreadable_row_survives_saves(tmp_path):
    from gui2.app import DownloadAppV2
    good = {"url": "http://a.example/x.bin", "save_path": str(tmp_path / "x.bin")}
    broken = {"url": "http://a.example/y.bin", "save_path": "y", "segments": [{"index": 0}]}
    q = QueueManager()
    q.shutdown()
    assert q.restore([good, None, broken]) == (1, 2)       # no crash on null

    class Win:
        queue = q
        _state_path = str(tmp_path / "downloads.json")

    for _ in range(2):                                     # the list and its backup
        DownloadAppV2._save_state(Win())
    rows = json.load(open(Win._state_path, encoding="utf-8"))
    assert None in rows and broken in rows and len(rows) == 3
    assert len(json.load(open(Win._state_path + ".bak", encoding="utf-8"))) == 3
