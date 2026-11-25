"""The animated on/off switch Settings uses (AnimatedToggle).

Colours come in via ``from gui.theme import *`` so ``apply_theme`` can rebind
them on a runtime theme switch (see ``gui.theme._THEME_CONSUMERS``).
"""
from PySide6.QtWidgets import QCheckBox
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRect
from PySide6.QtGui import QColor, QPainter

from gui.theme import *           # noqa: F401,F403 — colours rebind via apply_theme


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
