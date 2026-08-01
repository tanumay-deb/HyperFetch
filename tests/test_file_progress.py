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
    return [drawer.files_table.cellWidget(r, 2)
            for r in range(drawer.files_table.rowCount())]


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


def test_summary_tiles_count_files_and_bytes(tmp_path):
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
    assert drawer.fs_done.text() == "2 (50%)"      # 2 of 4 complete
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
    assert drawer.files_table.rowCount() == 1
    assert _rows(drawer)[0].value() == 0
