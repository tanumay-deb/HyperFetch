"""Download Complete popup (v2, mockup #4) — celebratory card with file stats
and Open File / Open Folder / View in List / Close.
"""
import os
import time
import math
import random

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QGraphicsDropShadowEffect, QWidget
)
from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen

import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta
from gui.icons import themed_icon
from gui2.palette import COLORS


class ConfettiWidget(QWidget):
    """A lightweight overlay that bursts confetti once."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.particles = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        
    def burst(self):
        self.particles = []
        colors = ["#B388FF", "#82B1FF", "#FF80AB", "#00E676", "#FFD54F"]
        cx = self.width() / 2
        cy = 80
        for _ in range(50):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(2, 10)
            self.particles.append({
                'x': cx, 'y': cy,
                'vx': math.cos(angle) * speed,
                'vy': math.sin(angle) * speed - 5,
                'color': random.choice(colors),
                'size': random.uniform(4, 8),
                'life': 1.0,
                'rot': random.uniform(0, 360),
                'vrot': random.uniform(-10, 10)
            })
        self.timer.start(16)
        
    def _step(self):
        alive = False
        for p in self.particles:
            p['x'] += p['vx']
            p['y'] += p['vy']
            p['vy'] += 0.4 # gravity
            p['rot'] += p['vrot']
            p['life'] -= 0.015
            if p['life'] > 0: alive = True
            
        self.update()
        if not alive:
            self.timer.stop()
            self.particles = []

    def paintEvent(self, event):
        if not self.particles: return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for p in self.particles:
            if p['life'] <= 0: continue
            painter.save()
            painter.translate(p['x'], p['y'])
            painter.rotate(p['rot'])
            color = QColor(p['color'])
            color.setAlphaF(p['life'])
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRect(int(-p['size']/2), int(-p['size']/2), int(p['size']), int(p['size']))
            painter.restore()


class CompleteDialog(QDialog):
    viewInList = Signal(str)

    def __init__(self, parent, t):
        super().__init__(parent)
        self.setWindowTitle("Download Complete")
        self.setMinimumWidth(440)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.t = t

        v = QVBoxLayout(self); v.setContentsMargins(32, 32, 32, 28); v.setSpacing(16)
        v.setAlignment(Qt.AlignHCenter)

        # Celebratory Checkmark
        check = QLabel()
        check.setAlignment(Qt.AlignCenter)
        check.setFixedSize(84, 84)
        check.setPixmap(themed_icon("check", "white").pixmap(44, 44))
        
        # Glowing gradient background for the check
        check.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {COLORS['success']}, stop:1 #059669);"
            f"border-radius: 42px;"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(COLORS['success']))
        shadow.setOffset(0, 4)
        check.setGraphicsEffect(shadow)
        
        v.addWidget(check, 0, Qt.AlignHCenter)

        # Typography
        title = QLabel("Download Completed!"); title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size:22px; font-weight:800; color: {COLORS['text']}; background:transparent;")
        sub = QLabel("The file has been downloaded successfully."); sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{COLORS['muted']}; font-size: 13px; background:transparent;")
        v.addWidget(title); v.addWidget(sub)
        v.addSpacing(4)

        # Glassmorphism Stats Panel
        panel = QFrame(); panel.setObjectName("panel")
        panel.setStyleSheet(f"QFrame#panel {{ background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; }}")
        pg = QVBoxLayout(panel); pg.setContentsMargins(20, 16, 20, 16); pg.setSpacing(12)
        
        top = QHBoxLayout()
        ic = QLabel(); ic.setStyleSheet("background:transparent;")
        ic_name = "magnet" if _torrent.is_torrent_task(t.url, t.filename) else "document"
        ic.setPixmap(themed_icon(ic_name, COLORS['accent']).pixmap(28, 28))
        
        nm = QVBoxLayout(); nm.setSpacing(2)
        name = QLabel(t.filename or "download"); name.setStyleSheet(f"font-size: 14px; font-weight:700; color:{COLORS['text']}; background:transparent;")
        name.setWordWrap(True)
        size = QLabel(human_size(t.total_size or t.downloaded)); size.setStyleSheet(f"color:{COLORS['accent']}; font-weight: 600; font-size:12px; background:transparent;")
        nm.addWidget(name); nm.addWidget(size)
        top.addWidget(ic); top.addSpacing(8); top.addLayout(nm, 1)
        pg.addLayout(top)

        # Stats
        elapsed = max(1e-9, time.time() - getattr(t, "added", 0)) if getattr(t, "added", 0) else 0
        avg = (t.downloaded / elapsed) if elapsed else 0
        
        stats_frame = QFrame()
        stats_frame.setStyleSheet("background: transparent;")
        s_lay = QVBoxLayout(stats_frame); s_lay.setContentsMargins(0, 0, 0, 0); s_lay.setSpacing(8)
        
        for label, value in (
            ("Downloaded", human_size(t.downloaded)),
            ("Total Time", fmt_eta(elapsed) if elapsed else "—"),
            ("Average Speed", human_speed(avg) if avg else "—"),
            ("Connections", str(len(t.segments) or "—")),
        ):
            r = QHBoxLayout()
            l = QLabel(label); l.setStyleSheet(f"color:{COLORS['muted']}; font-size: 13px; background:transparent;")
            x = QLabel(value); x.setStyleSheet(f"color:{COLORS['text']}; font-weight:600; font-size: 13px; background:transparent;")
            r.addWidget(l); r.addStretch(); r.addWidget(x)
            s_lay.addLayout(r)
            
        pg.addWidget(stats_frame)
        v.addWidget(panel)
        v.addSpacing(8)

        # Buttons
        row = QHBoxLayout(); row.setSpacing(10)
        of = QPushButton(" Open File"); of.setIcon(themed_icon("open", "text")); of.clicked.connect(self._open_file)
        of.setStyleSheet(f"QPushButton {{ padding: 8px 14px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
                         f"QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
                         
        ofd = QPushButton(" Folder"); ofd.setIcon(themed_icon("folder", "text")); ofd.clicked.connect(self._open_folder)
        ofd.setStyleSheet(f"QPushButton {{ padding: 8px 14px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
                          f"QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
                          
        vl = QPushButton("View in List"); vl.clicked.connect(lambda: (self.viewInList.emit(self.t.id), self.accept()))
        vl.setStyleSheet(f"QPushButton {{ padding: 8px 14px; font-weight: 600; background: transparent; color: {COLORS['muted']}; border: none; }}"
                         f"QPushButton:hover {{ color: {COLORS['text']}; }}")
                         
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        close.setStyleSheet(f"QPushButton {{ padding: 8px 24px; font-weight: 700; background: {COLORS['accent']}; color: white; border: none; border-radius: 8px; }}"
                            f"QPushButton:hover {{ background: {COLORS['accent']}dd; }}")
                            
        row.addWidget(of)
        row.addWidget(ofd)
        row.addStretch()
        row.addWidget(vl)
        row.addWidget(close)
        v.addLayout(row)
        
        # Confetti Overlay
        self.confetti = ConfettiWidget(self)
        self.confetti.resize(440, 500)
        
    def showEvent(self, event):
        super().showEvent(event)
        self.confetti.resize(self.size())
        self.confetti.burst()

    def _target(self):
        p = self.t.save_path
        if os.path.exists(p):
            return p
        folder = os.path.dirname(p) or "."
        if _torrent.is_torrent_task(self.t.url, self.t.filename) and os.path.isdir(folder):
            return folder
        return ""

    def _open_file(self):
        tgt = self._target()
        if tgt:
            try:
                os.startfile(tgt)
            except OSError:
                pass
        self.accept()

    def _open_folder(self):
        tgt = self._target()
        if tgt:
            folder = tgt if os.path.isdir(tgt) else os.path.dirname(tgt)
            try:
                os.startfile(folder)
            except OSError:
                pass
        self.accept()
