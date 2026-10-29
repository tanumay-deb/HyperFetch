"""A torrent's folders in its Files tab.

Reported 2026-10-29: a torrent with folders, and folders inside them, listed
only its files - each indented, with no row for any folder, so nothing said
where a file was, and a folder could not be dropped at once. Now, in name
order (folders first, as Explorer lists them), a folder's row comes before
what is in it, with its size and a box that ticks or unticks all of it (one
change for aria2); sorted by size or progress the files are listed flat,
their folders in their names; and a search keeps the folders of the files
it shows.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

import task as T
from gui2.details_drawer import DetailsDrawer

# in the engine's order; the tab lists them by path
FILES = ["Pack/Season 2/e1.mkv", "Pack/Season 1/e1.mkv", "Pack/cover.jpg",
         "Pack/Season 1/e2.mkv"]


def _drawer(tmp_path):
    app = QApplication.instance() or QApplication([])
    base = str(tmp_path / "dl")
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Pack", os.path.join(base, "magnet_.bin"))
    t.status = T.PAUSED
    t.file_progress = [{"index": i + 1, "path": f"{base}/{rel}", "length": 10 * (i + 1),
                        "completed": 0, "selected": True} for i, rel in enumerate(FILES)]
    sent = []
    host = QWidget()
    host.queue = types.SimpleNamespace(tasks=[t],
                                       change_torrent_files=lambda tid, sel: sent.append(sel))
    d = DetailsDrawer(host)
    d._host_ref = host                     # or the host is collected, and the drawer with it
    d.open_for(t)
    app.processEvents()
    return d, sent


def _shown(d):
    """(depth, name) of each row on screen."""
    out = []
    for r in range(d.files_table.rowCount()):
        if d.files_table.isRowHidden(r):
            continue
        w = d.files_table.cellWidget(r, 0)
        depth = (w.layout().contentsMargins().left() - 4) // 16
        out.append((depth, w.findChildren(QWidget)[-1].toolTip()))
    return out


def _box(d, name):
    for r in range(d.files_table.rowCount()):
        w = d.files_table.cellWidget(r, 0)
        if w.findChildren(QWidget)[-1].toolTip() == name:
            return w.findChild(QCheckBox)
    raise AssertionError(name)


def test_each_folder_has_its_row_before_what_is_in_it(tmp_path):
    d, _ = _drawer(tmp_path)
    assert _shown(d) == [(0, "Pack"), (1, "Season 1"), (2, "e1.mkv"), (2, "e2.mkv"),
                         (1, "Season 2"), (2, "e1.mkv"), (1, "cover.jpg")]


def test_a_folder_row_says_its_size(tmp_path):
    d, _ = _drawer(tmp_path)
    sizes = {d.files_table.cellWidget(r, 0).findChildren(QWidget)[-1].toolTip():
             d.files_table.item(r, 1).text() for r in range(d.files_table.rowCount())}
    assert sizes["Season 1"] == "60 B" and sizes["Pack"] == "100 B"


def test_a_folder_box_drops_all_of_it_in_one_change(tmp_path):
    d, sent = _drawer(tmp_path)
    _box(d, "Season 1").click()
    assert sent == ["1,3"], "aria2 was told %s" % sent
    assert _box(d, "Pack").checkState() == Qt.PartiallyChecked
    _box(d, "Season 1").click()
    assert sent[-1] == "1,2,3,4" and _box(d, "Pack").checkState() == Qt.Checked


def test_a_search_keeps_the_folders_of_what_it_shows(tmp_path):
    d, sent = _drawer(tmp_path)
    d.file_search.setText("e2")
    assert _shown(d) == [(0, "Pack"), (1, "Season 1"), (2, "e2.mkv")]
    d.btn_only.click()
    assert sent == ["4"]


def test_sorted_by_size_the_files_are_flat_with_their_folders(tmp_path):
    d, _ = _drawer(tmp_path)
    d._sort_files(1)
    assert _shown(d) == [(0, "Pack/Season 2/e1.mkv"), (0, "Pack/Season 1/e1.mkv"),
                         (0, "Pack/cover.jpg"), (0, "Pack/Season 1/e2.mkv")]
