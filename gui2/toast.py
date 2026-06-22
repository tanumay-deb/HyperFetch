"""Lightweight notification toasts (mockup #15) stacked bottom-right.
ToastManager.show(kind, title, msg) — kind in success/error/info.
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, QTimer

from gui2.palette import COLORS
from gui.icons import themed_icon

_KIND = {
    "success": ("check", COLORS["success"]),
    "error":   ("alert", COLORS["error"]),
    "info":    ("info", COLORS["info"]),
}


class Toast(QFrame):
    def __init__(self, parent, kind, title, msg, on_close):
        super().__init__(parent)
        self.setFixedWidth(330)
        icon_name, color = _KIND.get(kind, _KIND["info"])
        self.setStyleSheet(
            f"QFrame{{background:{COLORS['surface2']};border:1px solid {COLORS['border']};"
            f"border-left:3px solid {color};border-radius:10px;}}")
        h = QHBoxLayout(self); h.setContentsMargins(12, 10, 10, 10); h.setSpacing(10)
        ic = QLabel(); ic.setFixedSize(22, 22); ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(themed_icon(icon_name, color).pixmap(15, 15))
        ic.setStyleSheet(f"background:{color}33;border-radius:11px;")
        col = QVBoxLayout(); col.setSpacing(1)
        t = QLabel(title); t.setStyleSheet("font-weight:700;background:transparent;")
        m = QLabel(msg); m.setWordWrap(True)
        m.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
        col.addWidget(t); col.addWidget(m)
        x = QPushButton(); x.setIcon(themed_icon("close", "muted")); x.setObjectName("iconbtn"); x.setFixedSize(22, 22)
        x.setCursor(Qt.PointingHandCursor); x.clicked.connect(on_close)
        h.addWidget(ic); h.addLayout(col, 1); h.addWidget(x, 0, Qt.AlignTop)


class ToastManager:
    def __init__(self, parent):
        self.parent = parent
        self.items = []

    def show(self, kind, title, msg, secs=5):
        t = Toast(self.parent, kind, title, msg, lambda: self._close(t))
        self.items.append(t)
        t.show()
        self.reposition()
        QTimer.singleShot(secs * 1000, lambda: self._close(t))

    def _close(self, t):
        if t in self.items:
            self.items.remove(t)
            t.deleteLater()
            self.reposition()

    def reposition(self):
        m = 16
        y = self.parent.height() - m
        for t in reversed(self.items):
            t.adjustSize()
            y -= t.height()
            t.move(self.parent.width() - t.width() - m, y)
            y -= 10
            t.raise_()
