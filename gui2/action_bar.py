"""ActionBar — a contextual toolbar that slides in above the list whenever one
or more downloads are selected, so the common bulk actions (pause/resume/force/
move/remove) are one click away instead of buried in the right-click menu.

Dumb view: it only emits signals + renders the count. The main window wires the
signals to the queue and owns the move-to-queue menu (needs the live queue list).
"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from gui.icons import themed_icon
from gui2.palette import COLORS


class ActionBar(QFrame):
    openClicked = Signal()
    pauseClicked = Signal()
    resumeClicked = Signal()
    forceClicked = Signal()
    moveClicked = Signal()
    removeClicked = Signal()
    clearClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("actionBar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"#actionBar {{ background: {COLORS['surface2']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 10px; }}")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28); shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 130))
        self.setGraphicsEffect(shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 10, 7)
        lay.setSpacing(8)

        self.count = QLabel("0 selected")
        self.count.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; background: transparent;")
        lay.addWidget(self.count)
        lay.addSpacing(4)

        self.btn_open = self._btn("open", "Open", self.openClicked, lay)
        self.btn_pause = self._btn("pause", "Pause", self.pauseClicked, lay)
        self.btn_resume = self._btn("play", "Resume", self.resumeClicked, lay)
        self.btn_force = self._btn("force", "Force", self.forceClicked, lay)
        self.move_btn = self._btn("arrow-down", "Move to", self.moveClicked, lay)
        lay.addStretch()
        self.btn_remove = self._btn("trash", "Remove", self.removeClicked, lay, danger=True)

        x = QPushButton("Clear"); x.setObjectName("pill"); x.setCursor(Qt.PointingHandCursor)
        x.clicked.connect(self.clearClicked)
        lay.addWidget(x)

    def _btn(self, icon, label, signal, lay, danger=False):
        b = QPushButton("  " + label)
        b.setCursor(Qt.PointingHandCursor)
        b.setIcon(themed_icon(icon, "white" if danger else "text"))
        col = COLORS['error'] if danger else COLORS['surface']
        b.setStyleSheet(
            f"QPushButton {{ background: {col}; color: {'white' if danger else COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 5px 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['card_hover'] if not danger else COLORS['error']}; }}")
        b.clicked.connect(signal)
        lay.addWidget(b)
        return b

    def set_count(self, n):
        self.count.setText(f"{n} selected")

    def set_applicable(self, *, open_=False, pause=False, resume=False, force=False, move=False):
        """Show only the actions that make sense for the current selection's
        status(es) — e.g. a completed file gets Open (+ Remove), a downloading
        one gets Pause, a paused/failed one gets Resume/Force. Remove is always on."""
        self.btn_open.setVisible(open_)
        self.btn_pause.setVisible(pause)
        self.btn_resume.setVisible(resume)
        self.btn_force.setVisible(force)
        self.move_btn.setVisible(move)
