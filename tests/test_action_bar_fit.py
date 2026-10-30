"""The bulk bar beside an open drawer: icons only when the words do not fit
(Remove and Clear went under the drawer, asked 2026-10-30)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication   # noqa: E402

from gui2.action_bar import ActionBar         # noqa: E402


def test_compact_keeps_every_action_in_a_tooltip_and_is_narrower():
    QApplication.instance() or QApplication([])
    bar = ActionBar()
    bar.set_count(3)
    bar.set_applicable(pause=True, resume=True, force=True, folder=True, move=True)
    wide = bar.sizeHint().width()
    bar.set_compact(True)
    assert bar.sizeHint().width() < wide * 0.7
    assert bar.btn_remove.text() == "" and bar.btn_remove.toolTip() == "Remove"
    assert bar.count.text() == "3"
    bar.set_count(5)
    assert bar.count.text() == "5"
    bar.set_compact(False)
    assert bar.btn_remove.text().strip() == "Remove" and bar.count.text() == "5 selected"
    assert bar.sizeHint().width() == wide
