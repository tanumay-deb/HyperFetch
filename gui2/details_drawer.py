"""DetailsDrawer — a slide-in panel on the right with tabs:
Overview / Files / Connections / Headers / Logs.

It overlays the main pane and animates in/out. The app calls update_live()
each tick while it's open so the Overview stats, speed graph and Connections
tab stay current.

Overview layout (mockup redesign): big % + Speed/ETA/Downloaded stat row,
linear progress bar, live speed graph, quick-action row (Open Folder /
Copy Link / Delete), then collapsible General / Network / Integrity sections.
"""
import os
from collections import deque

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QTabWidget, QWidget, QScrollArea, QToolButton, QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QPoint, QSize, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath

import task as T
import utils
import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta, humanize_age
from gui.icons import themed_icon
from gui2.palette import COLORS

WIDTH = 440

# status → accent colour (matches the download card's bar colours)
_STATE_COLOR = {
    T.DOWNLOADING: COLORS["accent"], T.PAUSED: COLORS["warning"],
    T.ERROR: COLORS["error"], T.QUEUED: COLORS["muted"],
    T.SCHEDULED: COLORS["info"], T.COMPLETED: COLORS["success"],
    T.CANCELLED: COLORS["muted"],
}
# timeline events carry plain strings — str-keyed view of the same map
_STATE_COLOR_S = {str(k): v for k, v in _STATE_COLOR.items()}


class SpeedGraph(QWidget):
    def __init__(self, parent=None, history=80):
        super().__init__(parent)
        self._hist = deque([0.0] * history, maxlen=history)
        self._max = 1.0
        self.setMinimumHeight(110)

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
            from gui2.graphing import moving_avg, smooth_path
            vals = moving_avg(list(self._hist), 5)
            pts = [QPoint(int(w * i / (n - 1)), int(h - (v / self._max) * (h - 6) - 3))
                   for i, v in enumerate(vals)]
            from PySide6.QtCore import QPointF
            ptsf = [QPointF(pt) for pt in pts]
            path = smooth_path(ptsf)
            fill = QPainterPath(path)
            fill.lineTo(ptsf[-1].x(), h); fill.lineTo(ptsf[0].x(), h); fill.closeSubpath()
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


class _Section(QFrame):
    """Collapsible card: icon + title header with a chevron; clicking the
    header (anywhere) folds/unfolds the body. Mirrors the mockup's
    General / Network / Integrity groups."""

    def __init__(self, icon, title, parent=None):
        super().__init__(parent)
        self.setObjectName("dsec")
        self.setStyleSheet(
            f"#dsec {{ background: {COLORS['surface2']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 12px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(14, 11, 14, 11); v.setSpacing(10)

        self._head = QWidget(); self._head.setCursor(Qt.PointingHandCursor)
        self._head.setStyleSheet("background: transparent;")
        h = QHBoxLayout(self._head); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        ic = QLabel(); ic.setPixmap(themed_icon(icon, "muted").pixmap(15, 15))
        ic.setStyleSheet("background: transparent;")
        tl = QLabel(title)
        tl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 12px; background: transparent;")
        self._chev = QLabel(); self._chev.setStyleSheet("background: transparent;")
        h.addWidget(ic); h.addWidget(tl); h.addStretch(); h.addWidget(self._chev)
        v.addWidget(self._head)

        self.body = QWidget(); self.body.setStyleSheet("background: transparent;")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0); self.body_lay.setSpacing(9)
        v.addWidget(self.body)

        self._open = True
        self._set_chev()
        self._head.mousePressEvent = lambda _e: self.toggle()

    def _set_chev(self):
        self._chev.setPixmap(
            themed_icon("chevron-down" if self._open else "chevron-right", "muted").pixmap(14, 14))

    def toggle(self):
        self._open = not self._open
        self.body.setVisible(self._open)
        self._set_chev()

    def add_kv(self, label, value="—"):
        row, val = _kv(label, value)
        self.body_lay.addLayout(row)
        return val


