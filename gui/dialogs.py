"""Shared dialog widgets used by the v2 GUI.

Only two pieces survive here: the read-only :class:`PropertiesDialog` (shown for
an existing download) and the :class:`AnimatedToggle` switch used in Settings.
Colours come in via ``from gui.theme import *`` so ``apply_theme`` can rebind
them on a runtime theme switch (see ``gui.theme._THEME_CONSUMERS``).
"""
import os
import subprocess

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QApplication, QCheckBox
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRect
from PySide6.QtGui import QColor, QPainter

import task as T
import torrent
from gui.theme import *           # noqa: F401,F403 — colours rebind via apply_theme
from gui.theme import _muted_label


class PropertiesDialog(QDialog):
    """Read-only properties of an existing download (double-click a row)."""

    def __init__(self, parent, t: "T.DownloadTask"):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {t.filename}")
        self.setMinimumWidth(560)
        self.t = t

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(22, 20, 22, 18)

        title = QLabel(t.filename)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        title.setWordWrap(True)
        lay.addWidget(title)

        color = STATUS_COLORS.get(t.status, TEXT)
        pill = QLabel(t.status)
        c = QColor(color)
        pill.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},0.16);"
            f"color: {color}; border-radius: 10px; padding: 3px 12px;"
            "font-weight: 700; font-size: 11px;")
        pill.setFixedHeight(22)
        prow = QHBoxLayout()
        prow.addWidget(pill)
        prow.addStretch()
        lay.addLayout(prow)

        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(18)
        rows = [
            ("URL", t.url),
            ("Save path", t.save_path),
            ("Size", f"{human_size(t.downloaded)} of {human_size(t.total_size)}"
                     f"  ({t.percent}%)"),
        ]
        if t.error:
            rows.append(("Error", t.error))
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_muted_label(k), i, 0, Qt.AlignTop)
            if k == "URL":
                val = QLineEdit(v)
                val.setReadOnly(True)
                val.setCursorPosition(0)
                val.setStyleSheet("background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 4px; color: " + TEXT)
                grid.addWidget(val, i, 1)
            else:
                val = QLabel(v)
                val.setWordWrap(True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setStyleSheet("background: transparent;")
                grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        brow = QHBoxLayout()
        btn_file = QPushButton("Open File")
        btn_folder = QPushButton("Open Folder")
        btn_copy = QPushButton("Copy URL")
        btn_close = QPushButton("Close")
        btn_close.setObjectName("primary")
        self._open_target = self._resolve_open_target(t)
        btn_file.setEnabled(t.status == T.COMPLETED and bool(self._open_target))
        btn_file.clicked.connect(self._open_file)
        btn_folder.clicked.connect(self._open_folder)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(t.url))
        btn_close.clicked.connect(self.accept)
        brow.addWidget(btn_file)
        brow.addWidget(btn_folder)
        brow.addWidget(btn_copy)
        brow.addStretch()
        brow.addWidget(btn_close)
        lay.addLayout(brow)

    def _resolve_open_target(self, t):
        """Path that 'Open File' should launch. The real file/dir if it exists;
        for a torrent (save_path is a placeholder, and the payload is a folder)
        fall back to the destination folder so it still opens something useful."""
        if os.path.exists(t.save_path):
            return t.save_path
        folder = os.path.dirname(t.save_path) or "."
        if torrent.is_torrent_task(t.url, t.filename) and os.path.isdir(folder):
            return folder
        return ""

    def _open_file(self):
        target = getattr(self, "_open_target", "") or self.t.save_path
        try:
            os.startfile(target)
        except OSError:
            pass

    def _open_folder(self):
        folder = os.path.dirname(self.t.save_path) or "."
        if os.path.exists(self.t.save_path):
            subprocess.Popen(["explorer", "/select,", self.t.save_path])
        else:
            try:
                os.startfile(folder)
            except OSError:
                pass


class AnimatedToggle(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._thumb_pos = 2

        self.anim = QPropertyAnimation(self, b"thumb_pos", self)
        self.anim.setEasingCurve(QEasingCurve.InOutCirc)
        self.anim.setDuration(200)

        self.stateChanged.connect(self._on_state_change)

    def get_thumb_pos(self):
        return self._thumb_pos

    def set_thumb_pos(self, pos):
        self._thumb_pos = pos
        self.update()

    thumb_pos = Property(float, get_thumb_pos, set_thumb_pos)

    def _on_state_change(self, state):
        self.anim.stop()
        if state:
            self.anim.setEndValue(self.width() - 20)
        else:
            self.anim.setEndValue(2)
        self.anim.start()

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        track_rect = QRect(0, 0, self.width(), self.height())
        if self.isChecked():
            p.setBrush(QColor(ACCENT))
            p.setPen(Qt.NoPen)
        else:
            p.setBrush(QColor(SURFACE))
            p.setPen(QColor(BORDER))
        p.drawRoundedRect(track_rect, 11, 11)

        p.setBrush(QColor("white"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(self._thumb_pos, 2, 18, 18)
        p.end()
