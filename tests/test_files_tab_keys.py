"""The Files tab picks rows as a file list does (asked 2026-10-30): click,
Ctrl/Shift+click, Shift+arrows and Ctrl+A select rows, and Space or the
right-click menu ticks or unticks the files they stand for - a folder's row
for its files - as one change for aria2. Rows could not be selected at all
before (NoSelection)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

import task as T                                                 # noqa: E402
from gui2.details_drawer import DetailsDrawer                    # noqa: E402
from gui2.download_card import ElideLabel                        # noqa: E402
from queue_manager import QueueManager                           # noqa: E402


def _drawer(tmp_path, selected="", files=None):
    """A paused torrent of four files in Show/: row 0 is the folder, rows
    1-4 its files e1-e4."""
    app = QApplication.instance() or QApplication([])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Show", str(tmp_path / "magnet_.bin"),
                       filename="Show")
    t.status = T.PAUSED
    t.files = [[f, 10] for f in files] if files else [["Show/e%d.mkv" % n, 10]
                                                       for n in (1, 2, 3, 4)]
    t.selected_files = selected
    host = QWidget()
    host.resize(900, 700)
    host.queue = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    host.queue.shutdown()
    host.queue.tasks.append(t)
    d = DetailsDrawer(host)
    d._host_ref = host                     # or the host is collected, and the drawer with it
    host.show()
    d.open_for(t)
    d.tabs.setCurrentWidget(d.files_table.parentWidget())
    app.processEvents()
    assert [p for _r, p in d._file_places] == (files or ["Show/e%d.mkv" % n for n in (1, 2, 3, 4)])
    return d, t


def _rows(d):
    return sorted(i.row() for i in d.files_table.selectionModel().selectedRows())


def test_shift_down_and_space(tmp_path):
    d, t = _drawer(tmp_path)
    tbl = d.files_table
    tbl.setCurrentCell(1, 1)
    QTest.keyClick(tbl, Qt.Key_Down, Qt.ShiftModifier)
    assert _rows(d) == [1, 2]
    QTest.keyClick(tbl, Qt.Key_Space)                  # both ticked: both unticked
    assert t.selected_files == "3,4"
    QTest.keyClick(tbl, Qt.Key_Space)                  # and back
    assert t.selected_files == "1,2,3,4"


def test_ctrl_a_and_a_folder_row(tmp_path):
    d, t = _drawer(tmp_path, selected="1,2")
    tbl = d.files_table
    QTest.keyClick(tbl, Qt.Key_A, Qt.ControlModifier)
    assert _rows(d) == [0, 1, 2, 3, 4]
    QTest.keyClick(tbl, Qt.Key_Space)                  # not all ticked: all are
    assert t.selected_files == "1,2,3,4"
    tbl.setCurrentCell(0, 1)                           # the folder's row: all of it
    d._tick_picked(False)
    assert t.selected_files == "1", "the last file stays ticked, as with Select None"


def test_a_search_narrows_what_a_folder_row_stands_for(tmp_path):
    d, t = _drawer(tmp_path)
    d.file_search.setText("e2")
    QTest.keyClick(d.files_table, Qt.Key_A, Qt.ControlModifier)   # hidden rows too
    QTest.keyClick(d.files_table, Qt.Key_Space)
    assert t.selected_files == "1,3,4"


def test_check_only_selected(tmp_path):
    d, t = _drawer(tmp_path)
    d.files_table.setCurrentCell(3, 1)
    d._only_picked()
    assert t.selected_files == "3"


def test_the_mouse_picks_rows(tmp_path):
    d, _t = _drawer(tmp_path)
    tbl = d.files_table
    vp = tbl.viewport()
    at = lambda r: tbl.visualItemRect(tbl.item(r, 1)).center()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, at(1))
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ShiftModifier, at(3))
    assert _rows(d) == [1, 2, 3]
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, at(2))
    assert _rows(d) == [1, 3]
    name = tbl.cellWidget(4, 0).findChild(ElideLabel)             # on the file's name
    QTest.mouseClick(name, Qt.LeftButton)
    assert _rows(d) == [4]


def test_the_boxes_leave_the_keys_to_the_table(tmp_path):
    d, t = _drawer(tmp_path)
    assert all(cb.focusPolicy() == Qt.NoFocus for _i, cb, _s in d._file_row_widgets)
    assert all(cb.focusPolicy() == Qt.NoFocus for _r, _k, cb in d._folder_rows)
    d._file_row_widgets[0][1].click()                  # a box still ticks its own file
    assert t.selected_files == "2,3,4"


def test_a_box_on_one_of_several_selected_rows_ticks_them_all(tmp_path):
    """Asked 2026-10-31: as in a file list. On a row not selected, only its own."""
    d, t = _drawer(tmp_path)
    tbl = d.files_table
    tbl.setCurrentCell(1, 1)
    QTest.keyClick(tbl, Qt.Key_Down, Qt.ShiftModifier)
    QTest.keyClick(tbl, Qt.Key_Down, Qt.ShiftModifier)
    assert _rows(d) == [1, 2, 3]
    d._file_row_widgets[1][1].click()                  # e2's box, e1-e3 selected
    assert t.selected_files == "4"
    d._file_row_widgets[0][1].click()                  # and back, all three
    assert t.selected_files == "1,2,3,4"
    d._file_row_widgets[3][1].click()                  # e4 is not selected: only e4
    assert t.selected_files == "1,2,3"
    tbl.setCurrentCell(4, 1)                           # one row selected: only its own
    d._file_row_widgets[3][1].click()
    assert t.selected_files == "1,2,3,4"


def test_a_folder_box_on_one_of_several_selected_rows_ticks_them_all(tmp_path):
    """Rows: Show 0, A 1, e1 2, e2 3, B 4, e3 5, e4 6."""
    from PySide6.QtCore import QItemSelectionModel
    files = ["Show/A/e1.mkv", "Show/A/e2.mkv", "Show/B/e3.mkv", "Show/B/e4.mkv"]
    d, t = _drawer(tmp_path, files=files)
    sm = d.files_table.selectionModel()
    for row in (1, 5):                                 # folder A and the file e3
        sm.select(d.files_table.model().index(row, 0),
                  QItemSelectionModel.Select | QItemSelectionModel.Rows)
    box_a = next(cb for _r, k, cb in d._folder_rows if k == "Show/A")
    box_a.click()                                      # A's box: A's files and e3
    assert t.selected_files == "4"
