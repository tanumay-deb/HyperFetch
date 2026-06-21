"""Sidebar — brand, New Download, nav rows (categories + status with counts),
the live stats card, and Settings. Pure widgets; collapsible to an icon rail.
"""
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, Signal

from gui2.palette import COLORS
from gui2.speed_gauge import CircularSpeedGauge
from gui.theme import human_speed

# (icon, label, key, icon_color)
_CATEGORIES = [
    ("🗜", "Compressed", "Compressed", None),
    ("⚙", "Programs", "Programs", None),
    ("🎬", "Videos", "Video", None),
    ("🎵", "Music", "Music", None),
    ("📄", "Documents", "Documents", None),
    ("📁", "Other", "Other", None),
]
_STATUS = [
    ("↓", "Active", "Active", "#22c55e"),
    ("⏸", "Paused", "Paused", "#f59e0b"),
    ("✓", "Completed", "Completed", "#38bdf8"),
    ("！", "Failed", "Failed", "#ef4444"),
]


class NavRow(QFrame):
    clicked = Signal(str)

    def __init__(self, icon, label, key, icon_color=None):
        super().__init__()
        self.key = key
        self._active = False
        self._collapsed = False
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(12)
        self.ic = QLabel(icon)
        self.ic.setFixedWidth(22)
        self.ic.setAlignment(Qt.AlignCenter)
        self.ic.setStyleSheet(f"font-size: 15px; background: transparent; color: {icon_color or COLORS['muted']};")
        self.lbl = QLabel(label)
        self.lbl.setStyleSheet(f"background: transparent; font-weight: 600; color: {COLORS['muted']};")
        self.cnt = QLabel("")
        self.cnt.setStyleSheet(f"background: transparent; color: {COLORS['faint']}; font-size: 12px;")
        h.addWidget(self.ic)
        h.addWidget(self.lbl)
        h.addStretch()
        h.addWidget(self.cnt)
        self._restyle()

    def set_count(self, n):
        self.cnt.setText(str(n) if n is not None else "")

    def set_active(self, on):
        self._active = on
        self._restyle()

    def set_collapsed(self, on):
        self._collapsed = on
        self.lbl.setVisible(not on)
        self.cnt.setVisible(not on)
        self._restyle()

    def _restyle(self):
        if self._active:
            self.setStyleSheet(
                f"NavRow {{ background: {COLORS['surface2']}; border-radius: 9px;"
                f" border-left: 3px solid {COLORS['accent']}; }}")
            self.lbl.setStyleSheet(f"background: transparent; font-weight: 700; color: {COLORS['text']};")
        else:
            self.setStyleSheet("NavRow { background: transparent; border-radius: 9px; }")
            self.lbl.setStyleSheet(f"background: transparent; font-weight: 600; color: {COLORS['muted']};")

    def enterEvent(self, _e):
        if not self._active:
            self.setStyleSheet(f"NavRow {{ background: {COLORS['surface2']}; border-radius: 9px; }}")

    def leaveEvent(self, _e):
        if not self._active:
            self.setStyleSheet("NavRow { background: transparent; border-radius: 9px; }")

    def mousePressEvent(self, _e):
        self.clicked.emit(self.key)


