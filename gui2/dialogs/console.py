"""In-app developer console — a live tail of hyperfetch.log.

Reads the log incrementally (by file offset) on a timer so it follows new
output, with Copy / Clear / Open-Folder and a Verbose toggle that flips
debug-level logging on for the session (no settings change).
"""
import os
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QCheckBox, QApplication
)
from PySide6.QtGui import QTextCursor

import utils
from gui2.palette import COLORS
from gui2.brand import BrandLogo
from gui.icons import themed_icon


class ConsoleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Developer Console")
        self.setMinimumSize(740, 480)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._path = os.path.join(utils.app_data_dir(), "hyperfetch.log")
        self._pos = 0

        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 18); v.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(BrandLogo(20))
        title = QLabel("Developer Console"); title.setObjectName("dlgTitle")
        head.addWidget(title); head.addStretch()
        self.verbose = QCheckBox("Verbose (debug)")
        self.verbose.setChecked(logging.getLogger("hyperfetch").level == logging.DEBUG)
        self.verbose.setToolTip("Record verbose DEBUG output for this session")
        self.verbose.toggled.connect(lambda on: utils.setup_logging(on))
        self.autoscroll = QCheckBox("Auto-scroll"); self.autoscroll.setChecked(True)
        head.addWidget(self.verbose); head.addWidget(self.autoscroll)
        v.addLayout(head)

        self.view = QPlainTextEdit(); self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)        # cap memory on huge logs
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['bg']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 8px;"
            " font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; padding: 8px; }}")
        v.addWidget(self.view, 1)

        foot = QHBoxLayout()
        clear = QPushButton("  Clear"); clear.setIcon(themed_icon("trash", "muted")); clear.clicked.connect(self._clear)
        openf = QPushButton("  Open Folder"); openf.setIcon(themed_icon("open", "text")); openf.clicked.connect(self._open_folder)
        copy = QPushButton("  Copy"); copy.setIcon(themed_icon("clipboard", "text")); copy.clicked.connect(self._copy)
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        foot.addWidget(clear); foot.addWidget(openf); foot.addStretch(); foot.addWidget(copy); foot.addWidget(close)
        v.addLayout(foot)

        self._load_all()
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self); self._timer.timeout.connect(self._tail); self._timer.start(700)

    # ---- tailing ----
    def _load_all(self):
        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                self.view.setPlainText(f.read())
                self._pos = f.tell()
        except OSError:
            self.view.setPlainText("(no log yet — turn on Verbose, then run a download to see live output)")
            self._pos = 0
        self._scroll()

    def _tail(self):
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return
        if size < self._pos:                 # truncated / rotated → reload
            self._load_all(); return
        if size == self._pos:
            return
        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos); new = f.read(); self._pos = f.tell()
        except OSError:
            return
        if new:
            self.view.moveCursor(QTextCursor.End)
            self.view.insertPlainText(new)
            self._scroll()

    def _scroll(self):
        if self.autoscroll.isChecked():
            sb = self.view.verticalScrollBar(); sb.setValue(sb.maximum())

    # ---- actions ----
    def _copy(self):
        QApplication.clipboard().setText(self.view.toPlainText())

    def _clear(self):
        try:
            open(self._path, "w").close()
        except OSError:
            pass
        self._pos = 0
        self.view.clear()

    def _open_folder(self):
        try:
            os.startfile(utils.app_data_dir())   # noqa: S606 (Windows)
        except OSError:
            pass
