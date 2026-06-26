"""Sidebar — brand, New Download, nav rows (categories + status with counts),
the live stats card, and Settings. Pure widgets; collapsible to an icon rail.
"""
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget
)
from PySide6.QtCore import Qt, Signal

from gui2.palette import COLORS
from gui2.speed_gauge import SpeedGraph
from gui.theme import human_speed
from gui.icons import themed_icon
from gui2.brand import BrandLogo

# (icon, label, key, icon_color)
_CATEGORIES = [
    ("archive", "Compressed", "Compressed", None),
    ("program", "Programs", "Programs", None),
    ("video", "Videos", "Video", None),
    ("music", "Music", "Music", None),
    ("image", "Images", "Images", None),
    ("document", "Documents", "Documents", None),
    ("folder", "Other", "Other", None),
]
_STATUS = [
    ("play", "Active", "Active", COLORS["success"]),
    ("pause", "Paused", "Paused", COLORS["warning"]),
    ("check", "Completed", "Completed", COLORS["success"]),
    ("x-circle", "Failed", "Failed", COLORS["error"]),
]


class NavRow(QFrame):
    clicked = Signal(str)

    def __init__(self, icon, label, key, icon_color=None):
        super().__init__()
        self.key = key
        self._label = label
        self._count = 0
        self._active = False
        self._collapsed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)                     # hover shows the label (esp. collapsed)
        self._h = QHBoxLayout(self)
        self._h.setContentsMargins(12, 8, 12, 8)
        self._h.setSpacing(12)
        self.ic = QLabel()
        self.ic_name = icon
        self.ic_color = icon_color
        self.ic.setFixedWidth(22)
        self.ic.setAlignment(Qt.AlignCenter)
        self.lbl = QLabel(label)
        self.lbl.setStyleSheet(f"background: transparent; font-weight: 600; color: {COLORS['muted']};")
        self.cnt = QLabel("")
        self.cnt.setStyleSheet(f"background: transparent; color: {COLORS['faint']}; font-size: 12px;")
        self._h.addWidget(self.ic)
        self._h.addWidget(self.lbl)
        self._h.addStretch()
        self._h.addWidget(self.cnt)
        self._restyle()

    def set_count(self, n):
        self._count = n or 0
        self.cnt.setText(str(n) if n else "")
        self.setToolTip(f"{self._label}  ·  {self._count}" if self._count else self._label)

    def set_active(self, on):
        self._active = on
        self._restyle()

    def set_collapsed(self, on):
        self._collapsed = on
        self.lbl.setVisible(not on)
        self.cnt.setVisible(not on)
        # symmetric margins + full-width icon so the glyph truly centers in the rail
        self._h.setContentsMargins(0, 8, 0, 8) if on else self._h.setContentsMargins(12, 8, 12, 8)
        self.ic.setFixedWidth(44 if on else 22)
        self._restyle()

    def _restyle(self):
        color_val = self.ic_color or ("text" if self._active else "muted")
        self.ic.setPixmap(themed_icon(self.ic_name, color_val).pixmap(16, 16))
        if self._active:
            # collapsed → centered pill (a left-border looks detached on a centered
            # icon); expanded → the left accent bar
            if self._collapsed:
                self.setStyleSheet(f"NavRow {{ background: {COLORS['surface2']}; border-radius: 10px; }}")
            else:
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
    manageQueues = Signal()
    openHistory = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(260)
        self._rows = {}
        self._collapsed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(7)

        # ---- brand + collapse ----
        top = QHBoxLayout()
        self.brand_icon = BrandLogo(26)
        self.brand = QLabel("HyperFetch")
        self.brand.setObjectName("brand")
        self.brand.setStyleSheet("background: transparent; font-size: 18px; font-weight: 800;")
        self.btn_collapse = QPushButton()
        self.btn_collapse.setIcon(themed_icon("menu", "text"))
        self.btn_collapse.setObjectName("iconbtn")
        self.btn_collapse.setFixedSize(32, 30)
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.setToolTip("Collapse / expand sidebar")
        self.btn_collapse.clicked.connect(self.toggleCollapse)
        top.addWidget(self.brand_icon)
        top.addWidget(self.brand)
        top.addStretch()
        top.addWidget(self.btn_collapse)
        lay.addLayout(top)

        # ---- new download ----
        self.btn_new = QPushButton("  New Download")
        self.btn_new.setIcon(themed_icon("plus", "white"))
        self.btn_new.setObjectName("primary")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setToolTip("New Download")
        self.btn_new.clicked.connect(self.newDownload)
        lay.addWidget(self.btn_new)
        lay.addSpacing(4)

        # ---- nav: All (acts as the list header) + categories, no section labels ----
        self._add_row(lay, NavRow("all", "All", "All"))
        for ic, lbl, key, col in _CATEGORIES:
            self._add_row(lay, NavRow(ic, lbl, key, col))

        # status lives in the main-window filter pills — not in the sidebar.
        lay.addStretch()

        # ---- history ----
        self.btn_history = QPushButton("   History")
        self.btn_history.setIcon(themed_icon("history", "muted"))
        self.btn_history.setObjectName("navItem")
        self.btn_history.setCursor(Qt.PointingHandCursor)
        self.btn_history.setToolTip("History")
        self.btn_history.clicked.connect(self.openHistory)
        lay.addWidget(self.btn_history)

        # ---- queues ----
        self.btn_queues = QPushButton("   Queues")
        self.btn_queues.setIcon(themed_icon("queue", "muted"))
        self.btn_queues.setObjectName("navItem")
        self.btn_queues.setCursor(Qt.PointingHandCursor)
        self.btn_queues.setToolTip("Queues")
        self.btn_queues.clicked.connect(self.manageQueues)
        lay.addWidget(self.btn_queues)

        # ---- stats card: line graph + speed/connections readout ----
        self.stats = QFrame()
        self.stats.setObjectName("statsCard")
        sv = QVBoxLayout(self.stats)
        sv.setContentsMargins(12, 10, 12, 10)
        sv.setSpacing(8)
        self.graph = SpeedGraph()
        self.graph.setFixedHeight(54)
        sv.addWidget(self.graph)
        self.g_text = QWidget()
        self.g_text.setStyleSheet("background: transparent;")
        gt = QHBoxLayout(self.g_text)
        gt.setContentsMargins(0, 0, 0, 0)
        scol = QVBoxLayout(); scol.setSpacing(0)
        self.lbl_speed = QLabel("0 B/s")
        self.lbl_speed.setStyleSheet(f"font-size: 16px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap1 = QLabel("Current Speed")
        cap1.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        scol.addWidget(self.lbl_speed); scol.addWidget(cap1)
        ccol = QVBoxLayout(); ccol.setSpacing(0)
        self.lbl_conns = QLabel("0")
        self.lbl_conns.setAlignment(Qt.AlignRight)
        self.lbl_conns.setStyleSheet(f"font-size: 16px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap2 = QLabel("Connections")
        cap2.setAlignment(Qt.AlignRight)
        cap2.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        ccol.addWidget(self.lbl_conns); ccol.addWidget(cap2)
        gt.addLayout(scol); gt.addStretch(); gt.addLayout(ccol)
        sv.addWidget(self.g_text)
        lay.addWidget(self.stats)

        # ---- settings ----
        self.btn_settings = QPushButton("  Settings")
        self.btn_settings.setIcon(themed_icon("settings", "muted"))
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self._settings_qss = f"text-align: left; padding: 10px 12px; font-weight: 700; color: {COLORS['text']};"
        self.btn_settings.setStyleSheet(self._settings_qss)
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.clicked.connect(self.openSettings)
        lay.addWidget(self.btn_settings)

        self.set_collapsed(False)        # initial expanded state hides status icons
        self.set_active("All")

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
        sp = human_speed(bps) or "0 B/s"
        self.lbl_speed.setText(sp)
        self.lbl_conns.setText(str(conns))
        self.graph.push(bps)
        self.stats.setToolTip(f"Speed {sp}  ·  {conns} connection{'s' if conns != 1 else ''}")

    def set_collapsed(self, on):
        # width is animated by the app (DownloadAppV2._toggle_sidebar); here we
        # only toggle what's visible so the rail reads cleanly when narrow.
        self._collapsed = on
        self.brand.setVisible(not on)
        self.brand_icon.setVisible(not on)
        # keep the speed graph visible when collapsed (text hidden)
        self.stats.setVisible(True)
        self.g_text.setVisible(not on)
        for row in self._rows.values():
            row.set_collapsed(on)
        self.btn_new.setText("" if on else "  New Download")
        self.btn_history.setText("" if on else "   History")
        self.btn_queues.setText("" if on else "  Queues")
        self.btn_settings.setText("" if on else "  Settings")
        # center the icons in the rail when collapsed (navItem/ghost default to
        # left-aligned text, which leaves the icon hugging the left edge)
        center = "text-align: center; padding-left: 0; padding-right: 0;"
        self.btn_history.setStyleSheet(center if on else "")
        self.btn_queues.setStyleSheet(center if on else "")
        self.btn_settings.setStyleSheet(
            (self._settings_qss + center) if on else self._settings_qss)
