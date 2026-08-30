"""Developer Console: copy the line you selected, not the whole log.

Reported: "I should be able to copy or select a particular line from the
developer console." Copy always sent the entire file to the clipboard, and
auto-scroll yanked the view to the bottom every 700ms, so holding a selection
long enough to press the button was a race.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402
from PySide6.QtGui import QTextCursor                    # noqa: E402

import utils                                             # noqa: E402
from gui2.dialogs.console import ConsoleDialog           # noqa: E402

LOG = "line one\nline two\nline three\nline four\n"


@pytest.fixture
def console(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "hyperfetch.log").write_text(LOG, encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    dlg = ConsoleDialog()
    app.processEvents()
    return dlg, app


def _select_second_line(dlg):
    cur = dlg.view.textCursor()
    cur.movePosition(QTextCursor.Start)
    cur.movePosition(QTextCursor.Down)
    cur.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
    dlg.view.setTextCursor(cur)
    return cur


def test_copies_only_the_selected_line(console):
    dlg, app = console
    _select_second_line(dlg)
    dlg._copy()
    app.processEvents()
    got = QApplication.clipboard().text()
    assert got == "line two", f"copied {got!r} instead of just the selected line"


def test_copies_everything_when_nothing_is_selected(console):
    dlg, app = console
    dlg._copy()
    app.processEvents()
    got = QApplication.clipboard().text()
    assert "line one" in got and "line four" in got, \
        "with no selection Copy should still hand over the whole log"


def test_a_multi_line_selection_keeps_real_newlines(console):
    """Qt hands back U+2029 for line breaks; pasted raw that is one long line."""
    dlg, app = console
    cur = dlg.view.textCursor()
    cur.movePosition(QTextCursor.Start)
    cur.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor)
    cur.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
    dlg.view.setTextCursor(cur)
    dlg._copy()
    app.processEvents()
    got = QApplication.clipboard().text()
    assert " " not in got, "paragraph separators reached the clipboard"
    assert got.splitlines() == ["line one", "line two"], got


def test_autoscroll_does_not_fight_a_selection(console):
    """Scrolling to the bottom under the user's cursor is what made selecting
    a line while the log was live impractical."""
    dlg, app = console
    assert dlg.autoscroll.isChecked()
    _select_second_line(dlg)
    before = dlg.view.verticalScrollBar().value()
    dlg._scroll()
    assert dlg.view.verticalScrollBar().value() == before, \
        "auto-scroll moved the view while a selection was being held"


def test_autoscroll_still_follows_when_nothing_is_selected(console):
    dlg, app = console
    sb = dlg.view.verticalScrollBar()
    sb.setValue(0)
    dlg._scroll()
    assert sb.value() == sb.maximum(), "auto-scroll stopped following the log"
