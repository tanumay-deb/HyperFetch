"""Lightweight notification toasts (mockup #15) stacked bottom-right.
ToastManager.show(kind, title, msg) — kind in success/error/info. Toasts slide
in from the right with a fade, and fade out on dismiss/timeout.
"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint

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
        self.setObjectName("toast")
        self.setStyleSheet(
            f"#toast {{ background:{COLORS['surface2']}; border:1px solid {COLORS['border']}; "
            f"border-left:4px solid {color}; border-radius:8px; }}"
        )
        h = QHBoxLayout(self); h.setContentsMargins(14, 12, 12, 12); h.setSpacing(12)
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

        self._closing = False
        self._eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(self._eff); self._eff.setOpacity(0.0)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setDuration(240); self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._op_anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._op_anim.setDuration(240); self._op_anim.setEasingCurve(QEasingCurve.OutCubic)

    def slide_in(self, final):
        self.move(final.x() + 40, final.y())             # start a touch to the right
        for a, s, e in ((self._pos_anim, self.pos(), final), (self._op_anim, 0.0, 1.0)):
            a.stop(); a.setStartValue(s); a.setEndValue(e); a.start()

    def slide_out(self, done):
        if self._closing:
            return
        self._closing = True
        self._pos_anim.stop(); self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(QPoint(self.x() + 40, self.y())); self._pos_anim.start()
        self._op_anim.stop(); self._op_anim.setStartValue(self._eff.opacity())
        self._op_anim.setEndValue(0.0); self._op_anim.finished.connect(done); self._op_anim.start()


class ToastManager:
    def __init__(self, parent):
        self.parent = parent
        self.items = []

    def show(self, kind, title, msg, secs=5):
        t = Toast(self.parent, kind, title, msg, lambda: self._close(t))
        self.items.append(t)
        t.show()
        self.reposition(animate_new=t)
        QTimer.singleShot(secs * 1000, lambda: self._close(t))

    def _close(self, t):
        if t in self.items and not t._closing:
            t.slide_out(lambda: self._finish(t))

    def _finish(self, t):
        if t in self.items:
            self.items.remove(t)
        t.deleteLater()
        self.reposition()

    def reposition(self, animate_new=None):
        m = 16
        y = self.parent.height() - m
        for t in reversed(self.items):
            t.adjustSize()
            y -= t.height()
            final = QPoint(self.parent.width() - t.width() - m, y)
            if t is animate_new:
                t.slide_in(final)
            else:
                t.move(final)
            y -= 10
            t.raise_()
