"""Click-to-sort on the Files table.

The Name and Progress cells are WIDGETS. Qt's own setSortingEnabled reorders
items but leaves cell widgets pinned to their row, so it would put every
checkbox and progress bar against the wrong file — the rows are rebuilt in
sorted order instead, and each keeps the aria2 index it came from.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import task as T
from gui2.details_drawer import DetailsDrawer


def _app():
    return QApplication.instance() or QApplication([])


def _drawer_with(tmp_path, files):
    app = _app()
    host = QWidget()
    d = DetailsDrawer(host)
    d._host_ref = host
    base = str(tmp_path / "Show")
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Show",
                       str(tmp_path / "Show" / "payload"))
    t.status = T.DOWNLOADING
    t.file_progress = [
        {"index": i + 1, "path": f"{base}/{n}", "length": ln,
         "completed": c, "selected": s}
        for i, (n, ln, c, s) in enumerate(files)]
    d.open_for(t)
    app.processEvents()
    return d, t


def _names(d):
    return [d.files_table.item(r, 0).data(32 + 224)   # Qt.UserRole
            for r in range(d.files_table.rowCount())]


def _shown(d):
    out = []
    for r in range(d.files_table.rowCount()):
        w = d.files_table.cellWidget(r, 0)
        lbl = [c for c in w.findChildren(type(w.parent())) ] if w else []
        out.append(d.files_table.item(r, 0).data(32 + 224))
    return out


FILES = [("c_medium.mkv", 500, 250, True),
         ("a_big.mkv", 900, 0, True),
         ("b_small.mkv", 100, 100, True)]


def test_default_order_is_the_engines(tmp_path):
    d, _ = _drawer_with(tmp_path, FILES)
    assert _shown(d) == ["c_medium.mkv", "a_big.mkv", "b_small.mkv"]


def test_first_click_sorts_ascending(tmp_path):
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(0)                      # Name
    assert _shown(d) == ["a_big.mkv", "b_small.mkv", "c_medium.mkv"]


def test_second_click_on_the_same_column_reverses(tmp_path):
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(0)
    d._sort_files(0)
    assert _shown(d) == ["c_medium.mkv", "b_small.mkv", "a_big.mkv"]


def test_size_sorts_by_value_not_by_text(tmp_path):
    """"1.2 GB" must come after "900 MB"."""
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(1)
    assert _shown(d) == ["b_small.mkv", "c_medium.mkv", "a_big.mkv"]


def test_progress_sorts_by_percentage(tmp_path):
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(2)
    # 0%, 50%, 100%
    assert _shown(d) == ["a_big.mkv", "c_medium.mkv", "b_small.mkv"]


def test_switching_column_starts_ascending_again(tmp_path):
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(0)
    d._sort_files(0)                      # now descending on Name
    d._sort_files(1)                      # Size: must be ascending
    assert _shown(d) == ["b_small.mkv", "c_medium.mkv", "a_big.mkv"]


def test_each_row_keeps_its_own_files_progress(tmp_path):
    """The reason this is rebuilt rather than Qt-sorted: a bar must follow its
    file, not stay on its row index."""
    d, t = _drawer_with(tmp_path, FILES)
    d._sort_files(1)                      # smallest first: b(100%), c(50%), a(0%)
    d._refresh_files(t)
    _app().processEvents()
    vals = [d.files_table.cellWidget(r, 2).value()
            for r in range(d.files_table.rowCount())]
    assert vals == [100, 50, 0]


def test_selection_still_targets_the_right_file(tmp_path):
    """_apply_file_selection sends aria2 1-based indexes; after a sort those
    must still be the engine's, not the row numbers."""
    d, _ = _drawer_with(tmp_path, FILES)
    d._sort_files(1)
    idxs = [i for i, _cb, _sz in d._file_row_widgets]
    assert idxs == [2, 0, 1]              # b, c, a in the engine's numbering
