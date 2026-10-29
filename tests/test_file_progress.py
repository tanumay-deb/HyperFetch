"""Per-file torrent progress in the drawer's Files tab.

The tab used to compute each file's percent from its size on disk, which is not
progress: aria2 preallocates, so every file measured full size the moment the
download started. These assert the live numbers are used instead.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import task as T
from gui2.details_drawer import DetailsDrawer


def _app():
    return QApplication.instance() or QApplication([])


def _torrent_task(tmp_path, files):
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Show",
                       str(tmp_path / "Show" / "payload"))
    t.status = T.DOWNLOADING
    t.file_progress = files
    return t


def _rows(drawer):
    """The files' bars, in order - a folder's row has none."""
    bars = (drawer.files_table.cellWidget(r, 2) for r in range(drawer.files_table.rowCount()))
    return [b for b in bars if b is not None]


def test_bars_show_aria2s_numbers_not_the_size_on_disk(tmp_path):
    app = _app()
    host = QWidget()                       # hold a ref: the drawer's parent
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    os.makedirs(base, exist_ok=True)
    # preallocated on disk at FULL size, but only a quarter really fetched
    with open(os.path.join(base, "ep1.mkv"), "wb") as f:
        f.write(b"\0" * 1000)
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/ep1.mkv", "length": 1000,
         "completed": 250, "selected": True},
    ])

    drawer.open_for(t)
    app.processEvents()
    bar = _rows(drawer)[0]
    assert bar.value() == 25, "read the file's size on disk instead of its progress"


def test_summary_tiles_report_selection_and_progress(tmp_path):
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 100, "completed": 100,
         "selected": True},
        {"index": 2, "path": f"{base}/b.mkv", "length": 100, "completed": 100,
         "selected": True},
        {"index": 3, "path": f"{base}/c.mkv", "length": 100, "completed": 40,
         "selected": True},
        {"index": 4, "path": f"{base}/d.mkv", "length": 100, "completed": 0,
         "selected": True},
    ])

    drawer.open_for(t)
    app.processEvents()
    assert drawer.fs_count.text() == "4"
    assert drawer.fs_sel.text() == "4 of 4"        # nothing unticked yet
    assert drawer.fs_active.text() == "1"          # only c.mkv is part-done
    assert "400" in drawer.fs_size.text() or "B" in drawer.fs_size.text()


def test_a_skipped_file_reads_skipped_and_is_unticked(tmp_path):
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/keep.mkv", "length": 100, "completed": 50,
         "selected": True},
        {"index": 2, "path": f"{base}/skip.nfo", "length": 100, "completed": 0,
         "selected": False},
    ])

    drawer.open_for(t)
    app.processEvents()
    bars = _rows(drawer)
    assert bars[1].format() == "Skipped"
    # aria2's own selection must be reflected, not silently re-enabled
    assert drawer._file_row_widgets[1][1].isChecked() is False
    assert drawer._file_row_widgets[0][1].isChecked() is True


def test_live_tick_moves_the_bars_without_rebuilding(tmp_path):
    """Rebuilding each tick would drop the user's checkbox edits and scroll
    position, so the same widgets must be updated in place."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 1000, "completed": 100,
         "selected": True},
    ])
    drawer.open_for(t)
    app.processEvents()
    first = _rows(drawer)[0]
    assert first.value() == 10

    t.file_progress[0]["completed"] = 900
    drawer.update_live(t, 1234)
    app.processEvents()
    assert _rows(drawer)[0] is first, "the row was rebuilt instead of updated"
    assert first.value() == 90


def test_static_listing_is_used_before_metadata_arrives(tmp_path, monkeypatch):
    """With no live numbers yet, sizes still show but progress must read 0 —
    never a fabricated percentage."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    import gui2.details_drawer as dd
    monkeypatch.setattr(dd._torrent, "list_files",
                        lambda t: [("Show/ep1.mkv", 500)])
    t = _torrent_task(tmp_path, [])
    drawer.open_for(t)
    app.processEvents()
    assert len(_rows(drawer)) == 1
    assert _rows(drawer)[0].value() == 0


def test_unticking_a_file_drops_the_total_size(tmp_path):
    """The reason anyone unticks a file is to not pay for it, so the size has
    to answer that immediately — aria2 is not even told until the selection is
    applied, and a number that waits for a round trip reads as a dead click."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 50 << 20, "completed": 0,
         "selected": True},
        {"index": 2, "path": f"{base}/b.mkv", "length": 50 << 20, "completed": 0,
         "selected": True},
    ])
    drawer.open_for(t)
    app.processEvents()
    both = drawer.fs_size.text()
    assert drawer.fs_sel.text() == "2 of 2"

    drawer._file_row_widgets[1][1].setChecked(False)     # untick the second
    app.processEvents()
    assert drawer.fs_sel.text() == "1 of 2"
    assert drawer.fs_size.text() != both
    assert "50" in drawer.fs_size.text()                 # half of 100 MB


