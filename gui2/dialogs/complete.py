"""Download Complete popup (v2, mockup #4) — celebratory card with file stats
and Open File / Open Folder / View in List / Close.
"""
import os
import time
import math
import random

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGraphicsDropShadowEffect, QWidget, QCheckBox
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
        # Bounded on BOTH axes: this pops up unprompted, so it must never grow to
        # fit a long release name or a wall of stats and land as a huge window
        # over whatever the user was doing.
        self.setFixedWidth(380)
        self.setMaximumHeight(430)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.t = t

        v = QVBoxLayout(self); v.setContentsMargins(20, 18, 20, 16); v.setSpacing(9)
        v.setAlignment(Qt.AlignHCenter)

        # Celebratory Checkmark
        check = QLabel()
        check.setAlignment(Qt.AlignCenter)
        check.setFixedSize(56, 56)
        check.setPixmap(themed_icon("check", "white").pixmap(30, 30))

        # Glowing gradient background for the check
        check.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {COLORS['success']}, stop:1 #059669);"
            f"border-radius: 28px;"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setColor(QColor(COLORS['success']))
        shadow.setOffset(0, 3)
        check.setGraphicsEffect(shadow)

        v.addWidget(check, 0, Qt.AlignHCenter)

        # One line, not two: the subtitle only restated the title.
        title = QLabel("Download complete"); title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size:17px; font-weight:800; color: {COLORS['text']}; background:transparent;")
        v.addWidget(title)

        # Glassmorphism Stats Panel
        panel = QFrame(); panel.setObjectName("panel")
        panel.setStyleSheet(f"QFrame#panel {{ background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; }}")
        pg = QVBoxLayout(panel); pg.setContentsMargins(14, 11, 14, 11); pg.setSpacing(8)

        top = QHBoxLayout()
        ic = QLabel(); ic.setStyleSheet("background:transparent;")
        ic_name = "magnet" if _torrent.is_torrent_task(t.url, t.filename) else "document"
        ic.setPixmap(themed_icon(ic_name, COLORS['accent']).pixmap(22, 22))

        nm = QVBoxLayout(); nm.setSpacing(1)
        from gui2.download_card import ElideLabel
        # elide, never wrap: a long release name used to reflow into three lines
        # and push the whole popup taller
        name = ElideLabel(t.filename or "download")
        name.setToolTip(t.filename or "")
        name.setStyleSheet(f"font-size: 13px; font-weight:700; color:{COLORS['text']}; background:transparent;")
        size = QLabel(human_size(t.total_size or t.downloaded)); size.setStyleSheet(f"color:{COLORS['accent']}; font-weight: 600; font-size:11px; background:transparent;")
        nm.addWidget(name); nm.addWidget(size)
        top.addWidget(ic); top.addSpacing(8); top.addLayout(nm, 1)
        pg.addLayout(top)

        # Stats
        elapsed = max(1e-9, time.time() - getattr(t, "added", 0)) if getattr(t, "added", 0) else 0
        avg = (t.downloaded / elapsed) if elapsed else 0
        
        stats_frame = QFrame()
        stats_frame.setStyleSheet("background: transparent;")
        s_lay = QVBoxLayout(stats_frame); s_lay.setContentsMargins(0, 0, 0, 0); s_lay.setSpacing(5)

        # "Downloaded" repeated the size already shown beside the filename, and
        # Connections is meaningless for a torrent (it has no HTTP segments), so
        # each line here now says something the others do not.
        stats = [("Time", fmt_eta(elapsed) if elapsed else "—"),
                 ("Average speed", human_speed(avg) if avg else "—")]
        if not _torrent.is_torrent_task(t.url, t.filename) and t.segments:
            stats.append(("Connections", str(len(t.segments))))
        for label, value in stats:
            r = QHBoxLayout()
            l = QLabel(label); l.setStyleSheet(f"color:{COLORS['muted']}; font-size: 12px; background:transparent;")
            x = QLabel(value); x.setStyleSheet(f"color:{COLORS['text']}; font-weight:600; font-size: 12px; background:transparent;")
            r.addWidget(l); r.addStretch(); r.addWidget(x)
            s_lay.addLayout(r)

        pg.addWidget(stats_frame)
        v.addWidget(panel)

        # Buttons
        # Three buttons, not four. At this width the old row overflowed into
        # "Open F / Folde / iew in Lis", and "View in List" was the one worth
        # losing: closing the popup already reveals the list behind it.
        row = QHBoxLayout(); row.setSpacing(8)
        _flat = (f"QPushButton {{ padding: 7px 9px; font-weight: 600; background: {COLORS['surface2']};"
                 f" border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
                 f"QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        of = QPushButton("Open"); of.setIcon(themed_icon("open", "text")); of.clicked.connect(self._open_file)
        of.setStyleSheet(_flat)

        ofd = QPushButton("Folder"); ofd.setIcon(themed_icon("folder", "text")); ofd.clicked.connect(self._open_folder)
        ofd.setStyleSheet(_flat)

        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        close.setStyleSheet(f"QPushButton {{ padding: 7px 16px; font-weight: 700; background: {COLORS['accent']}; color: white; border: none; border-radius: 8px; }}"
                            f"QPushButton:hover {{ background: {COLORS['accent']}dd; }}")

        row.addWidget(of)
        row.addWidget(ofd)
        row.addStretch()
        row.addWidget(close)
        v.addLayout(row)

        self.skip_next = QCheckBox("Don't show this again")
        self.skip_next.setToolTip(
            "Finished downloads still appear in the list and as a toast.\n"
            "Turn back on in Settings → Downloads.")
        self.skip_next.setStyleSheet(
            f"color:{COLORS['muted']};font-size:11px;background:transparent;")
        v.addWidget(self.skip_next, 0, Qt.AlignLeft)

        # Confetti Overlay
        self.confetti = ConfettiWidget(self)
        self.confetti.resize(self.width(), self.maximumHeight())
        
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
