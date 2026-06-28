"""Shared dialog building blocks.

`DialogHeader` is the consistent brand-mark + title row used at the top of the
v2 dialogs, so they all look the same and the pattern lives in one place.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from gui2.brand import BrandLogo
from gui2 import palette


class DialogHeader(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(palette.SPACE_LG)
        h.addWidget(BrandLogo(20))
        lbl = QLabel(title); lbl.setObjectName("dlgTitle")
        h.addWidget(lbl)
        h.addStretch()
