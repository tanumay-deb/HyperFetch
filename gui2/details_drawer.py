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
    QTabWidget, QWidget, QScrollArea, QToolButton, QSizePolicy, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QLineEdit, QFileDialog,
    QCheckBox
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QPoint, QSize, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath

import task as T
import utils
import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta, humanize_age
from gui.icons import themed_icon
from gui2.palette import COLORS, fpx

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
        tl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: {fpx(12)}; background: transparent;")
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



class _FileRow(QWidget):
    def __init__(self, name, size, priority="Normal", is_folder=False, indent=0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(f"QWidget {{ border-bottom: 1px solid {COLORS['border']}; background: transparent; }} QWidget:hover {{ background: {COLORS['surface2']}; }}")
        h = QHBoxLayout(self); h.setContentsMargins(8 + indent*16, 2, 8, 2); h.setSpacing(12)
        
        from PySide6.QtWidgets import QCheckBox, QProgressBar, QMenu
        self.cb = QCheckBox(); self.cb.setChecked(True)
        h.addWidget(self.cb)
        
        ic_name = "folder" if is_folder else "document"
        ic_color = "warning" if is_folder else "muted"
        self.icon = QLabel(); self.icon.setPixmap(themed_icon(ic_name, ic_color).pixmap(16, 16))
        self.icon.setStyleSheet("background: transparent; border: none;")
        h.addWidget(self.icon)
        
        self.lbl_name = QLabel(name); self.lbl_name.setStyleSheet(f"color: {COLORS['text']}; font-weight: 600; font-size: {fpx(12)}; background: transparent; border: none;")
        self.lbl_name.setMinimumWidth(150); self.lbl_name.setWordWrap(False)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.lbl_name.font())
        elided = fm.elidedText(name, Qt.ElideMiddle, 300)
        self.lbl_name.setText(elided)
        self.lbl_name.setToolTip(name)
        h.addWidget(self.lbl_name, 1)
        
        self.lbl_size = QLabel(size); self.lbl_size.setFixedWidth(60)
        self.lbl_size.setStyleSheet(f"color: {COLORS['muted']}; font-size: {fpx(11)}; background: transparent; border: none;")
        h.addWidget(self.lbl_size)
        
        self.lbl_status = QLabel("Idle"); self.lbl_status.setFixedWidth(60)
        self.lbl_status.setStyleSheet(f"color: {COLORS['accent']}; font-size: {fpx(11)}; background: transparent; border: none;")
        h.addWidget(self.lbl_status)
        
        pc = QHBoxLayout(); pc.setSpacing(6)
        self.lbl_pct = QLabel("0%"); self.lbl_pct.setStyleSheet(f"color: {COLORS['success']}; font-weight: 700; font-size: {fpx(11)}; background: transparent; border: none;")
        self.lbl_pct.setFixedWidth(35)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setRange(0, 100); self.bar.setFixedHeight(4); self.bar.setFixedWidth(60)
        self.bar.setStyleSheet(f"QProgressBar{{background:{COLORS['surface']};border:none;border-radius:2px;}} QProgressBar::chunk{{background:{COLORS['accent']};border-radius:2px;}}")
        pc.addWidget(self.lbl_pct); pc.addWidget(self.bar)
        w_pc = QWidget(); w_pc.setLayout(pc); w_pc.setFixedWidth(100); w_pc.setStyleSheet("background: transparent; border: none;")
        h.addWidget(w_pc)
        
        self.lbl_added = QLabel("Today"); self.lbl_added.setFixedWidth(70)
        self.lbl_added.setStyleSheet(f"color: {COLORS['muted']}; font-size: {fpx(11)}; background: transparent; border: none;")
        h.addWidget(self.lbl_added)
        


    def set_progress(self, pct, status="Idle"):
        self.bar.setValue(pct)
        self.lbl_pct.setText(f"{pct}%")
        if pct == 100:
            self.lbl_pct.setStyleSheet(f"color: {COLORS['success']}; font-weight: 700; font-size: {fpx(11)}; background: transparent; border: none;")
            status = "Completed"
        
        self.lbl_status.setText(status)
        if status == "Completed":
            self.lbl_status.setStyleSheet(f"color: {COLORS['success']}; font-size: {fpx(11)}; background: transparent; border: none;")
        elif status == "Downloading":
            self.lbl_status.setStyleSheet(f"color: {COLORS['accent']}; font-size: {fpx(11)}; background: transparent; border: none;")
        elif status == "Waiting":
            self.lbl_status.setStyleSheet(f"color: {COLORS['warning']}; font-size: {fpx(11)}; background: transparent; border: none;")
        else:
            self.lbl_status.setStyleSheet(f"color: {COLORS['accent']}; font-size: {fpx(11)}; background: transparent; border: none;")

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
        self.h_name = QLabel(""); self.h_name.setStyleSheet(f"font-weight: 800; font-size: {fpx(14)}; background: transparent;")
        self.h_name.setWordWrap(True)
        self.h_name.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        self.tabs.addTab(self._files_tab(), "Files")
        self.tabs.addTab(self._conns_tab(), "Connections")
        
        from PySide6.QtWidgets import QStackedWidget
        self.headers_stack = QStackedWidget()
        self.headers_stack.addWidget(self._headers_tab())
        self.headers_stack.addWidget(self._trackers_tab())
        # label is swapped per task type in _populate_static — the same slot
        # shows request headers for an HTTP download and trackers for a torrent
        self._hdr_tab_index = self.tabs.addTab(self.headers_stack, "Headers")
        
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
        val.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: {fpx(13)}; background: transparent;")
        val.setAlignment(Qt.AlignHCenter)
        lab = QLabel(label)
        lab.setStyleSheet(f"color: {COLORS['muted']}; font-size: {fpx(10)}; background: transparent;")
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
            f" font-weight: 600; font-size: {fpx(11)}; }}"
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
        self.ov_pct.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: {fpx(34)}; background: transparent;")
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
        self.ov_status.setStyleSheet(f"color: {COLORS['muted']}; font-weight: 700; font-size: {fpx(12)}; background: transparent;")
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

    def _trackers_tab(self):
        """Announce list + a way to add more.

        Presented as a list, not a status table: aria2 exposes no per-tracker
        statistics and no reannounce, so peers/seeds/last-update columns would
        be invented.
        """
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14); v.setSpacing(10)

        sa = QScrollArea(); sa.setWidgetResizable(True)
        sa.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        inner = QWidget(); lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(6)
        lay.addStretch()
        sa.setWidget(inner)
        self._trackers_lay = lay
        v.addWidget(sa, 1)

        add_row = QHBoxLayout(); add_row.setSpacing(8)
        self.tracker_input = QLineEdit()
        self.tracker_input.setPlaceholderText("udp://tracker.example:80/announce, udp://other:6969")
        self.tracker_input.setToolTip("One or more tracker URLs, separated by commas")
        self.tracker_input.setStyleSheet(
            f"QLineEdit {{ background:{COLORS['surface2']}; color:{COLORS['text']};"
            f" border:1px solid {COLORS['border']}; border-radius:6px; padding:6px; }}")
        self.tracker_input.returnPressed.connect(self._add_trackers)
        btn = QPushButton(" Add"); btn.setIcon(themed_icon("plus", "text"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ padding:6px 14px; font-weight:600; background:{COLORS['surface2']};"
            f" border:1px solid {COLORS['border']}; border-radius:7px; }}"
            f"QPushButton:hover {{ background:{COLORS['card_hover']}; }}")
        btn.clicked.connect(self._add_trackers)
        add_row.addWidget(self.tracker_input, 1); add_row.addWidget(btn)
        v.addLayout(add_row)

        self.tracker_msg = QLabel("")
        self.tracker_msg.setWordWrap(True)
        self.tracker_msg.setStyleSheet(
            f"color:{COLORS['muted']}; font-size:{fpx(11)}; background:transparent;")
        v.addWidget(self.tracker_msg)
        return w

    def _add_trackers(self):
        """Merge comma-separated trackers into this magnet and, if it is
        running, hand them to aria2 so they take effect without a restart."""
        raw = self.tracker_input.text()
        entries = [s.strip() for s in raw.split(",") if s.strip()]
        if not entries:
            return
        win = self._window()
        t = next((x for x in win.queue.tasks if x.id == self._tid), None) if win else None
        if not t:
            return
        if not _torrent.is_magnet(t.url):
            self.tracker_msg.setText("Trackers can only be added to magnet links.")
            return

        bad = [e for e in entries if "://" not in e]
        if bad:
            self.tracker_msg.setText(f"Not a tracker URL: {bad[0]}")
            return

        new_url, added = _torrent.merge_magnet_trackers(t.url, entries)
        if not added:
            self.tracker_msg.setText("Already in this torrent's tracker list.")
            return
        t.url = new_url                      # persisted with the task
        t.log_event(f"Added {len(added)} tracker(s)")

        live = False
        if getattr(t, "gid", None):
            try:
                import aria2d
                aria2d.DAEMON.call("aria2.changeOption", t.gid,
                                   {"bt-tracker": ",".join(_torrent.magnet_trackers(new_url))})
                live = True
            except Exception:
                live = False
        self.tracker_input.clear()
        self.tracker_msg.setText(
            f"Added {len(added)} tracker(s)."
            + ("" if live else " They will be used the next time this torrent starts."))
        if win:
            win._save_state()
        self._render_trackers(t)

    def _render_trackers(self, t):
        lay = self._trackers_lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        urls = _torrent.magnet_trackers(t.url) if _torrent.is_magnet(t.url) else []
        if not urls:
            urls = [u for u in getattr(self, "_static_trackers", []) or []]
        for u in urls:
            l = QLabel(str(u)); l.setWordWrap(True)
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            l.setStyleSheet(f"color:{COLORS['text']}; font-size:{fpx(11)};"
                            " font-family: Consolas, monospace; background:transparent;")
            lay.addWidget(l)
        if not urls:
            e = QLabel("No trackers — this torrent relies on DHT.")
            e.setStyleSheet(f"color:{COLORS['muted']}; background:transparent;")
            lay.addWidget(e)
        lay.addStretch()

    def _conns_tab(self):
        """Live connections. For torrents these are swarm peers (aria2.getPeers);
        for an HTTP download they are the byte-range segments.

        Only columns aria2 actually reports are shown. Per-peer cumulative bytes
        and the uTP/TCP transport are deliberately absent — aria2 does not
        expose either, and inventing them on the screen people open to diagnose
        a slow download would be worse than leaving them out.
        """
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14); v.setSpacing(10)

        row = QHBoxLayout(); row.setSpacing(10)
        self.conn_summary = QLabel("—")
        self.conn_summary.setStyleSheet(
            f"color:{COLORS['muted']}; font-size:{fpx(12)}; background:transparent;")
        row.addWidget(self.conn_summary); row.addStretch()
        v.addLayout(row)

        self.conns_table = QTableWidget(0, 4)
        self.conns_table.setHorizontalHeaderLabels(["Peer", "Progress", "Down", "Up"])
        self.conns_table.verticalHeader().setVisible(False)
        self.conns_table.setShowGrid(False)
        self.conns_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.conns_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.conns_table.setStyleSheet(
            f"QTableWidget {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {COLORS['border']}; padding: 4px; }}"
            f"QHeaderView::section {{ background: {COLORS['surface2']}; color: {COLORS['muted']};"
            f" border: none; padding: 6px; font-weight: bold;"
            f" border-bottom: 1px solid {COLORS['border']}; }}")
        hh = self.conns_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        v.addWidget(self.conns_table, 1)

        self.conn_empty = QLabel()
        self.conn_empty.setAlignment(Qt.AlignCenter); self.conn_empty.setWordWrap(True)
        self.conn_empty.setStyleSheet(
            f"color:{COLORS['muted']}; font-size:{fpx(13)}; background:transparent;")
        self.conn_empty.hide()
        v.addWidget(self.conn_empty)
        return w

    @staticmethod
    def _bitfield_pct(bits):
        """Percentage of pieces a peer holds, from aria2's hex bitfield."""
        if not bits:
            return 0
        try:
            n = int(bits, 16)
        except (TypeError, ValueError):
            return 0
        total = len(bits) * 4                      # 4 bits per hex char
        return min(100, int(bin(n).count("1") * 100 / total)) if total else 0

    def _peer_rows(self, t):
        """[(label, pct, down_bps, up_bps)] from the daemon, or [] if the RPC
        engine is not driving this task."""
        gid = getattr(t, "gid", None)
        if not gid:
            return []
        try:
            import aria2d
            peers = aria2d.DAEMON.call("aria2.getPeers", gid) or []
        except Exception:
            return []
        out = []
        for p in peers:
            ip = str(p.get("ip", "?"))
            port = p.get("port")
            label = f"{ip}:{port}" if port else ip
            if str(p.get("seeder", "")).lower() == "true":
                label += "  · seed"
            out.append((label,
                        self._bitfield_pct(p.get("bitfield")),
                        int(p.get("downloadSpeed") or 0),
                        int(p.get("uploadSpeed") or 0)))
        out.sort(key=lambda r: r[2], reverse=True)        # fastest first
        return out

    def _fill_conns(self, t, is_tor):
        rows = self._peer_rows(t) if is_tor else []
        if not is_tor:
            # HTTP: the "connections" are this download's byte-range segments
            for s in t.segments:
                total = (s.end - s.start + 1) if s.end >= s.start else 0
                pct = int(s.downloaded * 100 / total) if total else (100 if s.complete else 0)
                rows.append((f"Segment {s.index + 1}", pct, 0, 0))

        self.conns_table.setRowCount(len(rows))
        for i, (label, pct, down, up) in enumerate(rows):
            self.conns_table.setItem(i, 0, QTableWidgetItem(label))
            self.conns_table.setItem(i, 1, QTableWidgetItem(f"{pct}%"))
            self.conns_table.setItem(i, 2, QTableWidgetItem(human_speed(down) if down else "—"))
            self.conns_table.setItem(i, 3, QTableWidgetItem(human_speed(up) if up else "—"))

        if rows:
            self.conns_table.show(); self.conn_empty.hide()
            if is_tor:
                seeds = getattr(t, "tor_seeds", 0)
                self.conn_summary.setText(f"{len(rows)} peer(s) · {seeds} seed(s)")
            else:
                live = sum(1 for s in t.segments if not s.complete)
                self.conn_summary.setText(f"{len(rows)} segment(s) · {live} active")
        else:
            self.conns_table.hide(); self.conn_empty.show()
            if is_tor and t.status == T.DOWNLOADING and not getattr(t, "gid", None):
                # honest about WHY it is empty rather than implying no peers
                self.conn_summary.setText("")
                self.conn_empty.setText(
                    "Per-peer details need the shared torrent engine.\n"
                    "Enable it in Settings → Advanced.")
            elif t.status == T.DOWNLOADING:
                self.conn_summary.setText("")
                self.conn_empty.setText("Connecting…")
            else:
                self.conn_summary.setText("")
                self.conn_empty.setText("Resume the download to see connections.")

    def _headers_tab(self):
        """Request headers as a real Name/Value table rather than 'k: v' text —
        values (cookies stripped, long UA strings) are selectable per cell and
        the name column stays readable when a value is huge."""
        self.h_table = QTableWidget(0, 2)
        self.h_table.setHorizontalHeaderLabels(["Header", "Value"])
        self.h_table.verticalHeader().setVisible(False)
        self.h_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.h_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.h_table.setAlternatingRowColors(True)
        hh = self.h_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        return self.h_table

    def _fill_headers(self, t):
        """Request headers we sent, then what the server answered.

        Cookies/auth are stripped from the request side and Set-Cookie never
        reaches the response side — this panel is the one place a screenshot
        could leak a session.
        """
        req = utils.strip_sensitive(getattr(t, "headers", {}) or {})
        resp = getattr(t, "response_headers", {}) or {}
        status = getattr(t, "response_status", 0)
        remote = getattr(t, "remote_address", "")

        rows = []
        rows.append(("— Request —", ""))
        rows += sorted(req.items()) or [("(none sent)", "")]
        rows.append(("", ""))
        if resp:
            label = f"— Response —   HTTP {status}" if status else "— Response —"
            if remote:
                label += f"   ·   {remote}"
            rows.append((label, ""))
            rows += sorted(resp.items())
        else:
            rows.append(("— Response —", ""))
            rows.append(("(captured once the download connects)", ""))

        self.h_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            name = QTableWidgetItem(str(k))
            if str(k).startswith("—"):          # section marker, not a header
                f = name.font(); f.setBold(True); name.setFont(f)
                name.setForeground(QColor(COLORS["accent"]))
            val = QTableWidgetItem(str(v))
            val.setToolTip(str(v))
            self.h_table.setItem(i, 0, name)
            self.h_table.setItem(i, 1, val)

    def _scroll_tab(self, key):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(2, 12, 2, 2); lay.setSpacing(6)
        lay.addStretch()
        sa.setWidget(inner)
        setattr(self, f"_{key}_lay", lay)
        return sa

    def _files_tab(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(12)
        
        # Top toolbar
        top_bar = QHBoxLayout()
        
        self.file_search = QLineEdit()
        self.file_search.setPlaceholderText("Search files...")
        self.file_search.textChanged.connect(self._filter_files)
        self.file_search.addAction(themed_icon("search", "muted"), QLineEdit.LeadingPosition)
        self.file_search.setStyleSheet(f"QLineEdit {{ background: {COLORS['surface2']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}")
        
        self.btn_select_all = QPushButton(" Select All")
        self.btn_select_all.setIcon(themed_icon("check-square", "text"))
        self.btn_select_all.setStyleSheet(f"QPushButton {{ padding: 4px 12px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 7px; }} QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        self.btn_select_all.clicked.connect(lambda: self._set_all_files(True))
        
        self.btn_select_none = QPushButton(" Select None")
        self.btn_select_none.setIcon(themed_icon("square", "text"))
        self.btn_select_none.setStyleSheet(f"QPushButton {{ padding: 4px 12px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 7px; }} QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        self.btn_select_none.clicked.connect(lambda: self._set_all_files(False))
        
        top_bar.addWidget(self.file_search, 1)
        top_bar.addWidget(self.btn_select_all)
        top_bar.addWidget(self.btn_select_none)
        v.addLayout(top_bar)
        
        # Table
        self.files_table = QTableWidget()
        self.files_table.setColumnCount(3)
        self.files_table.setHorizontalHeaderLabels(["Name", "Size", "Status"])
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setShowGrid(False)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setStyleSheet(
            f"QTableWidget {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {COLORS['border']}; padding: 4px; }}"
            f"QHeaderView::section {{ background: {COLORS['surface2']}; color: {COLORS['muted']}; border: none; padding: 6px; font-weight: bold; border-bottom: 1px solid {COLORS['border']}; }}"
        )
        
        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.files_table.setColumnWidth(1, 80)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.files_table.setColumnWidth(2, 90)
        
        v.addWidget(self.files_table, 1)
        return w

    def _filter_files(self):
        query = self.file_search.text().lower()
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, 0)
            if item:
                self.files_table.setRowHidden(row, query not in item.data(Qt.UserRole).lower())

    def _set_all_files(self, state):
        if not hasattr(self, '_file_row_widgets') or not self._file_row_widgets: return
        for idx, cb, sz in self._file_row_widgets:
            cb.setChecked(state)

    def _logs_tab(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(12)
        
        # Top toolbar
        top_bar = QHBoxLayout()
        
        self.log_filter_combo = QComboBox()
        self.log_filter_combo.addItems(["All Logs", "INFO", "SUCCESS", "ERROR", "WARNING"])
        self.log_filter_combo.currentTextChanged.connect(self._filter_logs)
        self.log_filter_combo.setFixedWidth(120)
        self.log_filter_combo.setStyleSheet(f"QComboBox {{ background: {COLORS['surface2']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}")
        
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Search logs...")
        self.log_search.textChanged.connect(self._filter_logs)
        self.log_search.addAction(themed_icon("search", "muted"), QLineEdit.LeadingPosition)
        self.log_search.setStyleSheet(f"QLineEdit {{ background: {COLORS['surface2']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}")
        
        self.clear_logs_btn = QPushButton(" Clear Logs")
        self.clear_logs_btn.setIcon(themed_icon("trash", "text"))
        self.clear_logs_btn.setObjectName("ghost")
        self.clear_logs_btn.setStyleSheet(f"QPushButton {{ padding: 4px 12px; font-weight: 600; background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 7px; }} QPushButton:hover {{ background: {COLORS['card_hover']}; }}")
        self.clear_logs_btn.clicked.connect(self._clear_logs)
        
        top_bar.addWidget(self.log_filter_combo)
        top_bar.addWidget(self.log_search, 1)
        top_bar.addWidget(self.clear_logs_btn)
        v.addLayout(top_bar)
        
        # Table
        self.logs_table = QTableWidget()
        self.logs_table.setColumnCount(4)
        self.logs_table.setHorizontalHeaderLabels(["Time", "Level", "Source", "Message"])
        self.logs_table.verticalHeader().setVisible(False)
        self.logs_table.setShowGrid(False)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.logs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.logs_table.setStyleSheet(
            f"QTableWidget {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {COLORS['border']}; padding: 4px; }}"
            f"QHeaderView::section {{ background: {COLORS['surface2']}; color: {COLORS['muted']}; border: none; padding: 6px; font-weight: bold; border-bottom: 1px solid {COLORS['border']}; }}"
        )
        
        header = self.logs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.logs_table.setColumnWidth(0, 80)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.logs_table.setColumnWidth(1, 90)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.logs_table.setColumnWidth(2, 110)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        
        v.addWidget(self.logs_table, 1)
        
        # Bottom toolbar
        bot_bar = QHBoxLayout()
        self.logs_count_lbl = QLabel("Showing 0 of 0 logs")
        self.logs_count_lbl.setStyleSheet(f"color: {COLORS['muted']}; font-size: {fpx(12)};")
        
        self.export_logs_btn = QPushButton(" Export Logs")
        self.export_logs_btn.setIcon(themed_icon("download", "white"))
        self.export_logs_btn.setStyleSheet(f"background: {COLORS['accent']}; color: white; border-radius: 4px; padding: 6px 12px; font-weight: bold;")
        self.export_logs_btn.clicked.connect(self._export_logs)
        
        bot_bar.addWidget(self.logs_count_lbl)
        bot_bar.addStretch()
        bot_bar.addWidget(self.export_logs_btn)
        v.addLayout(bot_bar)
        
        return w

    def _filter_logs(self):
        query = self.log_search.text().lower()
        level_filter = self.log_filter_combo.currentText()
        visible_count = 0
        total_count = self.logs_table.rowCount()
        
        for row in range(total_count):
            level_item = self.logs_table.item(row, 1)
            msg_item = self.logs_table.item(row, 3)
            src_item = self.logs_table.item(row, 2)
            if not level_item or not msg_item: continue
            
            lvl = level_item.data(Qt.UserRole)
            txt = msg_item.text().lower() + (src_item.text().lower() if src_item else "")
            
            show = True
            if level_filter != "All Logs" and lvl != level_filter:
                show = False
            if query and query not in txt:
                show = False
                
            self.logs_table.setRowHidden(row, not show)
            if show:
                visible_count += 1
                
        self.logs_count_lbl.setText(f"Showing {visible_count:,} of {total_count:,} logs")

    def _clear_logs(self):
        if not self._tid: return
        t = self.parent().queue.get_task(self._tid)
        if t and hasattr(t, "events"):
            t.events.clear()
            self._ev_count = -1
            self.update_live(t, 0.0)
            
    def _export_logs(self):
        if not self._tid: return
        t = self.parent().queue.get_task(self._tid)
        if not t: return
        path, _ = QFileDialog.getSaveFileName(self, "Export Logs", f"{t.filename}.log", "Log Files (*.log *.txt)")
        if not path: return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                for ev in getattr(t, "events", []):
                    ts = ev.get("time", 0) if isinstance(ev, dict) else ev[0]
                    msg = ev.get("message", "") if isinstance(ev, dict) else ev[1]
                    lvl = ev.get("level", "INFO") if isinstance(ev, dict) else "INFO"
                    src = ev.get("source", "System") if isinstance(ev, dict) else "System"
                    from datetime import datetime
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
                    f.write(f"[{time_str}] [{lvl}] [{src}] {msg}\n")
        except Exception as e:
            print(f"Failed to export logs: {e}")

    # ---- small actions ----
    def _emit(self, action):
        if self._tid:
            self.action.emit(action, self._tid)

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
            f" border-radius: 8px; font-size: {fpx(13)}; }}"
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
            tl.setStyleSheet(f"color: {COLORS['text']}; font-weight: 800; font-size: {fpx(13)}; background: transparent;")
            sb = QLabel(sub); sb.setAlignment(Qt.AlignCenter); sb.setWordWrap(True)
            sb.setStyleSheet(f"color: {COLORS['muted']}; font-size: {fpx(12)}; background: transparent;")
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
                style += f" font-family: Consolas, monospace; font-size: {fpx(11)};"
            l.setStyleSheet(style)
            lay.addWidget(l)
        if not lines:
            e = QLabel("—"); e.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
            lay.addWidget(e)
        lay.addStretch()

    # ---- open / close ----
    def _window(self):
        """The DownloadAppV2 that owns this drawer (it holds the queue).

        NOT QApplication.instance() — that has no `queue`, so looking there
        made every file-selection change silently do nothing.
        """
        w = self.parent()
        while w is not None and not hasattr(w, "queue"):
            w = w.parent()
        return w

    def _apply_file_selection(self):
        if not getattr(self, "_file_row_widgets", None):
            return
        # aria2 indexes files from 1
        selected = [str(idx + 1) for idx, cb, _ in self._file_row_widgets if cb.isChecked()]
        win = self._window()
        if not win:
            return
        t = next((x for x in win.queue.tasks if x.id == self._tid), None)
        # is_torrent_task(), not a task attribute that never existed
        if not t or not _torrent.is_torrent_task(t.url, t.filename):
            return
        if not selected:
            # aria2 reads an empty select-file as "everything", so an empty
            # selection would silently re-enable every file. Keep the last file
            # ticked instead of doing the opposite of what the user asked.
            self._file_row_widgets[0][1].setChecked(True)
            return
        win.queue.change_torrent_files(self._tid, ",".join(selected))

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
            
        # Extract trackers statically once
        self._static_trackers = []
        if is_tor:
            import torrent
            if t.url and t.url.startswith("magnet:"):
                extracted = torrent.magnet_trackers(t.url)
                self._static_trackers.extend(extracted)
                for pt in torrent.PUBLIC_TRACKERS:
                    if pt not in self._static_trackers:
                        self._static_trackers.append(pt + " (Public)")
            elif t.url and os.path.isfile(t.url):
                try:
                    with open(t.url, "rb") as f:
                        data = torrent._bdecode(f.read())
                        if b"announce-list" in data:
                            for tier in data[b"announce-list"]:
                                for tr_b in tier:
                                    self._static_trackers.append(tr_b.decode("utf-8", "ignore"))
                        elif b"announce" in data:
                            self._static_trackers.append(data[b"announce"].decode("utf-8", "ignore"))
                except Exception:
                    pass

        self._file_row_widgets = []
        self.files_table.setRowCount(0)
        
        sp = t.save_path
        total_sz = 0
        total_files = 0
        
        def add_file_row(idx, name, size, is_folder, pct, status_str, indent=0):
            r = self.files_table.rowCount()
            self.files_table.insertRow(r)
            
            # Col 0: Name (Checkbox + Icon + Label)
            w = QWidget()
            h = QHBoxLayout(w); h.setContentsMargins(4 + indent*16, 2, 4, 2); h.setSpacing(8)
            cb = QCheckBox(); cb.setChecked(True)
            cb.stateChanged.connect(self._apply_file_selection)
            ic_name = "folder" if is_folder else "document"
            ic_color = "warning" if is_folder else "muted"
            icon = QLabel(); icon.setPixmap(themed_icon(ic_name, ic_color).pixmap(16, 16))
            lbl = QLabel(name); lbl.setStyleSheet("background: transparent;")
            lbl.setToolTip(name)
            h.addWidget(cb)
            h.addWidget(icon)
            h.addWidget(lbl, 1)
            
            name_item = QTableWidgetItem()
            name_item.setData(Qt.UserRole, name)
            self.files_table.setItem(r, 0, name_item)
            self.files_table.setCellWidget(r, 0, w)
            
            # Col 1: Size
            sz_item = QTableWidgetItem(human_size(size))
            sz_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sz_item.setForeground(QColor(COLORS['muted']))
            self.files_table.setItem(r, 1, sz_item)
            
            # Col 2: Status
            status_item = QTableWidgetItem(f"{pct}%" if pct < 100 else status_str)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor(COLORS['accent'] if pct < 100 else COLORS['muted']))
            self.files_table.setItem(r, 2, status_item)
            
            self._file_row_widgets.append((idx, cb, size))
            
        if is_tor:
            entries = _torrent.list_files(t)
            if not entries:
                self.files_table.setRowCount(1)
                self.files_table.setSpan(0, 0, 1, 3)
                wait_item = QTableWidgetItem("Waiting for torrent metadata...")
                wait_item.setTextAlignment(Qt.AlignCenter)
                wait_item.setForeground(QColor(COLORS['muted']))
                self.files_table.setItem(0, 0, wait_item)
            else:
                for i, (rel, size) in enumerate(entries):
                    total_sz += size
                    total_files += 1
                    parts = rel.split("/")
                    indent = len(parts) - 1
                    name = parts[-1]
                    pct = 0
                    if sp:
                        base = sp if os.path.isdir(sp) else os.path.dirname(sp) or "."
                        fp = os.path.join(base, *parts)
                        if os.path.isfile(fp):
                            got = os.path.getsize(fp)
                            if size: pct = int(got * 100 / size)
                            elif size == 0: pct = 100
                    status_str = "Completed" if pct >= 100 else ("Downloading" if t.status == T.DOWNLOADING else "Idle")
                    add_file_row(i, name, size, False, pct, status_str, indent)
        else:
            if sp and os.path.isdir(sp):
                try:
                    for i, name in enumerate(sorted(os.listdir(sp))):
                        fp = os.path.join(sp, name)
                        sz = os.path.getsize(fp) if os.path.isfile(fp) else 0
                        total_sz += sz
                        total_files += 1
                        add_file_row(i, name, sz, os.path.isdir(fp), 100, "Completed")
                except OSError: pass
            else:
                sz = t.total_size or 0
                total_sz += sz
                total_files += 1
                pct = 100 if t.status == T.COMPLETED else t.percent
                status_str = "Completed" if t.status == T.COMPLETED else ("Downloading" if t.status == T.DOWNLOADING else "Idle")
                add_file_row(0, t.filename or "file", sz, False, pct, status_str)
        


        # Headers (cookies/auth stripped)
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        self.headers_stack.setCurrentIndex(1 if is_tor else 0)
        # the slot shows trackers for a torrent and request headers for an
        # HTTP download — say which, instead of always claiming 'Headers'
        self.tabs.setTabText(self._hdr_tab_index, "Trackers" if is_tor else "Headers")
        if is_tor:
            self._render_trackers(t)
        tab_index = self.tabs.indexOf(self.headers_stack)
        self.tabs.setTabText(tab_index, "Trackers" if is_tor else "Headers")
        
        if is_tor:
            trk = getattr(t, "trackers", [])
            lines = []
            for tr in trk:
                st = tr.get("status", "Unknown")
        else:
            self._fill_headers(t)

        # Logs: rendered as an event timeline by _render_logs (update_live
        # rebuilds it whenever the event count changes)
        self._ev_count = -1

    # ---- Logs timeline ----
    def _render_logs(self, t):
        self.logs_table.setRowCount(0)
        events = getattr(t, "events", [])
        
        self.logs_table.setRowCount(len(events) + 1)
        
        def make_badge(level):
            l = QLabel(level)
            l.setAlignment(Qt.AlignCenter)
            bg, text_col = COLORS["info"], "white"
            if level == "SUCCESS": bg = COLORS["success"]
            elif level == "ERROR": bg = COLORS["error"]
            elif level == "WARNING": bg = COLORS["warning"]
            l.setStyleSheet(f"background: {bg}; color: {text_col}; border-radius: 4px; font-size: {fpx(10)}; font-weight: bold; padding: 2px 6px;")
            w = QWidget(); lay = QHBoxLayout(w); lay.setContentsMargins(4, 2, 4, 2); lay.addWidget(l)
            return w, level
            
        def make_item(text, color):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(color))
            return item
            
        from datetime import datetime
        row_idx = 0
        
        # Added event
        added_ts = getattr(t, "added", 0)
        time_str = datetime.fromtimestamp(added_ts).strftime("%H:%M:%S") if added_ts else ""
        self.logs_table.setItem(row_idx, 0, make_item(time_str, COLORS["muted"]))
        
        badge_w, lvl = make_badge("SUCCESS")
        self.logs_table.setCellWidget(row_idx, 1, badge_w)
        lvl_item = QTableWidgetItem()
        lvl_item.setData(Qt.UserRole, lvl)
        self.logs_table.setItem(row_idx, 1, lvl_item)
        
        self.logs_table.setItem(row_idx, 2, make_item("System", COLORS["muted"]))
        self.logs_table.setItem(row_idx, 3, make_item("Added to queue", COLORS["text"]))
        row_idx += 1
        
        for ev in events:
            ts = ev.get("time", 0) if isinstance(ev, dict) else ev[0]
            msg = ev.get("message", "") if isinstance(ev, dict) else ev[1]
            lvl = ev.get("level", "INFO") if isinstance(ev, dict) else "INFO"
            src = ev.get("source", "System") if isinstance(ev, dict) else "System"
            
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            self.logs_table.setItem(row_idx, 0, make_item(time_str, COLORS["muted"]))
            
            badge_w, b_lvl = make_badge(lvl)
            self.logs_table.setCellWidget(row_idx, 1, badge_w)
            lvl_item = QTableWidgetItem()
            lvl_item.setData(Qt.UserRole, b_lvl)
            self.logs_table.setItem(row_idx, 1, lvl_item)
            
            self.logs_table.setItem(row_idx, 2, make_item(src, COLORS["muted"]))
            self.logs_table.setItem(row_idx, 3, make_item(msg, COLORS["text"]))
            row_idx += 1

        # Plain-text mirror of the table for copy-to-clipboard. A QTableWidget
        # cannot be pasted into a bug report, and this is the whole point of the
        # Logs tab, so keep the flat form alongside the rendered one.
        lines = [f"Added: {getattr(t, 'added', 0)}  [SUCCESS] System — Added to queue"]
        for ev in events:
            if isinstance(ev, dict):
                lines.append(f"{ev.get('time', 0)}  [{ev.get('level', 'INFO')}] "
                             f"{ev.get('source', 'System')} — {ev.get('message', '')}")
            elif isinstance(ev, (list, tuple)) and len(ev) == 2:
                lines.append(f"{ev[0]}  [INFO] System — {ev[1]}")
        lines.append(f"URL: {getattr(t, 'url', '')}")
        if getattr(t, "error", ""):
            lines.append(f"Error: {t.error}")
        self._log_lines = lines

        self._filter_logs()

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
        self.ov_status.setStyleSheet(f"color: {col}; font-weight: 700; font-size: {fpx(12)}; background: transparent;")
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

        # Update files progress if applicable
        if hasattr(self, '_file_row_widgets') and self._file_row_widgets and t.status == T.DOWNLOADING:
            sp = t.save_path
            if _torrent.is_torrent_task(t.url, t.filename) and sp:
                entries = _torrent.list_files(t)
                for i, cb, size in self._file_row_widgets[:150]:
                    if i < len(entries):
                        rel, _ = entries[i]
                        parts = rel.split("/")
                        base = sp if os.path.isdir(sp) else os.path.dirname(sp) or "."
                        fp = os.path.join(base, *parts)
                        if os.path.isfile(fp):
                            got = os.path.getsize(fp)
                            pct = int(got * 100 / size) if size else 100
                            pct = min(100, pct)
                            status_str = "Completed" if pct >= 100 else "Downloading"
                            item = self.files_table.item(i, 2)
                            if item:
                                item.setText(f"{pct}%" if pct < 100 else status_str)
                                item.setForeground(QColor(COLORS['accent'] if pct < 100 else COLORS['muted']))
            elif not _torrent.is_torrent_task(t.url, t.filename) and len(self._file_row_widgets) == 1:
                pct = t.percent
                status_str = "Completed" if pct >= 100 else "Downloading"
                item = self.files_table.item(0, 2)
                if item:
                    item.setText(f"{pct}%" if pct < 100 else status_str)
                    item.setForeground(QColor(COLORS['accent'] if pct < 100 else COLORS['muted']))

        # (trackers are rendered by _render_trackers from _populate_static)

        # Logs timeline: rebuild only when a new event landed
        n = len(getattr(t, "events", []))
        if n != getattr(self, "_ev_count", -1):
            self._ev_count = n
            self._render_logs(t)

        # Connections
        if is_tor:
            self.ov_conns.setText(str(getattr(t, "tor_conns", 0)))
        else:
            live = [s for s in t.segments if not s.complete]
            self.ov_conns.setText(str(len(live)) if t.status == T.DOWNLOADING else "0")
        # peers/segments only matter while the Connections tab is on screen —
        # getPeers is an RPC round trip and this runs on the 500ms tick
        if self.tabs.currentIndex() == self.tabs.indexOf(self.conns_table.parent()):
            self._fill_conns(t, is_tor)

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
