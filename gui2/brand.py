"""The HyperFetch logo — the bundled gradient-bolt icon (assets/icon.png), used
in the main sidebar, the settings header, and the window/taskbar icon so they
all match.
"""
import os

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIcon

from gui.theme import resource_path

_PNG = resource_path("assets", "icon.png")
_ICO = resource_path("assets", "icon.ico")


def brand_pixmap(size=64):
    pm = QPixmap(_PNG)
    if pm.isNull():
        return QPixmap(size, size)
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def brand_icon():
    return QIcon(_ICO if os.path.exists(_ICO) else _PNG)


class BrandLogo(QLabel):
    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent;")
        self.setPixmap(brand_pixmap(size))