def test_a_file_with_a_few_bytes_reads_under_one_percent(tmp_path):
    """Rounding made these rows show 0% while the Downloading tile counted
    them, so the tile looked wrong."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 100000, "completed": 5,
         "selected": True},
    ])
    drawer.open_for(t)
    app.processEvents()
    assert _rows(drawer)[0].format() == "<1%"
    assert drawer.fs_active.text() == "1"


def test_a_restored_torrent_does_not_claim_zero_percent(tmp_path, monkeypatch):
    """file_progress is transient, so anything restored from disk starts with
    none. Rendering that as "0%" told the user nothing had downloaded — on a
    torrent the daemon reported as 7.49/7.49 GB complete."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    import gui2.details_drawer as dd
    monkeypatch.setattr(dd._torrent, "list_files",
                        lambda t: [("Show/ep1.mkv", 500), ("Show/ep2.mkv", 500)])
    t = _torrent_task(tmp_path, [])          # no live per-file data
    t.status = T.PAUSED
    drawer.open_for(t)
    app.processEvents()
    fmts = [bar.format() for bar in _rows(drawer)]
    assert fmts == ["—", "—"], f"claimed a progress it does not know: {fmts}"


def test_a_completed_torrent_shows_its_files_as_done(tmp_path, monkeypatch):
    """A finished torrent's files ARE all present, so with no live numbers the
    honest answer is 100%, not "unknown"."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    import gui2.details_drawer as dd
    monkeypatch.setattr(dd._torrent, "list_files",
                        lambda t: [("Show/ep1.mkv", 500), ("Show/ep2.mkv", 500)])
    t = _torrent_task(tmp_path, [])
    t.status = T.COMPLETED
    drawer.open_for(t)
    app.processEvents()
    vals = [bar.value() for bar in _rows(drawer)]
    assert vals == [100, 100]


def test_live_numbers_still_win(tmp_path):
    """When the engine IS reporting, that is the truth — not the fallback.
    (A COMPLETED torrent was the example here, but those numbers are fetched
    only while the tab is open and can be from its start: a finished torrent's
    files read as done - test_a_completed_torrents_old_numbers_do_not_show.)"""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 1000, "completed": 250,
         "selected": True}])
    t.status = T.PAUSED                       # the fallback would say "—"
    drawer.open_for(t)
    app.processEvents()
    assert drawer.files_table.cellWidget(0, 2).value() == 25


def test_a_completed_torrents_old_numbers_do_not_show(tmp_path):
    """The numbers are fetched only while the Files tab is open, so a torrent
    that finished with the tab shut kept the ones from its start: 7-34% a file
    and "Downloading 95" on a completed torrent (reported 2026-10-29)."""
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 1000, "completed": 250, "selected": True},
        {"index": 2, "path": f"{base}/b.mkv", "length": 1000, "completed": 70, "selected": True},
        {"index": 3, "path": f"{base}/c.nfo", "length": 100, "completed": 40, "selected": False},
    ])
    t.status = T.COMPLETED
    drawer.open_for(t)
    app.processEvents()
    assert [(b.value(), b.format()) for b in _rows(drawer)] == [
        (100, "100%"), (100, "100%"), (40, "Skipped")]
    assert drawer.fs_active.text() == "0"


def test_the_numbers_come_back_after_a_restart(tmp_path):
    """Saved with the task: a restarted PC with no internet shows the files as
    they were, not "—" (asked 2026-10-29)."""
    import json
    app = _app()
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 1000, "completed": 470, "selected": True},
        {"index": 2, "path": f"{base}/b.mkv", "length": 1000, "completed": 0, "selected": False}])
    t.files = [["Show/a.mkv", 1000], ["Show/b.mkv", 1000]]
    t.status = T.PAUSED
    t.files_at = 1793266370.5
    back = T.DownloadTask.from_dict(json.loads(json.dumps(t.to_dict())))
    assert back.file_progress == t.file_progress and back.files_at == t.files_at
    host = QWidget()                       # held: the drawer goes with its parent
    drawer = DetailsDrawer(host)
    drawer.open_for(back)
    app.processEvents()
    assert [b.format() for b in _rows(drawer)] == ["47%", "Skipped"]

    t.files = []                     # past the file list's cap: neither is kept
    assert T.DownloadTask.from_dict(json.loads(json.dumps(t.to_dict()))).file_progress == []


def test_the_corner_says_how_fresh_the_numbers_are(tmp_path):
    """While it downloads, a countdown to the next read (once a minute);
    once it stops, how old the last numbers are; nothing once finished
    (asked 2026-10-29)."""
    import time
    app = _app()
    host = QWidget()
    drawer = DetailsDrawer(host)
    base = str(tmp_path / "Show")
    t = _torrent_task(tmp_path, [
        {"index": 1, "path": f"{base}/a.mkv", "length": 1000, "completed": 470, "selected": True}])
    seen = []
    for status, ago in ((T.DOWNLOADING, 20), (T.DOWNLOADING, 61), (T.PAUSED, 300),
                        (T.COMPLETED, 300)):
        t.status, t.files_at = status, time.time() - ago
        drawer.open_for(t)
        app.processEvents()
        seen.append(drawer.files_fresh.text())
    assert seen == ["Refreshing in 40 s", "Refreshing…", "Updated 5 min ago", ""]
