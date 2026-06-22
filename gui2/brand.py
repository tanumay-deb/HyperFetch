"""One canonical HyperFetch logo — a purple disc with a white bolt — used in the
main sidebar, the settings header, and the window/taskbar icon, so they match.
"""
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPixmap, QIcon

from gui.icons import themed_icon
from gui2.palette import COLORS


def brand_pixmap(size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(COLORS["accent"]))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    g = int(size * 0.58)
    p.drawPixmap((size - g) // 2, (size - g) // 2, themed_icon("bolt", "white").pixmap(g, g))
    p.end()
    return pm


def brand_icon():
    return QIcon(brand_pixmap(64))


class BrandLogo(QLabel):
    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self._sz = size
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(COLORS["accent"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect())
        g = int(self._sz * 0.58)
        p.drawPixmap((self._sz - g) // 2, (self._sz - g) // 2,
                     themed_icon("bolt", "white").pixmap(g, g))
        p.end()