class Sidebar(QFrame):
    filterChanged = Signal(str)
    newDownload = Signal()
    openSettings = Signal()
    toggleCollapse = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(260)
        self._rows = {}
        self._collapsed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        # ---- brand + collapse ----
        top = QHBoxLayout()
        self.brand_icon = QLabel("⚡")
        self.brand_icon.setStyleSheet(f"font-size: 20px; color: {COLORS['accent']}; background: transparent;")
        self.brand = QLabel("HyperFetch")
        self.brand.setObjectName("brand")
        self.brand.setStyleSheet("background: transparent; font-size: 18px; font-weight: 800;")
        self.btn_collapse = QPushButton("☰")
        self.btn_collapse.setObjectName("iconbtn")
        self.btn_collapse.setFixedSize(32, 30)
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.setStyleSheet("font-size: 18px;")
        self.btn_collapse.clicked.connect(self.toggleCollapse)
        top.addWidget(self.brand_icon)
        top.addWidget(self.brand)
        top.addStretch()
        top.addWidget(self.btn_collapse)
        lay.addLayout(top)

        # ---- new download ----
        self.btn_new = QPushButton("＋  New Download")
        self.btn_new.setObjectName("primary")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.clicked.connect(self.newDownload)
        lay.addWidget(self.btn_new)
        lay.addSpacing(4)

        # ---- DOWNLOADS ----
        self.t_dl = self._section("DOWNLOADS")
        lay.addWidget(self.t_dl)
        all_row = NavRow("▦", "All", "All")
        self._add_row(lay, all_row)

        # ---- CATEGORIES ----
        self.t_cat = self._section("CATEGORIES")
        lay.addWidget(self.t_cat)
        for ic, lbl, key, col in _CATEGORIES:
            self._add_row(lay, NavRow(ic, lbl, key, col))

        # ---- STATUS ----
        self.t_st = self._section("STATUS")
        lay.addWidget(self.t_st)
        for ic, lbl, key, col in _STATUS:
            self._add_row(lay, NavRow(ic, lbl, key, col))

        lay.addStretch()

        # ---- stats card ----
        self.stats = QFrame()
        self.stats.setObjectName("statsCard")
        sc = QHBoxLayout(self.stats)
        sc.setContentsMargins(12, 14, 14, 14)
        sc.setSpacing(10)
        self.gauge = CircularSpeedGauge()
        self.gauge.setFixedSize(80, 80)
        sc.addWidget(self.gauge)
        stext = QVBoxLayout()
        stext.setSpacing(1)
        self.lbl_speed = QLabel("0 B/s")
        self.lbl_speed.setStyleSheet(f"font-size: 17px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap1 = QLabel("Current Speed")
        cap1.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        self.lbl_conns = QLabel("0")
        self.lbl_conns.setStyleSheet(f"font-size: 17px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap2 = QLabel("Connections")
        cap2.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        stext.addWidget(self.lbl_speed); stext.addWidget(cap1)
        stext.addSpacing(6)
        stext.addWidget(self.lbl_conns); stext.addWidget(cap2)
        stext.addStretch()
        self._stext = stext
        sc.addLayout(stext)
        lay.addWidget(self.stats)

        # ---- settings ----
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setStyleSheet(f"text-align: left; padding: 10px 12px; font-weight: 700; color: {COLORS['text']};")
        self.btn_settings.clicked.connect(self.openSettings)
        lay.addWidget(self.btn_settings)

        self.set_active("All")

    def _section(self, title):
        l = QLabel(title)
        l.setObjectName("sectionTitle")
        l.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px; font-weight: 800; letter-spacing: 1px; background: transparent; padding: 8px 2px 2px;")
        return l

    def _add_row(self, lay, row):
        row.clicked.connect(self._on_row)
        self._rows[row.key] = row
        lay.addWidget(row)

    def _on_row(self, key):
        self.set_active(key)
        self.filterChanged.emit(key)

    def set_active(self, key):
        for k, row in self._rows.items():
            row.set_active(k == key)

    def set_counts(self, counts):
        for key, row in self._rows.items():
            row.set_count(counts.get(key, 0))

    def set_stats(self, bps, conns):
        self.lbl_speed.setText(human_speed(bps) or "0 b/s")
        self.lbl_conns.setText(str(conns))
        self.gauge.push(bps)

    def set_collapsed(self, on):
        self._collapsed = on
        self.setFixedWidth(72 if on else 260)
        self.brand.setVisible(not on)
        for t in (self.t_dl, self.t_cat, self.t_st):
            t.setVisible(not on)
        self.stats.setVisible(not on)
        for row in self._rows.values():
            row.set_collapsed(on)
        self.btn_new.setText("＋" if on else "＋  New Download")
        self.btn_settings.setText("⚙" if on else "⚙  Settings")