class DetailsDrawer(QFrame):
    action = Signal(str, str)        # (action, task_id)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("drawer")
        self.setStyleSheet(f"#drawer {{ background: {COLORS['surface']}; border-left: 1px solid {COLORS['border']}; }}")
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
        self._pinned = False
        self.pin_btn = QPushButton("📌"); self.pin_btn.setFixedSize(28, 28)
        self.pin_btn.setCursor(Qt.PointingHandCursor)
        self.pin_btn.setToolTip("Pin: keep this download in the panel while selecting others")
        self.pin_btn.clicked.connect(self._toggle_pin)
        self._style_pin()
        close = QPushButton(); close.setIcon(themed_icon("close", "muted")); close.setObjectName("iconbtn"); close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor); close.clicked.connect(self.close_drawer)
        head.addWidget(self.h_icon); head.addWidget(self.h_name, 1)
        head.addWidget(self.pin_btn); head.addWidget(close)
        lay.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; top: -1px; }}
            QTabBar::tab {{
                background: transparent; color: {COLORS['muted']};
                padding: 6px 11px; margin: 0 3px 12px 0; border-radius: 12px; font-weight: 700;
            }}
            QTabBar::tab:hover {{ background: {COLORS['surface2']}; color: {COLORS['text']}; }}
            QTabBar::tab:selected {{ background: {COLORS['accent']}; color: white; }}
            QTabBar QToolButton {{
                background: {COLORS['surface2']}; border: 1px solid {COLORS['border']};
                border-radius: 6px; margin-bottom: 12px;
            }}
        """)
        self.tabs.addTab(self._overview_tab(), "Overview")
        self.tabs.addTab(self._scroll_tab("files"), "Files")
        self.tabs.addTab(self._scroll_tab("conns"), "Connections")
        self.tabs.addTab(self._scroll_tab("headers"), "Headers")
        self.tabs.addTab(self._logs_tab(), "Logs")
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
    def _stat_col(self, label):
        col = QVBoxLayout(); col.setSpacing(1)
        val = QLabel("—")
        val.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 13px; background: transparent;")
        val.setAlignment(Qt.AlignHCenter)
        lab = QLabel(label)
        lab.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px; background: transparent;")
        lab.setAlignment(Qt.AlignHCenter)
        col.addWidget(val); col.addWidget(lab)
        return col, val

    def _qa_btn(self, icon, label, cb):
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setIcon(themed_icon(icon, "text")); b.setIconSize(QSize(17, 17))
        b.setText(label)
        b.setCursor(Qt.PointingHandCursor)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.setStyleSheet(
            f"QToolButton {{ background: {COLORS['surface2']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 10px; padding: 8px 4px 6px; color: {COLORS['text']};"
            f" font-weight: 600; font-size: 11px; }}"
            f"QToolButton:hover {{ background: {COLORS['card_hover']}; }}")
        b.clicked.connect(cb)
        return b

    def _overview_tab(self):
        outer = QScrollArea(); outer.setWidgetResizable(True)
        outer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        w = QWidget(); w.setStyleSheet("background: transparent;")
        v = QVBoxLayout(w); v.setContentsMargins(2, 14, 6, 2); v.setSpacing(14)

        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self.op_effect = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(self.op_effect)
        self.op_anim = QPropertyAnimation(self.op_effect, b"opacity")
        self.op_anim.setDuration(400)
        self.op_anim.setEasingCurve(QEasingCurve.OutCubic)

        # ---- stat row: big % + Speed / ETA / Downloaded columns ----
        stats = QHBoxLayout(); stats.setSpacing(10)
        self.ov_pct = QLabel("0%")
        self.ov_pct.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 34px; background: transparent;")
        stats.addWidget(self.ov_pct)
        stats.addStretch()
        c1, self.ov_speed = self._stat_col("Speed");      stats.addLayout(c1)
        stats.addSpacing(8)
        c2, self.ov_eta = self._stat_col("ETA");          stats.addLayout(c2)
        stats.addSpacing(8)
        c3, self.ov_done = self._stat_col("Downloaded");  stats.addLayout(c3)
        v.addLayout(stats)

        # ---- linear progress bar + status line ----
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setRange(0, 100)
        self.bar.setFixedHeight(6)
        self._bar_color = None
        self._style_bar(COLORS["accent"])
        v.addWidget(self.bar)
        self.ov_status = QLabel("")
        self.ov_status.setStyleSheet(f"color: {COLORS['muted']}; font-weight: 700; font-size: 12px; background: transparent;")
        v.addWidget(self.ov_status)

        self.graph = SpeedGraph(); v.addWidget(self.graph)

        # ---- quick actions ----
        qa = QHBoxLayout(); qa.setSpacing(8)
        qa.addWidget(self._qa_btn("folder", "Open Folder", lambda: self._emit("folder")))
        self._copy_btn = self._qa_btn("link", "Copy Link", self._copy_link)
        qa.addWidget(self._copy_btn)
        qa.addWidget(self._qa_btn("trash", "Delete", lambda: self._emit("delete")))
        v.addLayout(qa)

        # ---- General / Network / Integrity ----
        gen = _Section("folder", "General")
        self.ov_path = gen.add_kv("Save Location")
        self._path_copy = QPushButton(); self._path_copy.setIcon(themed_icon("clipboard", "muted"))
        self._path_copy.setFixedSize(22, 22); self._path_copy.setCursor(Qt.PointingHandCursor)
        self._path_copy.setToolTip("Copy path")
        self._path_copy.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._path_copy.clicked.connect(self._copy_path)
        # append the copy button onto the Save Location row (last layout added)
        gen.body_lay.itemAt(gen.body_lay.count() - 1).layout().addWidget(self._path_copy)
        self.ov_created = gen.add_kv("Created")
        self.ov_fsize = gen.add_kv("File Size")
        v.addWidget(gen)

        net = _Section("bolt", "Network")
        self.ov_conns = net.add_kv("Connections")
        self.ov_proto = net.add_kv("Protocol")
        self.ov_range = net.add_kv("Resume Supported")
        v.addWidget(net)

        integ = _Section("check", "Integrity")
        self.ov_hash = integ.add_kv("Status")
        self.ov_digest = integ.add_kv("SHA-256")
        self.ov_digest.setCursor(Qt.IBeamCursor)
        self.ov_digest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(integ)

        v.addStretch()
        outer.setWidget(w)
        return outer

    def _scroll_tab(self, key):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(2, 12, 2, 2); lay.setSpacing(6)
        lay.addStretch()
        sa.setWidget(inner)
        setattr(self, f"_{key}_lay", lay)
        return sa

    def _logs_tab(self):
        """Logs with a Copy button — text is selectable, and Copy grabs the whole
        log (status / URL / error) in one click for pasting into a bug report."""
        self._log_lines = []
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 8, 0, 0); v.setSpacing(6)
        bar = QHBoxLayout(); bar.addStretch()
        copy = QPushButton("  Copy"); copy.setIcon(themed_icon("clipboard", "text"))
        copy.setCursor(Qt.PointingHandCursor)
        copy.setStyleSheet(
            f"QPushButton {{ padding: 4px 12px; font-weight: 600; background: {COLORS['surface2']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 7px; }}"
            f"QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        copy.clicked.connect(self._copy_logs)
        bar.addWidget(copy)
        v.addLayout(bar)
        sa = self._scroll_tab("logs")
        v.addWidget(sa, 1)
        return w

    # ---- small actions ----
    def _emit(self, action):
        if self._tid:
            self.action.emit(action, self._tid)

    def _copy_logs(self):
        QApplication.clipboard().setText("\n".join(self._log_lines))

    def _flash(self, btn, text="Copied ✓"):
        old = btn.text()
        btn.setText(text)
        QTimer.singleShot(1200, lambda: btn.setText(old))

    def _copy_link(self):
        if self._url:
            QApplication.clipboard().setText(self._url)
            self._flash(self._copy_btn)

    def _copy_path(self):
        if getattr(self, "_full_path", ""):
            QApplication.clipboard().setText(self._full_path)

    def _style_pin(self):
        on = self._pinned
        self.pin_btn.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent'] if on else 'transparent'};"
            f" border: 1px solid {COLORS['accent'] if on else 'transparent'};"
            f" border-radius: 8px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent'] if on else COLORS['surface2']}; }}")

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self._style_pin()

    def _style_bar(self, col):
        if col == self._bar_color:
            return
        self._bar_color = col
        self.bar.setStyleSheet(
            f"QProgressBar{{background:{COLORS['surface2']};border:none;border-radius:3px;max-height:6px;}}"
            f"QProgressBar::chunk{{background:{col};border-radius:3px;}}")

    def _fill(self, key, lines, empty=None):
        """Populate a list tab. `empty` = (icon, title, sub) renders a friendly
        centred empty state instead of a bare dash."""
        lay = getattr(self, f"_{key}_lay")
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not lines and empty:
            # widgets go straight into the tab layout (not a nested fixed box) so
            # the word-wrapped subtitle gets its full height and never clips
            icon, title, sub = empty
            lay.addSpacing(44)
            ic = QLabel(); ic.setAlignment(Qt.AlignCenter)
            ic.setPixmap(themed_icon(icon, COLORS['muted']).pixmap(34, 34))
            ic.setStyleSheet("background: transparent;")
            tl = QLabel(title); tl.setAlignment(Qt.AlignCenter)
            tl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: 13px; background: transparent;")
            sb = QLabel(sub); sb.setAlignment(Qt.AlignCenter); sb.setWordWrap(True)
            sb.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px; background: transparent;")
            lay.addWidget(ic)
            lay.addSpacing(6)
            lay.addWidget(tl)
            lay.addWidget(sb)
            lay.addStretch()
            return
        for text, mono in lines:
            l = QLabel(text); l.setWordWrap(True)
            # selectable so users can highlight + Ctrl+C an error / URL / header
            l.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            l.setCursor(Qt.IBeamCursor)
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
    def _load(self, t):
        """Point the drawer at a task and refresh its content (no slide)."""
        self._tid = t.id
        self.graph.reset()
        self._populate_static(t)
        self.update_live(t, 0.0)

    def open_for(self, t):
        self._load(t)
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

    def retarget(self, t):
        """Swap to another task while already open — quick cross-fade, no slide.
        A pinned drawer stays on its task while the user selects others."""
        if not self.isVisible() or t.id == self._tid or self._pinned:
            return
        self._load(t)
        self.raise_()
        if hasattr(self, 'op_anim'):
            self.op_anim.stop()
            self.op_anim.setStartValue(0.35)
            self.op_anim.setEndValue(1.0)
            self.op_anim.start()

    def close_drawer(self):
        self._tid = None
        if self._pinned:
            self._pinned = False
            self._style_pin()
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
        self._url = t.url or ""
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        try:
            from gui2.download_card import _CAT_ICON
            cat = utils.category_for(t.filename)
            ic_name, ic_color = _CAT_ICON.get(cat, ("document", COLORS['muted']))
            if is_tor: ic_name, ic_color = "magnet", COLORS['accent']
        except Exception:
            ic_name, ic_color = "document", COLORS['muted']
        self.h_icon.setPixmap(themed_icon(ic_name, ic_color).pixmap(22, 22))
        # middle-elide the path so it stays one line; full path in the tooltip
        # (and _copy_path copies the full value, not the elided display)
        self._full_path = t.save_path or ""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.ov_path.font())
        self.ov_path.setWordWrap(False)
        self.ov_path.setText(fm.elidedText(self._full_path, Qt.ElideMiddle, 250) if self._full_path else "—")
        self.ov_path.setToolTip(self._full_path)
        self.ov_created.setText(humanize_age(getattr(t, "added", 0)) or "—")

        # Network facts that don't change mid-download
        if is_tor:
            self.ov_proto.setText("BitTorrent")
            self.ov_range.setText("—")
        else:
            scheme = (t.url or "").split(":", 1)[0].upper()
            self.ov_proto.setText(scheme or "—")
            self.ov_range.setText("Yes" if t.supports_range else "No")

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
        self._fill("headers", [(f"{k}: {v}", True) for k, v in hdr.items()],
                   empty=("document", "No extra headers",
                          "Browser-sent downloads carry Referer / User-Agent here."))

        # Logs: rendered as an event timeline by _render_logs (update_live
        # rebuilds it whenever the event count changes)
        self._ev_count = -1

    # ---- Logs timeline ----
    def _render_logs(self, t):
        """Rebuild the Logs tab as a status timeline (mockup): dot + event +
        relative age, then the URL / error details below. Only called when the
        event count changes, so no per-tick widget churn."""
        lay = self._logs_lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        rows = [(getattr(t, "added", 0), "Added")] + \
               [(ts, txt) for ts, txt in getattr(t, "events", [])]
        copy_lines = []
        for ts, txt in rows:
            age = humanize_age(ts) or ""
            copy_lines.append(f"{txt}  ({age})" if age else txt)
            r = QHBoxLayout(); r.setSpacing(10)
            dot = QLabel("●")
            col = _STATE_COLOR_S.get(txt, COLORS["accent"])
            dot.setStyleSheet(f"color: {col}; font-size: 9px; background: transparent;")
            lab = QLabel(txt)
            lab.setStyleSheet(f"color: {COLORS['text']}; font-weight: 700; font-size: 12px; background: transparent;")
            when = QLabel(age)
            when.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px; background: transparent;")
            r.addWidget(dot); r.addWidget(lab); r.addStretch(); r.addWidget(when)
            w = QWidget(); w.setLayout(r); w.setStyleSheet("background: transparent;")
            lay.addWidget(w)

        # detail lines (selectable) under the timeline
        details = [f"URL: {t.url}"]
        if t.error:
            details.append(f"Error: {t.error}")
        copy_lines += details
        lay.addSpacing(10)
        for text in details:
            l = QLabel(text); l.setWordWrap(True)
            l.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            l.setCursor(Qt.IBeamCursor)
            l.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;"
                            " font-family: Consolas, monospace; font-size: 11px;")
            lay.addWidget(l)
        lay.addStretch()
        self._log_lines = copy_lines

    def _integrity_text(self, t):
        st = getattr(t, "hash_status", "")
        if st == "ok":
            return "Verified ✓"
        if st == "fail":
            return "Mismatch — file may be corrupt"
        if st == "nohash":
            return "No checksum published"
        if not utils.HASH_CHECK:
            return "Verification off (Settings → Advanced)"
        if t.status == T.COMPLETED:
            return "—"
        return "Checked after completion"

    def update_live(self, t, bps):
        if t.id != self._tid:
            return
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        pct = 100 if t.status == T.COMPLETED else t.percent
        self.ov_pct.setText(f"{pct}%")
        self.bar.setValue(pct)
        col = _STATE_COLOR.get(t.status, COLORS["accent"])
        self._style_bar(col)
        self.ov_status.setText(str(t.status))
        self.ov_status.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 12px; background: transparent;")
        self.ov_speed.setText(human_speed(bps) or "0 b/s")
        done = human_size(t.downloaded) if t.downloaded > 0 else "0 B"
        self.ov_done.setText(f"{done} / {human_size(t.total_size)}" if t.total_size else done)
        self.ov_fsize.setText(human_size(t.total_size) if t.total_size else "—")
        eta = fmt_eta((t.total_size - t.downloaded) / bps) if bps > 0 and t.total_size else ""
        self.ov_eta.setText(eta or "—")
        self.ov_hash.setText(self._integrity_text(t))
        digest = getattr(t, "sha256", "")
        if digest:
            self.ov_digest.setText(digest[:10] + "…" + digest[-10:])
            self.ov_digest.setToolTip(digest)
        else:
            self.ov_digest.setText("—")
            self.ov_digest.setToolTip("")
        self.graph.push(bps if t.status == T.DOWNLOADING else 0.0)

        # Logs timeline: rebuild only when a new event landed
        n = len(getattr(t, "events", []))
        if n != getattr(self, "_ev_count", -1):
            self._ev_count = n
            self._render_logs(t)

        # Connections
        if is_tor:
            self.ov_conns.setText(str(getattr(t, "tor_conns", 0)))
            if t.status == T.DOWNLOADING:
                self._fill("conns", [
                    (f"Peers connected: {getattr(t,'tor_conns',0)}", False),
                    (f"Seeders: {getattr(t,'tor_seeds',0)}", False),
                ])
            else:
                self._fill("conns", [], empty=(
                    "magnet", "No active peers",
                    "Resume the download to see swarm details."))
        else:
            live = [s for s in t.segments if not s.complete]
            self.ov_conns.setText(str(len(live)) if t.status == T.DOWNLOADING else "0")
            if t.status == T.DOWNLOADING and t.segments:
                lines = []
                for s in t.segments:
                    total = (s.end - s.start + 1) if s.end >= s.start else 0
                    pc = int(s.downloaded * 100 / total) if total else (100 if s.complete else 0)
                    lines.append((f"Segment {s.index + 1}: {pc}%   ({human_size(s.downloaded)})", False))
                self._fill("conns", lines)
            else:
                self._fill("conns", [], empty=(
                    "link", "No active connections",
                    "Resume the download to see connection details."))

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
