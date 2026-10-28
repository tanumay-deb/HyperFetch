"""Keep only some of a torrent's files without ticking them one by one.

Asked 2026-10-28: a torrent of 100 files, keep the .jpg, not the .mp4 -
there was a search box and Select All / Select None, but those two ignored
the search, and each tick was its own change sent to aria2. Now, with a
search, they act on the files it shows; "Only these" keeps those and drops
the rest; and a bulk change reaches aria2 as one.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import task as T
from gui2.details_drawer import DetailsDrawer

FILES = ["a.jpg", "b.mp4", "c.jpg", "d.mp4", "e.JPG", "f.txt"]


def _drawer(tmp_path):
    app = QApplication.instance() or QApplication([])
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Pics", str(tmp_path / "Pics" / "payload"))
    t.status = T.PAUSED
    base = str(tmp_path / "Pics")
    t.file_progress = [{"index": i + 1, "path": f"{base}/{n}", "length": 10,
                        "completed": 0, "selected": True} for i, n in enumerate(FILES)]
    sent = []
    host = QWidget()
    host.queue = types.SimpleNamespace(tasks=[t],
                                       change_torrent_files=lambda tid, sel: sent.append(sel))
    d = DetailsDrawer(host)
    d._host_ref = host                     # or the host is collected, and the drawer with it
    d.open_for(t)
    app.processEvents()
    return d, sent


def _ticked(d):
    return [FILES[idx] for idx, cb, _ in d._file_row_widgets if cb.isChecked()]


def test_only_the_files_a_search_shows(tmp_path):
    d, sent = _drawer(tmp_path)
    assert d.btn_only.isHidden()
    d.file_search.setText(".jpg")
    assert not d.btn_only.isHidden() and d.btn_select_all.text().strip() == "Select these"
    d.btn_only.click()
    assert _ticked(d) == ["a.jpg", "c.jpg", "e.JPG"]
    assert sent == ["1,3,5"], "aria2 was told %d times" % len(sent)


def test_unselect_these_leaves_the_others(tmp_path):
    d, sent = _drawer(tmp_path)
    d.file_search.setText("mp4")
    d.btn_select_none.click()
    assert _ticked(d) == ["a.jpg", "c.jpg", "e.JPG", "f.txt"]
    assert sent == ["1,3,5,6"]


def test_without_a_search_the_buttons_mean_everything(tmp_path):
    d, sent = _drawer(tmp_path)
    d.file_search.setText("")
    assert d.btn_select_all.text().strip() == "Select All" and d.btn_only.isHidden()
    d.btn_select_none.click()
    # aria2 reads an empty selection as "all": one file stays ticked
    assert len(_ticked(d)) == 1
    d.btn_select_all.click()
    assert _ticked(d) == FILES
