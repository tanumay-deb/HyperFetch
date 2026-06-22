"""DetailsDrawer — a slide-in panel on the right with tabs:
Overview / Files / Connections / Headers / Logs (mockup #3).

It overlays the main pane and animates in/out. The app calls update_live()
each tick while it's open so the Overview gauge, speed graph and Connections
tab stay current.
"""
import os
from collections import deque

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QRectF, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath

import task as T
import utils
import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta, humanize_age
from gui.icons import themed_icon
from gui2.palette import COLORS

WIDTH = 440


class ProgressRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct = 0
        self.setFixedSize(132, 132)

    def set_pct(self, p):
        self._pct = max(0, min(100, int(p)))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        c = COLORS
        m = 12
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        
        pen = QPen(QColor(c["border2"]), 9, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen); p.drawArc(rect, 90 * 16, -360 * 16)
        
        glow_pen = QPen(QColor(c["accent"]), 18, Qt.SolidLine, Qt.RoundCap)
        glow_color = glow_pen.color()
        glow_color.setAlpha(40)
        glow_pen.setColor(glow_color)
        p.setPen(glow_pen)
        p.drawArc(rect, 90 * 16, int(-360 * (self._pct / 100.0) * 16))

        pen.setColor(QColor(c["accent"])); p.setPen(pen)
        p.drawArc(rect, 90 * 16, int(-360 * (self._pct / 100.0) * 16))
        
        p.setPen(QColor(c["text"]))
        f = p.font(); f.setPixelSize(26); f.setBold(True); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, f"{self._pct}%")
        p.end()


class SpeedGraph(QWidget):
    def __init__(self, parent=None, history=80):
        super().__init__(parent)
        self._hist = deque([0.0] * history, maxlen=history)
        self._max = 1.0
        self.setMinimumHeight(120)

    def push(self, bps):
        self._hist.append(max(0.0, float(bps)))
        self._max = max(1.0, max(self._hist))
        self.update()

    def reset(self):
        self._hist = deque([0.0] * self._hist.maxlen, maxlen=self._hist.maxlen)
        self._max = 1.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        c = COLORS
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(c["border"]), 1))
        for i in range(1, 4):                       # faint gridlines
            y = h * i / 4
            p.drawLine(0, int(y), w, int(y))
        n = len(self._hist)
        if n >= 2:
            path = QPainterPath(); fill = QPainterPath()
            fill.moveTo(0, h)
            for i, v in enumerate(self._hist):
                x = w * i / (n - 1)
                y = h - (v / self._max) * (h - 6) - 3
                (path.moveTo(x, y) if i == 0 else path.lineTo(x, y))
                fill.lineTo(x, y)
            fill.lineTo(w, h); fill.closeSubpath()
            from PySide6.QtGui import QLinearGradient
            grad = QLinearGradient(0, 0, 0, h)
            c_top = QColor(c["accent"]); c_top.setAlpha(120)
            c_bot = QColor(c["accent"]); c_bot.setAlpha(0)
            grad.setColorAt(0, c_top); grad.setColorAt(1, c_bot)
            p.fillPath(fill, grad)
            p.setPen(QPen(QColor(c["accent"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        p.end()


def _kv(label, value):
    row = QHBoxLayout()
    l = QLabel(label); l.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
    v = QLabel(value); v.setStyleSheet(f"color: {COLORS['text']}; font-weight: 600; background: transparent;")
    v.setWordWrap(True); v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(l); row.addStretch(); row.addWidget(v)
    return row, v


class DetailsDrawer(QFrame):
    action = Signal(str, str)        # (action, task_id)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("statsCard")
        self.setStyleSheet(f"#drawer {{ background: {COLORS['surface']}; border-left: 1px solid {COLORS['border']}; }}")
        self.setObjectName("drawer")
        self.setFixedWidth(WIDTH)
        self._tid = None
        self.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        self.h_icon = QLabel(); self.h_icon.setStyleSheet("background: transparent;")
        self.h_icon.setPixmap(themed_icon("document", "text").pixmap(18, 18))
        self.h_name = QLabel(""); self.h_name.setStyleSheet("font-weight: 800; font-size: 14px; background: transparent;")
        self.h_name.setWordWrap(True)
        close = QPushButton(); close.setIcon(themed_icon("close", "muted")); close.setObjectName("iconbtn"); close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor); close.clicked.connect(self.close_drawer)
        head.addWidget(self.h_icon); head.addWidget(self.h_name, 1); head.addWidget(close)
        lay.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; top: -1px; }}
            QTabBar::tab {{
                background: transparent; color: {COLORS['muted']};
                padding: 6px 14px; margin: 0 4px 12px 0; border-radius: 12px; font-weight: 700;
            }}
            QTabBar::tab:hover {{ background: {COLORS['surface2']}; color: {COLORS['text']}; }}
            QTabBar::tab:selected {{ background: {COLORS['accent']}; color: white; }}
        """)
        self.tabs.addTab(self._overview_tab(), "Overview")
        self.tabs.addTab(self._scroll_tab("files"), "Files")
        self.tabs.addTab(self._scroll_tab("conns"), "Connections")
        self.tabs.addTab(self._scroll_tab("headers"), "Headers")
        self.tabs.addTab(self._scroll_tab("logs"), "Logs")
        lay.addWidget(self.tabs, 1)

        foot = QHBoxLayout()
        self.btn_primary = QPushButton(" Pause"); self.btn_primary.setIcon(themed_icon("pause", "text"))
        self.btn_primary.setStyleSheet(f"QPushButton {{ padding: 8px 16px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }} QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        self.btn_primary.clicked.connect(self._primary)
        self.btn_more = QPushButton(" More"); self.btn_more.setIcon(themed_icon("chevron-down", "text"))
        self.btn_more.setStyleSheet(f"QPushButton {{ padding: 8px 16px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }} QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        self.btn_more.clicked.connect(lambda: self.action.emit("more", self._tid))
        foot.addWidget(self.btn_primary); foot.addStretch(); foot.addWidget(self.btn_more)
        lay.addLayout(foot)

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

    # ---- tab builders ----
    def _overview_tab(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 16, 0, 0); v.setSpacing(16)
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self.op_effect = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(self.op_effect)
        self.op_anim = QPropertyAnimation(self.op_effect, b"opacity")
        self.op_anim.setDuration(400)
        self.op_anim.setEasingCurve(QEasingCurve.OutCubic)
        
        top = QHBoxLayout(); top.setSpacing(20)
        self.ring = ProgressRing()
        top.addWidget(self.ring)
        info = QVBoxLayout(); info.setSpacing(4)
        self.ov_size = QLabel("—"); self.ov_size.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 16px; background: transparent;")
        self.ov_speed = QLabel("—"); self.ov_speed.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600; background: transparent;")
        info.addWidget(self.ov_size); info.addWidget(self.ov_speed)
        info.addSpacing(12)
        r1, self.ov_eta = _kv("ETA", "—"); info.addLayout(r1)
        r2, self.ov_done = _kv("Downloaded", "—"); info.addLayout(r2)
        info.addStretch()
        top.addLayout(info, 1)
        v.addLayout(top)

        self.graph = SpeedGraph(); v.addWidget(self.graph)

        glass_panel = QFrame()
        glass_panel.setStyleSheet(f"QFrame {{ background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 12px; }}")
        grid = QVBoxLayout(glass_panel); grid.setContentsMargins(16, 16, 16, 16); grid.setSpacing(12)
        r, self.ov_path = _kv("Save Location", "—"); grid.addLayout(r)
        r, self.ov_created = _kv("Created", "—"); grid.addLayout(r)
        r, self.ov_conns = _kv("Connections", "—"); grid.addLayout(r)
        r, self.ov_hash = _kv("Hash (SHA-256)", "—"); grid.addLayout(r)
        v.addWidget(glass_panel)
        v.addStretch()
        return w

    def _scroll_tab(self, key):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(2, 12, 2, 2); lay.setSpacing(6)
        lay.addStretch()
        sa.setWidget(inner)
        setattr(self, f"_{key}_lay", lay)
        return sa

    def _fill(self, key, lines):
        lay = getattr(self, f"_{key}_lay")
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for text, mono in lines:
            l = QLabel(text); l.setWordWrap(True)
            style = f"color: {COLORS['text']}; background: transparent;"
            if mono:
                style += " font-family: Consolas, monospace; font-size: 11px;"
            l.setStyleSheet(style)
            lay.addWidget(l)
        if not lines:
            e = QLabel("—"); e.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
            lay.addWidget(e)
        lay.addStretch()

    # ---- open / close ----
    def open_for(self, t):
        self._tid = t.id
        self.graph.reset()
        self._populate_static(t)
        self.update_live(t, 0.0)
        self.reposition()
        self.show(); self.raise_()
        start = QPoint(self.parent().width(), 0)
        end = QPoint(self.parent().width() - WIDTH, 0)
        self.move(start)
        self.anim.stop(); self.anim.setStartValue(start); self.anim.setEndValue(end); self.anim.start()
        if hasattr(self, 'op_anim'):
            self.op_anim.stop()
            self.op_anim.setStartValue(0.0)
            self.op_anim.setEndValue(1.0)
            self.op_anim.start()

    def close_drawer(self):
        self._tid = None
        self.hide()

    def reposition(self):
        if self.parent():
            self.setFixedHeight(self.parent().height())
            if not self.isVisible():
                return
            self.move(self.parent().width() - WIDTH, 0)

    def _primary(self):
        if self._tid:
            self.action.emit(self._primary_action, self._tid)

    # ---- data ----
    def _populate_static(self, t):
        self.h_name.setText(t.filename or "download")
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        try:
            from gui2.download_card import _CAT_ICON
            cat = utils.category_for(t.filename)
            ic_name, ic_color = _CAT_ICON.get(cat, ("document", COLORS['muted']))
            if is_tor: ic_name, ic_color = "magnet", COLORS['accent']
        except Exception:
            ic_name, ic_color = "document", COLORS['muted']
        self.h_icon.setPixmap(themed_icon(ic_name, ic_color).pixmap(22, 22))
        self.ov_path.setText(t.save_path or "—")
        self.ov_created.setText(humanize_age(getattr(t, "added", 0)) or "—")

        # Files
        files = []
        sp = t.save_path
        if sp and os.path.isdir(sp):
            try:
                for name in sorted(os.listdir(sp)):
                    fp = os.path.join(sp, name)
                    sz = os.path.getsize(fp) if os.path.isfile(fp) else 0
                    files.append((f"{name}   ({human_size(sz)})", False))
            except OSError:
                pass
        else:
            files.append((f"{t.filename}   ({human_size(t.total_size)})", False))
        self._fill("files", files)

        # Headers (cookies/auth stripped)
        hdr = utils.strip_sensitive(getattr(t, "headers", {}) or {})
        self._fill("headers", [(f"{k}: {v}", True) for k, v in hdr.items()])

        # Logs (we don't keep a per-task log yet — show the essentials)
        logs = [(f"Added: {humanize_age(getattr(t,'added',0)) or '—'}", True),
                (f"Status: {t.status}", True),
                (f"URL: {t.url}", True)]
        if t.error:
            logs.append((f"Error: {t.error}", True))
        self._fill("logs", logs)

    def update_live(self, t, bps):
        if t.id != self._tid:
            return
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        pct = 100 if t.status == T.COMPLETED else t.percent
        self.ring.set_pct(pct)
        self.ov_size.setText(f"{human_size(t.downloaded)} / {human_size(t.total_size)}")
        self.ov_speed.setText(f"{human_speed(bps) or '0 b/s'} · {t.status}")
        self.ov_done.setText(human_size(t.downloaded))
        eta = fmt_eta((t.total_size - t.downloaded) / bps) if bps > 0 and t.total_size else ""
        self.ov_eta.setText(eta or "—")
        self.ov_hash.setText({"ok": "verified", "fail": "mismatch",
                              "nohash": "no checksum"}.get(getattr(t, "hash_status", ""), "—"))
        self.graph.push(bps if t.status == T.DOWNLOADING else 0.0)

        # Connections
        if is_tor:
            self.ov_conns.setText(str(getattr(t, "tor_conns", 0)))
            self._fill("conns", [
                (f"Peers connected: {getattr(t,'tor_conns',0)}", False),
                (f"Seeders: {getattr(t,'tor_seeds',0)}", False),
            ])
        else:
            live = [s for s in t.segments if not s.complete]
            self.ov_conns.setText(str(len(live)))
            lines = []
            for s in t.segments:
                total = (s.end - s.start + 1) if s.end >= s.start else 0
                pc = int(s.downloaded * 100 / total) if total else (100 if s.complete else 0)
                lines.append((f"Segment {s.index + 1}: {pc}%   ({human_size(s.downloaded)})", False))
            self._fill("conns", lines)

        # primary button
        if t.status == T.DOWNLOADING:
            self._primary_action = "pause"
            self.btn_primary.setIcon(themed_icon("pause", "text")); self.btn_primary.setText("  Pause")
        elif t.status in (T.PAUSED, T.ERROR, T.QUEUED, T.SCHEDULED):
            self._primary_action = "resume"
            self.btn_primary.setIcon(themed_icon("play", "text")); self.btn_primary.setText("  Resume")
        elif t.status == T.COMPLETED:
            self._primary_action = "open"
            self.btn_primary.setIcon(themed_icon("open", "text")); self.btn_primary.setText("  Open File")
        else:
            self._primary_action = "details"
