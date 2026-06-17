"""HyperFetch - IDM-style GUI.

Multi-segment downloads with a live queue, pause/resume/cancel, IDM-style
file-info dialogs, persistent state, and an embedded Flask server so the
browser extension feeds downloads into this same window.
"""
import os
import sys
import time
import threading
import subprocess
from collections import deque

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QDialog,
    QLineEdit, QSpinBox, QFileDialog, QMessageBox, QAbstractItemView,
    QFrame, QButtonGroup, QGridLayout, QSplitter, QSizePolicy, QComboBox, QMenu, QInputDialog,
    QCheckBox, QListWidget, QListWidgetItem, QTableView, QStyledItemDelegate, QStyle
)
from PySide6.QtCore import (
    Qt, QTimer, QModelIndex, QAbstractTableModel, QSortFilterProxyModel, QRect, QSize
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QBrush, QPen, QLinearGradient

import task as T
import utils
from queue_manager import QueueManager
from api_server import run_server, PORT

APP_VERSION = "1.1.0"


def resource_path(*parts):
    """Locate a bundled resource both in dev and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


SEGMENTS = 8
MAX_CONCURRENT = 3

# ---------------------------------------------------------------- theme palettes
DARK = dict(
    accent="#6366f1", accent2="#8b5cf6",
    bg="#0b1220", surface="#111a2e", surface2="#1a2540",
    border="#243352", hover="#223054", pressed="#1b2747",
    alt="#141e36", sel="#26335c",
    text="#e5e9f5", muted="#8b97b8",
)
LIGHT = dict(
    accent="#6366f1", accent2="#8b5cf6",
    bg="#eef1f7", surface="#ffffff", surface2="#f3f5fb",
    border="#d7dce8", hover="#e9edf6", pressed="#dfe4f1",
    alt="#f6f8fc", sel="#dde6fb",
    text="#1a2236", muted="#5d6b8a",
)

# Active palette name + flattened color globals. apply_theme() reassigns these so
# custom-painted widgets (which read them at paint time) follow a theme switch.
THEME = "dark"
ACCENT = ACCENT_2 = BG = SURFACE = SURFACE_2 = ""
BORDER = HOVER = PRESSED = ALT = SEL = TEXT = MUTED = ""

# status pill colours read well on both palettes, so they stay theme-independent
STATUS_COLORS = {
    T.DOWNLOADING: "#34d399",
    T.COMPLETED:   "#38bdf8",
    T.PAUSED:      "#fbbf24",
    T.QUEUED:      "#94a3b8",
    T.ERROR:       "#f87171",
    T.CANCELLED:   "#64748b",
}


def palette_for(name):
    return LIGHT if name == "light" else DARK


def apply_theme(name):
    """Switch the active palette by reassigning the module-level colour globals."""
    global THEME, ACCENT, ACCENT_2, BG, SURFACE, SURFACE_2
    global BORDER, HOVER, PRESSED, ALT, SEL, TEXT, MUTED
    THEME = "light" if name == "light" else "dark"
    p = palette_for(THEME)
    ACCENT, ACCENT_2 = p["accent"], p["accent2"]
    BG, SURFACE, SURFACE_2 = p["bg"], p["surface"], p["surface2"]
    BORDER, HOVER, PRESSED = p["border"], p["hover"], p["pressed"]
    ALT, SEL = p["alt"], p["sel"]
    TEXT, MUTED = p["text"], p["muted"]

def build_qss():
    """Build the app stylesheet from the active palette."""
    return f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Segoe UI Variable Display', 'Segoe UI';
    font-size: 13px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 8px 16px;
    font-weight: 600;
    color: {TEXT};
}}
QPushButton:hover {{ background: {HOVER}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; background: {SURFACE}; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {ACCENT_2});
    border: none;
    color: white;
    padding: 9px 20px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #818cf8, stop:1 #a78bfa);
}}
QPushButton#primary:pressed {{ background: {ACCENT}; }}

QPushButton#chip {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 5px 16px;
    color: {MUTED};
    font-weight: 600;
}}
QPushButton#chip:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#chip:checked {{
    background: {SURFACE_2};
    border-color: {ACCENT};
    color: white;
}}

QPushButton#ghost {{
    background: transparent;
    border: none;
    color: {MUTED};
    padding: 8px 10px;
}}
QPushButton#ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; border-radius: 9px; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 9px 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:read-only {{ color: {MUTED}; }}
QLineEdit::placeholder {{ color: {MUTED}; }}

/* ---------- table ---------- */
QTableView {{
    background: {SURFACE};
    alternate-background-color: {ALT};
    border: 1px solid {BORDER};
    border-radius: 12px;
    gridline-color: transparent;
    outline: none;
}}
QTableView::item {{ padding: 0 10px; border: none; }}
QTableView::item:selected {{ background: {SEL}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    padding: 10px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 700;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 1px;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

/* ---------- left nav rail ---------- */
QFrame#sidebar {{
    background: {SURFACE};
    border: none;
    border-right: 1px solid {BORDER};
}}
QWidget#mainPane {{ background: {BG}; }}
QListWidget#nav {{
    background: transparent;
    border: none;
    outline: none;
    font-size: 13px;
}}
QListWidget#nav::item {{
    padding: 5px 8px;
    border-radius: 7px;
    color: {MUTED};
    margin: 0;
}}
QListWidget#nav::item:hover {{ background: {HOVER}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: {SEL}; color: {TEXT}; }}
QListWidget#nav::item:disabled {{
    /* section header: small, all-caps, muted, no hover, no selection */
    color: {MUTED};
    background: transparent;
    padding: 12px 8px 4px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}}

/* ---------- progress ---------- */
QProgressBar {{
    background: {BG};
    border: none;
    border-radius: 4px;
    text-align: center;
    color: transparent;
    max-height: 8px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {ACCENT}, stop:1 {ACCENT_2});
    border-radius: 4px;
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}

QToolTip {{
    background: {SURFACE_2}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 4px;
}}

/* ---------- splitter ---------- */
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QSplitter::handle:hover {{
    background: {ACCENT};
}}
"""


apply_theme("dark")   # default; DownloadApp re-applies the saved choice at startup


def human_size(n):
    if n <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_speed(bps):
    if bps <= 0:
        return ""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:.0f} {unit}" if unit == "B/s" else f"{bps:.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


def humanize_age(epoch):
    """Relative 'date added' text, e.g. '5 min ago', '2 days ago'."""
    if not epoch:
        return ""
    d = time.time() - epoch
    if d < 60:
        return "just now"
    if d < 3600:
        n = int(d // 60);  return f"{n} min ago"
    if d < 86400:
        n = int(d // 3600); return f"{n} hr ago" if n == 1 else f"{n} hrs ago"
    n = int(d // 86400);    return "1 day ago" if n == 1 else f"{n} days ago"


def fmt_eta(secs):
    """Time-left text from seconds; '' when unknown."""
    if not secs or secs <= 0 or secs == float("inf"):
        return ""
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _muted_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 600;"
                      "background: transparent;")
    return lbl


class TaskTableModel(QAbstractTableModel):
    """Model over the (already filtered) visible task list. Sorting + the
    two-line name rendering are handled by the proxy + NameDelegate; live
    progress/speed updates come through refresh_dynamic()."""
    COLS = ["File", "Size", "Status", "Speed", "Time Left", "Added"]
    SORT_ROLE = Qt.UserRole + 1
    TASK_ROLE = Qt.UserRole + 2

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tasks = []

    def set_tasks(self, tasks):
        tasks = list(tasks)
        if tasks != self.tasks:           # membership/order changed -> full reset
            self.beginResetModel()
            self.tasks = tasks
            self.endResetModel()

    def refresh_dynamic(self):
        """Repaint volatile columns (progress/speed/time-left/status) in place."""
        if self.tasks:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.tasks) - 1, len(self.COLS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tasks)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLS[section]
        return None

    def _eta_secs(self, t, bps):
        if t.status == T.DOWNLOADING and bps > 0 and t.total_size > 0:
            return max(0, (t.total_size - t.downloaded) / bps)
        return 0

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self.tasks[index.row()]
        col = index.column()
        if role == self.TASK_ROLE:
            return t
        bps = self.app._bps_for(t)
        if role == Qt.DisplayRole:
            if col == 0:
                return t.filename                       # painted by NameDelegate
            if col == 1:
                return human_size(t.total_size) if t.total_size else "—"
            if col == 2:
                return t.status
            if col == 3:
                return human_speed(bps) if t.status == T.DOWNLOADING else ""
            if col == 4:
                return fmt_eta(self._eta_secs(t, bps))
            if col == 5:
                return humanize_age(t.added)
        elif role == Qt.ForegroundRole and col == 2:
            return QColor(STATUS_COLORS.get(t.status, TEXT))
        elif role == Qt.TextAlignmentRole and col in (1, 3, 4):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == self.SORT_ROLE:
            if col == 0:
                return (t.filename or "").lower()
            if col == 1:
                return t.total_size or t.downloaded
            if col == 2:
                return t.status
            if col == 3:
                return bps
            if col == 4:
                return self._eta_secs(t, bps)
            if col == 5:
                return t.added or 0
        return None


class NameDelegate(QStyledItemDelegate):
    """File cell: category icon + filename, with a thin progress bar (active) or
    the category name (idle) on the second line — the ABDM-style two-line cell."""
    ICONS = {"Compressed": "📦", "Programs": "🧩", "Video": "🎬",
             "Music": "🎵", "Documents": "📄", "Other": "📁"}

    def sizeHint(self, option, index):
        return QSize(240, 48)

    def paint(self, painter, option, index):
        t = index.data(TaskTableModel.TASK_ROLE)
        if t is None:
            return super().paint(painter, option, index)
        r = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # row background (delegate owns the whole cell, so paint it ourselves)
        if option.state & QStyle.State_Selected:
            painter.fillRect(r, QColor(SEL))
        else:
            painter.fillRect(r, QColor(ALT if index.row() % 2 else SURFACE))

        cat = utils.category_for(t.filename)
        # icon
        painter.setPen(QColor(TEXT))
        icon_f = QFont(); icon_f.setPointSize(12)
        painter.setFont(icon_f)
        painter.drawText(QRect(r.left() + 12, r.top(), 26, r.height()),
                         int(Qt.AlignVCenter | Qt.AlignLeft), self.ICONS.get(cat, "📁"))

        tx = r.left() + 44
        tw = max(40, r.right() - tx - 10)

        # line 1 — filename
        name_f = QFont(); name_f.setPointSize(9); name_f.setBold(True)
        painter.setFont(name_f)
        painter.setPen(QColor(TEXT))
        name = painter.fontMetrics().elidedText(t.filename or "", Qt.ElideMiddle, tw)
        painter.drawText(QRect(tx, r.top() + 5, tw, 17),
                         int(Qt.AlignVCenter | Qt.AlignLeft), name)

        # line 2 — progress bar (active) or category
        sub_top = r.top() + 25
        if t.status in (T.DOWNLOADING, T.PAUSED) and t.total_size > 0:
            pct = max(0, min(100, t.percent))
            barw = max(20, tw - 48)
            bar = QRect(tx, sub_top + 4, barw, 6)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG))
            painter.drawRoundedRect(bar, 3, 3)
            if pct > 0:
                fill = QRect(bar.left(), bar.top(), int(bar.width() * pct / 100), bar.height())
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(fill, 3, 3)
            painter.setPen(QColor(MUTED))
            small = QFont(); small.setPointSize(8); painter.setFont(small)
            painter.drawText(QRect(bar.right() + 8, sub_top, 44, 16),
                             int(Qt.AlignVCenter | Qt.AlignLeft), f"{pct}%")
        else:
            painter.setPen(QColor(MUTED))
            sub_f = QFont(); sub_f.setPointSize(8); painter.setFont(sub_f)
            painter.drawText(QRect(tx, sub_top, tw, 16),
                             int(Qt.AlignVCenter | Qt.AlignLeft), cat)
        painter.restore()


class SpeedGraphWidget(QWidget):
    def __init__(self, parent=None, max_points=120):
        super().__init__(parent)
        self.max_points = max_points
        self.data = [0.0] * max_points
        self._ema = 0.0      # smoothed sample (the spiky 0.5s reads are noisy)
        self._peak = 1.0     # decaying Y-scale so one spike can't flatten the rest
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_value(self, val):
        # smooth toward sustained throughput rather than plotting each raw burst
        self._ema = val if self._ema <= 0 else 0.3 * val + 0.7 * self._ema
        self.data.append(self._ema)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        # peak decays ~6%/tick: the axis follows real sustained speed, and a lone
        # spike relaxes out instead of permanently squashing the baseline.
        self._peak = max(self._ema, self._peak * 0.94)
        self.update()

    def current(self):
        """Smoothed current speed (B/s) — used for the readout so it doesn't flicker."""
        return self._ema

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        max_val = self._peak * 1.15  # decaying peak + headroom (not the raw max)
        if max_val <= 0:
            max_val = 1

        path = QPainterPath()
        dx = w / (self.max_points - 1)
        
        for i, val in enumerate(self.data):
            x = i * dx
            y = h - (val / max_val) * h
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, 0, 0, h)
        c = QColor(ACCENT)
        gradient.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 100))
        gradient.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        
        painter.fillPath(fill_path, QBrush(gradient))

        pen = QPen(c)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)

# ====================================================================== dialogs
class FileInfoDialog(QDialog):
    """'Add Download' dialog — URL, optional category routing, destination.

    Shown when the browser hands over a download (URL fixed) and for the
    Add URL button (URL editable). Probes size/type in the background.

    Public interface consumed by ``DownloadApp._show_file_info``:
      * ``choice``   -> ``None`` | ``"now"`` | ``"later"``
      * ``values()`` -> ``(url, filename, folder)``
    """

    NO_CATEGORY = "General"

    def __init__(self, parent, url="", save_dir="", suggested="", headers=None):
        super().__init__(parent)
        self.setWindowTitle("Add Download")
        self.setMinimumWidth(520)
        self.choice = None                  # "now" | "later"
        self._probe_result = None
        self.headers = headers or {}       # browser cookies/UA for the probe
        editable = not url

        self.base_save_dir = save_dir or utils.default_download_dir()

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 16, 20, 16)

        # ---- header: gradient logo chip + title ----
        head = QHBoxLayout()
        head.setSpacing(10)
        logo = QLabel("⬇")
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            f"stop:0 {ACCENT}, stop:1 {ACCENT_2});"
            "border-radius: 8px; color: white; font-size: 15px; font-weight: 800;")
        head_title = QLabel("Add Download")
        head_title.setStyleSheet("font-size: 16px; font-weight: 700; background: transparent;")
        head.addWidget(logo)
        head.addWidget(head_title)
        head.addStretch()
        lay.addLayout(head)

        # ---- URL row (field + paste) ----
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(not editable)
        self.url_edit.setCursorPosition(0)
        if editable:
            self.url_edit.setPlaceholderText("https://example.com/file.zip")
            self.url_edit.editingFinished.connect(self._start_probe)
            self.url_edit.textChanged.connect(self._url_changed)
        url_row.addWidget(self.url_edit, 1)
        self.btn_paste = self._icon_button("📋", "Paste URL from clipboard")
        self.btn_paste.clicked.connect(self._paste_url)
        self.btn_paste.setVisible(editable)
        url_row.addWidget(self.btn_paste)
        lay.addLayout(url_row)

        # ---- category + size row ----
        cat_row = QHBoxLayout()
        cat_row.setSpacing(8)
        self.use_cat = QCheckBox("Use Category")
        self.use_cat.setChecked(True)
        self.use_cat.toggled.connect(self._apply_category)
        cat_row.addWidget(self.use_cat)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("▦  " + self.NO_CATEGORY, self.NO_CATEGORY)
        for name in utils.CATEGORIES:
            self.cat_combo.addItem("▦  " + name, name)
        self.cat_combo.currentIndexChanged.connect(self._apply_category)
        cat_row.addWidget(self.cat_combo, 1)

        self.btn_add_cat = self._icon_button("＋", "New category folder")
        self.btn_add_cat.clicked.connect(self._new_category)
        cat_row.addWidget(self.btn_add_cat)

        cat_row.addSpacing(8)
        self.size_lbl = QLabel("▦  …" if url else "▦  —")
        self.size_lbl.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")
        cat_row.addWidget(self.size_lbl)
        lay.addLayout(cat_row)

        # ---- destination row (folder + browse/menu/refresh/settings) ----
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dir_edit = QLineEdit(self.base_save_dir)
        dir_row.addWidget(self.dir_edit, 1)
        self.btn_browse = self._icon_button("📁", "Choose folder")
        self.btn_browse.clicked.connect(self._browse)
        dir_row.addWidget(self.btn_browse)
        self.btn_dirmenu = self._icon_button("▾", "Quick folders")
        self.btn_dirmenu.clicked.connect(self._show_dir_menu)
        dir_row.addWidget(self.btn_dirmenu)
        dir_row.addSpacing(8)
        self.btn_refresh = self._icon_button("⟳", "Re-check file size")
        self.btn_refresh.clicked.connect(self._start_probe)
        dir_row.addWidget(self.btn_refresh)
        self.btn_gear = self._icon_button("⚙", "Settings")
        self.btn_gear.clicked.connect(self._open_settings)
        dir_row.addWidget(self.btn_gear)
        lay.addLayout(dir_row)

        # ---- filename row ----
        self.fname = QLineEdit(suggested)
        self.fname.setPlaceholderText("auto-detected from URL")
        lay.addWidget(self.fname)

        # ---- action buttons: Add / Download ............ Cancel ----
        brow = QHBoxLayout()
        self.btn_later = QPushButton("Download Later")
        self.btn_later.setToolTip("Add to the list but don't start yet")
        self.btn_now = QPushButton("⬇  Download")
        self.btn_now.setToolTip("Start downloading now")
        self.btn_now.setObjectName("primary")
        btn_cancel = QPushButton("Cancel")
        self.btn_later.clicked.connect(lambda: self._done("later"))
        self.btn_now.clicked.connect(lambda: self._done("now"))
        btn_cancel.clicked.connect(self.reject)
        brow.addWidget(self.btn_later)
        brow.addWidget(self.btn_now)
        brow.addStretch()
        brow.addWidget(btn_cancel)
        lay.addLayout(brow)

        # initial wiring
        self._apply_category()
        if suggested:
            self._select_category_for(suggested)

        if editable:
            self.btn_now.setEnabled(False)
            self.btn_later.setEnabled(False)
            self._maybe_prefill_clipboard()
        else:
            self._start_probe()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_probe)
        self._timer.start(150)

    # ---- small helpers ----
    def _icon_button(self, glyph, tip=""):
        b = QPushButton(glyph)
        b.setObjectName("ghost")
        b.setFixedSize(38, 38)
        if tip:
            b.setToolTip(tip)
        return b

    def _url_changed(self, text):
        ok = text.strip().lower().startswith(("http://", "https://"))
        self.btn_now.setEnabled(ok)
        self.btn_later.setEnabled(ok)

    def _paste_url(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)
            self.url_edit.setCursorPosition(0)
            self._start_probe()

    def _maybe_prefill_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if not self.url_edit.text() and text.lower().startswith(("http://", "https://")):
            self.url_edit.setText(text)
            self.url_edit.setCursorPosition(0)
            self._start_probe()

    # ---- category routing ----
    def _apply_category(self, *_):
        on = self.use_cat.isChecked()
        self.cat_combo.setEnabled(on)
        self.btn_add_cat.setEnabled(on)
        cat = self.cat_combo.currentData() if on else None
        if cat and cat != self.NO_CATEGORY:
            self.dir_edit.setText(os.path.join(self.base_save_dir, cat))
        else:
            self.dir_edit.setText(self.base_save_dir)

    def _select_category_for(self, filename):
        """Auto-pick the category whose extension set matches ``filename``."""
        ext = os.path.splitext(filename)[1].lower()
        target = self.NO_CATEGORY
        for name, exts in utils.CATEGORIES.items():
            if ext in exts:
                target = name
                break
        idx = self.cat_combo.findData(target)
        if idx >= 0:
            self.cat_combo.setCurrentIndex(idx)

    def _new_category(self):
        name, ok = QInputDialog.getText(self, "New Category", "Folder name:")
        name = utils.safe_filename((name or "").strip()) if ok else ""
        if not name or name == "download":
            return
        if self.cat_combo.findData(name) < 0:
            self.cat_combo.addItem("▦  " + name, name)
        self.use_cat.setChecked(True)
        self.cat_combo.setCurrentIndex(self.cat_combo.findData(name))

    # ---- destination ----
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to folder",
                                             self.dir_edit.text())
        if d:
            self.use_cat.setChecked(False)   # an explicit folder overrides category
            self.dir_edit.setText(d)

    def _set_dir(self, path):
        self.dir_edit.setText(path)

    def _show_dir_menu(self):
        menu = QMenu(self)
        menu.addAction("📂  " + self.base_save_dir,
                       lambda: self._set_dir(self.base_save_dir))
        for i in range(self.cat_combo.count()):
            name = self.cat_combo.itemData(i)
            if name and name != self.NO_CATEGORY:
                path = os.path.join(self.base_save_dir, name)
                menu.addAction("📁  " + name, lambda p=path: self._set_dir(p))
        menu.exec(self.btn_dirmenu.mapToGlobal(self.btn_dirmenu.rect().bottomLeft()))

    def _open_settings(self):
        p = self.parent()
        if p is not None and hasattr(p, "on_settings"):
            p.on_settings()
            new_base = getattr(p, "save_dir", self.base_save_dir)
            if new_base and new_base != self.base_save_dir:
                self.base_save_dir = new_base
                self._apply_category()

    # ---- size probe ----
    def _start_probe(self):
        url = self.url_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            return
        self.size_lbl.setText("▦  Probing…")

        def work(u=url):
            from downloader import probe_info
            self._probe_result = probe_info(u, self.headers)
        threading.Thread(target=work, daemon=True).start()

    def _poll_probe(self):
        if self._probe_result is None:
            return
        info, self._probe_result = self._probe_result, None
        self.size_lbl.setText("▦  " + (human_size(info["size"]) if info["size"]
                                       else "Unknown size"))
        self.size_lbl.setToolTip(info["type"] or "")
        if not self.fname.text():
            fname = utils.filename_from_url(self.url_edit.text().strip())
            self.fname.setPlaceholderText(fname)
            if self.use_cat.isChecked():
                self._select_category_for(fname)

    def _done(self, choice):
        self.choice = choice
        folder = self.dir_edit.text().strip()
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
        self.accept()

    def values(self):
        return (self.url_edit.text().strip(), self.fname.text().strip(),
                self.dir_edit.text().strip())


class DownloadCompleteDialog(QDialog):
    """Modeless 'Download Completed' popup.

    Raised by ``DownloadApp`` when a task finishes during this session.
    Open / Open Folder / Copy link / Close.
    """

    def __init__(self, parent, task):
        super().__init__(parent)
        self.task = task
        self.setWindowTitle(task.filename or "Download Completed")
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(22, 20, 22, 18)

        row = QHBoxLayout()
        row.setSpacing(14)
        badge = QLabel("▦")
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background: {SURFACE_2}; border: 1px solid {BORDER};"
            "border-radius: 12px; font-size: 22px;")
        row.addWidget(badge)

        info = QVBoxLayout()
        info.setSpacing(3)
        done = QLabel("✓  Download Completed")
        done.setStyleSheet(
            f"color: {STATUS_COLORS[T.DOWNLOADING]}; font-size: 16px;"
            "font-weight: 800; background: transparent;")
        size = human_size(task.total_size or task.downloaded)
        meta = QLabel(f"{size}    ·    {task.filename}")
        meta.setStyleSheet(f"color: {MUTED}; background: transparent;")
        meta.setWordWrap(True)
        info.addWidget(done)
        info.addWidget(meta)
        row.addLayout(info, 1)
        lay.addLayout(row)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.btn_open = QPushButton("Open")
        self.btn_open.setObjectName("primary")
        self.btn_open.clicked.connect(self._open_file)
        self.btn_folder = QPushButton("Open Folder")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_copy = QPushButton("🔗")
        self.btn_copy.setObjectName("ghost")
        self.btn_copy.setFixedSize(38, 38)
        self.btn_copy.setToolTip("Copy download link")
        self.btn_copy.clicked.connect(self._copy_link)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        brow.addWidget(self.btn_open)
        brow.addWidget(self.btn_folder)
        brow.addWidget(self.btn_copy)
        brow.addStretch()
        brow.addWidget(btn_close)
        lay.addLayout(brow)

        exists = os.path.exists(task.save_path)
        self.btn_open.setEnabled(exists)
        self.btn_folder.setEnabled(exists)

    def _open_file(self):
        try:
            os.startfile(self.task.save_path)
        except OSError:
            pass

    def _open_folder(self):
        path = os.path.normpath(self.task.save_path)
        try:
            subprocess.Popen(["explorer", "/select,", path])
        except OSError:
            try:
                os.startfile(os.path.dirname(path))
            except OSError:
                pass

    def _copy_link(self):
        QApplication.clipboard().setText(self.task.url or "")


class PropertiesDialog(QDialog):
    """Read-only properties of an existing download (double-click a row)."""

    def __init__(self, parent, t: "T.DownloadTask"):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {t.filename}")
        self.setMinimumWidth(560)
        self.t = t

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(22, 20, 22, 18)

        title = QLabel(t.filename)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        title.setWordWrap(True)
        lay.addWidget(title)

        color = STATUS_COLORS.get(t.status, TEXT)
        pill = QLabel(t.status)
        c = QColor(color)
        pill.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},0.16);"
            f"color: {color}; border-radius: 10px; padding: 3px 12px;"
            "font-weight: 700; font-size: 11px;")
        pill.setFixedHeight(22)
        prow = QHBoxLayout()
        prow.addWidget(pill)
        prow.addStretch()
        lay.addLayout(prow)

        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(18)
        rows = [
            ("URL", t.url),
            ("Save path", t.save_path),
            ("Size", f"{human_size(t.downloaded)} of {human_size(t.total_size)}"
                     f"  ({t.percent}%)"),
            ("Segments", str(len(t.segments) or "—")),
            ("Range support", "Yes" if t.supports_range else
             ("No" if t.segments else "Unknown")),
            ("Speed limit", f"{t.speed_limit // 1024} KB/s" if t.speed_limit > 0 else "Unlimited"),
        ]
        if t.error:
            rows.append(("Error", t.error))
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_muted_label(k), i, 0, Qt.AlignTop)
            val = QLabel(v)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setStyleSheet("background: transparent;")
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        brow = QHBoxLayout()
        btn_file = QPushButton("Open File")
        btn_folder = QPushButton("Open Folder")
        btn_copy = QPushButton("Copy URL")
        btn_close = QPushButton("Close")
        btn_close.setObjectName("primary")
        btn_file.setEnabled(t.status == T.COMPLETED and os.path.exists(t.save_path))
        btn_file.clicked.connect(self._open_file)
        btn_folder.clicked.connect(self._open_folder)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(t.url))
        btn_close.clicked.connect(self.accept)
        brow.addWidget(btn_file)
        brow.addWidget(btn_folder)
        brow.addWidget(btn_copy)
        brow.addStretch()
        brow.addWidget(btn_close)
        lay.addLayout(brow)

    def _open_file(self):
        try:
            os.startfile(self.t.save_path)
        except OSError:
            pass

    def _open_folder(self):
        folder = os.path.dirname(self.t.save_path) or "."
        if os.path.exists(self.t.save_path):
            subprocess.Popen(["explorer", "/select,", self.t.save_path])
        else:
            try:
                os.startfile(folder)
            except OSError:
                pass


class SettingsDialog(QDialog):
    def __init__(self, parent, save_dir, max_concurrent, segments,
                 verify_tls=True, pair_token="", theme="dark"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(22, 20, 22, 18)

        title = QLabel("⚙  Settings")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        lay.addWidget(title)

        lay.addWidget(_muted_label("Default download folder"))
        drow = QHBoxLayout()
        self.dir_edit = QLineEdit(save_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        drow.addWidget(self.dir_edit, 1)
        drow.addWidget(browse)
        lay.addLayout(drow)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.addWidget(_muted_label("Concurrent downloads"), 0, 0)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 10)
        self.concurrent.setValue(max_concurrent)
        grid.addWidget(self.concurrent, 0, 1)
        grid.addWidget(_muted_label("Connections per download"), 1, 0)
        self.segments = QSpinBox()
        self.segments.setRange(1, 16)
        self.segments.setValue(segments)
        grid.addWidget(self.segments, 1, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        note = _muted_label("More connections download large files faster — 8 is a "
                            "good default. New values apply to downloads started afterwards.")
        note.setWordWrap(True)
        lay.addWidget(note)

        # ---- appearance section ----
        sep_a = QFrame()
        sep_a.setFrameShape(QFrame.HLine)
        sep_a.setStyleSheet(f"color: {BORDER};")
        lay.addWidget(sep_a)
        lay.addWidget(QLabel("🎨  Appearance"))
        arow = QHBoxLayout()
        arow.addWidget(_muted_label("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.setCurrentText("Light" if theme == "light" else "Dark")
        arow.addWidget(self.theme_combo)
        arow.addStretch()
        lay.addLayout(arow)

        # ---- security section ----
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        lay.addWidget(sep)
        lay.addWidget(QLabel("🔒  Security"))

        self.verify_chk = QCheckBox("Verify HTTPS certificates (recommended)")
        self.verify_chk.setChecked(verify_tls)
        lay.addWidget(self.verify_chk)
        warn = _muted_label("Only turn off for trusted hosts with self-signed "
                            "certificates. Off = vulnerable to interception.")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        lay.addWidget(_muted_label("Browser extension pairing token"))
        trow = QHBoxLayout()
        self.token_edit = QLineEdit(pair_token)
        self.token_edit.setReadOnly(True)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self.token_edit.text()))
        trow.addWidget(self.token_edit, 1)
        trow.addWidget(copy_btn)
        lay.addLayout(trow)
        tnote = _muted_label("Paste this into the extension popup once to let it "
                             "send downloads to this app.")
        tnote.setWordWrap(True)
        lay.addWidget(tnote)

        brow = QHBoxLayout()
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        brow.addStretch()
        brow.addWidget(cancel)
        brow.addWidget(ok)
        lay.addLayout(brow)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Default download folder",
                                             self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def values(self):
        theme = "light" if self.theme_combo.currentText() == "Light" else "dark"
        return (self.dir_edit.text().strip(), self.concurrent.value(),
                self.segments.value(), self.verify_chk.isChecked(), theme)


# ====================================================================== main app
class DownloadApp(QWidget):
    COLS = ["File", "Size", "Progress", "Speed", "Status"]
    FILTERS = ["All", "Active", "Paused", "Done"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HyperFetch")
        self.setMinimumSize(900, 540)
        for ic in (resource_path("assets", "icon.ico"),
                   resource_path("assets", "icon.png")):
            if os.path.exists(ic):
                self.setWindowIcon(QIcon(ic))
                break

        self._settings_path = os.path.join(utils.app_data_dir(), "settings.json")
        self._state_path = os.path.join(utils.app_data_dir(), "downloads.json")
        self._load_settings()

        self.queue = QueueManager(max_concurrent=self.max_concurrent,
                                  segments=self.segments)
        self.pending = deque()  # filled by the embedded Flask server
        self._speed = {}        # task.id -> (last_downloaded, last_time, bps)
        self._rows = {}         # task.id -> row index
        self._filter = "All"
        self._search = ""
        self._dialog_open = False
        self._save_tick = 0
        self._completed_seen = None   # seeded on first refresh; tracks done IDs
        self._complete_popup = None   # the live "Download Completed" popup
        self._flash_text = ""         # transient status-bar message
        self._flash_until = 0.0

        self._build_ui()
        self._load_state()
        self._start_server()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    # ------------------------------------------------------------- settings/state
    def _load_settings(self):
        s = utils.load_json(self._settings_path, {})
        self.save_dir = s.get("save_dir") or utils.default_download_dir()
        if not os.path.isdir(self.save_dir):
            self.save_dir = utils.default_download_dir()
        self.max_concurrent = int(s.get("max_concurrent", MAX_CONCURRENT))
        self.segments = int(s.get("segments", SEGMENTS))
        self.global_speed_limit = int(s.get("global_speed_limit", 0))
        utils.global_limiter.set_limit(self.global_speed_limit)
        # security
        self.verify_tls = bool(s.get("verify_tls", True))
        utils.VERIFY_TLS = self.verify_tls
        self.theme = s.get("theme", "dark")
        apply_theme(self.theme)
        self.pair_token = utils.get_or_create_token()
        # per-column widths the user dragged to last time (col-index -> px).
        # Stretched col 0 (File) is excluded; only the fixed-default cols persist.
        self.column_widths = s.get("column_widths") or {}

    def _save_settings(self):
        widths = {}
        if hasattr(self, "table"):
            h = self.table.horizontalHeader()
            for c in range(1, self.model.columnCount()):
                widths[str(c)] = h.sectionSize(c)
        utils.save_json(self._settings_path, {
            "save_dir": self.save_dir,
            "max_concurrent": self.max_concurrent,
            "segments": self.segments,
            "global_speed_limit": getattr(self, "global_speed_limit", 0),
            "verify_tls": getattr(self, "verify_tls", True),
            "theme": getattr(self, "theme", "dark"),
            "column_widths": widths or self.column_widths,
        })

    def _load_state(self):
        for d in utils.load_json(self._state_path, []):
            try:
                t = T.DownloadTask.from_dict(d)
            except (KeyError, TypeError, ValueError):
                continue
            self.queue.add_task(t, start=False)

    def _save_state(self):
        keep = [t.to_dict() for t in self.queue.tasks
                if t.status != T.CANCELLED]
        utils.save_json(self._state_path, keep)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        self.setStyleSheet(build_qss())
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- left nav rail ----
        root.addWidget(self._build_sidebar())

        # ---- main pane ----
        main = QWidget()
        main.setObjectName("mainPane")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(18, 14, 18, 10)
        main_layout.setSpacing(12)

        # ---- centered app title ----
        header = QLabel("⚡ HyperFetch")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 16px; font-weight: 800;")
        main_layout.addWidget(header)

        # ---- toolbar ----
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_add = QPushButton("＋  New Download")
        self.btn_add.setObjectName("primary")
        self.btn_resume = QPushButton("▶  Resume")
        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_cancel = QPushButton("✕  Delete")
        for b in (self.btn_add, self.btn_resume, self.btn_pause, self.btn_cancel):
            bar.addWidget(b)

        bar.addStretch()

        # search box (filters the list by filename)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the list")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)

        self.limit_combo = QComboBox()
        self.limit_combo.addItems(["Unlimited", "100 KB/s", "500 KB/s", "1 MB/s", "5 MB/s", "10 MB/s"])
        limit_txt = "Unlimited"
        if getattr(self, "global_speed_limit", 0) > 0:
            mb = self.global_speed_limit / (1024*1024)
            limit_txt = f"{int(mb)} MB/s" if mb >= 1 else f"{int(self.global_speed_limit / 1024)} KB/s"
        self.limit_combo.setCurrentText(limit_txt)
        self.limit_combo.currentTextChanged.connect(self._on_global_limit_changed)
        bar.addWidget(_muted_label("Limit:"))
        bar.addWidget(self.limit_combo)

        self.btn_more = QPushButton("⋯")
        self.btn_more.setObjectName("ghost")
        self.btn_more.setToolTip("Bulk actions — pause / resume / cancel / clear all")
        self.btn_clear = QPushButton("🗑")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setToolTip("Clear finished downloads from the list")
        self.btn_open = QPushButton("📂")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setToolTip("Open download folder")
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setToolTip("Settings")
        for b in (self.btn_more, self.btn_clear, self.btn_open, self.btn_settings):
            bar.addWidget(b)
        main_layout.addLayout(bar)

        # ---- table (model/view: sortable, delegate-painted name cell) ----
        self.model = TaskTableModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(TaskTableModel.SORT_ROLE)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        self.table.setItemDelegateForColumn(0, NameDelegate(self.table))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        # File stays stretchable (eats the leftover); other cols are user-draggable.
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setStretchLastSection(False)
        DEFAULTS = {1: 120, 2: 120, 3: 110, 4: 110, 5: 130}
        for c, default in DEFAULTS.items():
            h.setSectionResizeMode(c, QHeaderView.Interactive)
            saved = self.column_widths.get(str(c)) if hasattr(self, "column_widths") else None
            self.table.setColumnWidth(c, int(saved) if saved else default)
        # persist any width the user drags
        h.sectionResized.connect(lambda *_: self._mark_settings_dirty())
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.doubleClicked.connect(self._on_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_action_buttons())
        self.table.sortByColumn(5, Qt.DescendingOrder)   # newest first, like ABDM
        main_layout.addWidget(self.table)

        # ---- empty state ----
        self.empty = QLabel("No downloads yet.\nAdd a URL or grab one from the browser.",
                            self.table)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {MUTED}; font-size: 14px; background: transparent;")

        # ---- bottom status bar ----
        status = QHBoxLayout()
        status.setSpacing(12)
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.speed_graph = SpeedGraphWidget()
        self.speed_graph.setFixedSize(110, 22)
        self.total_speed = QLabel()
        self.total_speed.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {STATUS_COLORS[T.DOWNLOADING]};")
        status.addWidget(self.status_lbl)
        status.addStretch()
        status.addWidget(self.subtitle)
        status.addWidget(self.speed_graph)
        status.addWidget(self.total_speed)
        main_layout.addLayout(status)

        root.addWidget(main, 1)

        self.btn_add.clicked.connect(self.on_add)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_resume.clicked.connect(self.on_resume)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_more.clicked.connect(self._show_bulk_menu)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_open.clicked.connect(self.on_open)
        self.btn_settings.clicked.connect(self.on_settings)
        self._update_action_buttons()

    # ---- left nav rail (categories + status groups) ----
    # icon + label + filter-key. None entry = section header (non-selectable).
    NAV_ITEMS = [
        ("📁",  "All",        "All"),
        None,                                       # ── Categories ──
        ("📦", "Compressed",  "Compressed"),
        ("🧩", "Programs",    "Programs"),
        ("🎬", "Videos",      "Video"),
        ("🎵", "Music",       "Music"),
        ("📄", "Documents",   "Documents"),
        ("🗂", "Other",       "Other"),
        None,                                       # ── Queues ──
        ("⛓", "Main",        "Queue:Main"),
        None,                                       # ── Status ──
        ("⏳", "Unfinished",  "Unfinished"),
        ("✓",  "Finished",    "Finished"),
    ]
    SECTION_TITLES = ["Categories", "Queues", "Status"]

    def _build_sidebar(self):
        rail = QFrame()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(200)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(10, 16, 10, 14)
        lay.setSpacing(8)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setUniformItemSizes(False)

        # the items will end up as: header, items, header, items, ...
        # — headers are non-selectable + styled via NavItemRole below.
        self._nav_count_items = {}     # filter key -> QListWidgetItem (for counts)
        section_iter = iter(self.SECTION_TITLES)
        for entry in self.NAV_ITEMS:
            if entry is None:
                hdr = QListWidgetItem(next(section_iter, "").upper())
                hdr.setFlags(Qt.NoItemFlags)        # non-selectable section header
                hdr.setData(Qt.UserRole + 1, "header")
                self.nav.addItem(hdr)
                continue
            icon, label, key = entry
            it = QListWidgetItem(f"  {icon}   {label}")
            it.setData(Qt.UserRole, key)
            self.nav.addItem(it)
            self._nav_count_items[key] = it
        self.nav.setCurrentRow(0)
        self.nav.currentItemChanged.connect(self._on_nav)
        lay.addWidget(self.nav, 1)
        return rail

    def _on_nav(self, current, _previous):
        if current is None:
            return
        key = current.data(Qt.UserRole)
        if not key:                # section header — ignore
            return
        self._set_filter(key)

    def _refresh_nav_counts(self):
        """Live per-item counts shown right-aligned, like ABDM's '0'."""
        # one pass over tasks per tick, cheap.
        tasks = list(self.queue.tasks)
        by_cat = {}
        for t in tasks:
            by_cat[utils.category_for(t.filename)] = by_cat.get(utils.category_for(t.filename), 0) + 1
        unfinished = sum(1 for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED))
        finished = sum(1 for t in tasks if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED))
        counts = {
            "All": len(tasks),
            "Compressed": by_cat.get("Compressed", 0),
            "Programs": by_cat.get("Programs", 0),
            "Video": by_cat.get("Video", 0),
            "Music": by_cat.get("Music", 0),
            "Documents": by_cat.get("Documents", 0),
            "Other": by_cat.get("Other", 0),
            "Queue:Main": len(tasks),       # one queue today; mirror the total
            "Unfinished": unfinished,
            "Finished": finished,
        }
        labels = {k: (icon, label) for (icon, label, k) in (e for e in self.NAV_ITEMS if e)}
        for key, item in self._nav_count_items.items():
            n = counts.get(key, 0)
            icon, label = labels[key]
            item.setText(f"  {icon}   {label}" + (f"   · {n}" if n else ""))

    def _on_search(self, text):
        self._search = text.strip().lower()
        self.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "empty"):
            self.empty.setGeometry(self.table.rect())

    def _start_server(self):
        def serve():
            try:
                run_server(self.queue, self.save_dir, PORT, pending=self.pending,
                           token=self.pair_token)
            except OSError as e:
                self._server_err = str(e)
        self._server_err = ""
        threading.Thread(target=serve, daemon=True).start()

    # ---------------------------------------------------------------- helpers
    def _set_filter(self, name):
        self._filter = name
        self.refresh()

    def _visible_tasks(self):
        tasks = list(self.queue.tasks)
        f = self._filter
        if f and f.startswith("Queue:"):
            # only one queue today; this node mirrors "All" but is the entry-point
            # for future per-queue filtering once multi-queue support lands.
            pass
        elif f == "Active":
            tasks = [t for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED)]
        elif f == "Paused":
            tasks = [t for t in tasks if t.status == T.PAUSED]
        elif f in ("Done", "Finished"):
            tasks = [t for t in tasks if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED)]
        elif f == "Unfinished":
            tasks = [t for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED)]
        elif f in utils.CATEGORIES or f == "Other":
            tasks = [t for t in tasks if utils.category_for(t.filename) == f]
        q = getattr(self, "_search", "")
        if q:
            tasks = [t for t in tasks if q in (t.filename or "").lower()]
        return tasks

    def _bps_for(self, t):
        """Current smoothed speed (B/s) for a task, read by the table model."""
        return self._speed.get(t.id, (0, 0, 0.0))[2]

    def _task_at(self, proxy_index):
        """Map a view (proxy) index to its task, or None."""
        if not proxy_index.isValid():
            return None
        row = self.proxy.mapToSource(proxy_index).row()
        return self.model.tasks[row] if 0 <= row < len(self.model.tasks) else None

    def _selected_tasks(self):
        """Tasks for every selected row (multi-select aware)."""
        sm = self.table.selectionModel()
        if not sm:
            return []
        out = []
        for idx in sm.selectedRows():
            t = self._task_at(idx)
            if t is not None:
                out.append(t)
        return out

    def _update_action_buttons(self):
        """Enable Pause/Resume/Cancel only when the selection has a valid target."""
        ts = self._selected_tasks()
        self.btn_pause.setEnabled(any(t.status in (T.DOWNLOADING, T.QUEUED) for t in ts))
        self.btn_resume.setEnabled(any(t.status in (T.PAUSED, T.ERROR) for t in ts))
        self.btn_cancel.setEnabled(
            any(t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED) for t in ts))

    def _mark_settings_dirty(self):
        """Debounced settings save — the next refresh tick (within ~10s) writes
        out widths/options the user just changed. Cheap to call from a header
        resize without writing the JSON on every pixel of drag."""
        self._settings_dirty = True

    def _flash(self, msg, secs=2.5):
        """Show a transient one-line message in the status bar."""
        self._flash_text = msg
        self._flash_until = time.time() + secs
        self.status_lbl.setText(self._status_text())

    # ---------------------------------------------------------------- dialogs
    def _show_file_info(self, url="", suggested="", headers=None, flash=False):
        """Run the IDM-style dialog and queue the result.

        ``flash`` (browser-pushed downloads) blinks the dialog to the front so a
        download sent from the extension grabs attention even if the app is in
        the background."""
        self._dialog_open = True
        try:
            dlg = FileInfoDialog(self, url, self.save_dir, suggested, headers)
            if flash:
                # browser-pushed: float above every other window and pull the app
                # to the foreground so the dialog "captures the screen" — the user
                # shouldn't have to switch to the app to see it.
                dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
                self._bring_to_front()
                self._flash_dialog(dlg)
            if dlg.exec() != QDialog.Accepted or not dlg.choice:
                return
            final_url, fname, folder = dlg.values()
            if not final_url:
                return
            if not os.path.isdir(folder):
                folder = self.save_dir
            filename = utils.filename_from_url(final_url, fname or suggested)
            save_path = utils.unique_path(folder, filename)
            t = T.DownloadTask(final_url, save_path,
                               filename=filename, headers=headers)
            if dlg.choice == "later":
                t.status = T.PAUSED
                self.queue.add_task(t, start=False)
            else:
                self.queue.add_task(t)
            self._save_state()
        finally:
            self._dialog_open = False

    def _bring_to_front(self):
        """Restore + foreground the main window so a browser-pushed dialog is
        seen even if the app was minimized or behind other windows."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _flash_dialog(self, dlg):
        """Blink a browser-pushed dialog to the front + flash the taskbar so a
        download sent from the extension is noticed even when the app is hidden."""
        self.raise_()
        self.activateWindow()
        QApplication.alert(self, 2000)
        QTimer.singleShot(0, lambda: self._pulse_dialog(dlg, 0))

    def _pulse_dialog(self, dlg, n):
        try:
            if not dlg.isVisible() or n >= 6:
                dlg.setWindowOpacity(1.0)
                return
            dlg.setWindowOpacity(0.65 if n % 2 else 1.0)
            dlg.raise_()
            dlg.activateWindow()
        except RuntimeError:
            return  # dialog already closed
        QTimer.singleShot(120, lambda: self._pulse_dialog(dlg, n + 1))

    def _check_completions(self):
        """Pop a 'Download Completed' card when a task finishes this session."""
        current = {t.id for t in self.queue.tasks if t.status == T.COMPLETED}
        if self._completed_seen is None:
            self._completed_seen = current      # don't notify for preexisting
            return
        new_done = current - self._completed_seen
        self._completed_seen = current
        if not new_done:
            return
        # show the latest completion; avoid stacking popups when several finish
        task = next((t for t in self.queue.tasks if t.id in new_done), None)
        if task is not None:
            self._show_complete_popup(task)

    def _show_complete_popup(self, task):
        if self._complete_popup is not None:
            try:
                self._complete_popup.close()
            except RuntimeError:
                pass  # already destroyed by Qt
        dlg = DownloadCompleteDialog(self, task)
        self._complete_popup = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ---------------------------------------------------------------- actions
    def on_add(self):
        self._show_file_info()

    def on_pause(self):
        ts = [t for t in self._selected_tasks()
              if t.status in (T.DOWNLOADING, T.QUEUED)]
        if not ts:
            self._flash("Select a downloading or queued item to pause.")
            return
        for t in ts:
            self.queue.pause_task(t)
        self._flash(f"Paused {len(ts)} download(s).")
        self.refresh()

    def on_resume(self):
        ts = [t for t in self._selected_tasks()
              if t.status in (T.PAUSED, T.ERROR)]
        if not ts:
            self._flash("Select a paused or errored item to resume.")
            return
        for t in ts:
            self.queue.resume_task(t)
        self._flash(f"Resumed {len(ts)} download(s).")
        self.refresh()

    def on_cancel(self):
        ts = [t for t in self._selected_tasks()
              if t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED)]
        if not ts:
            self._flash("Select an active item to cancel.")
            return
        for t in ts:
            self.queue.cancel_task(t)
        self._flash(f"Cancelled {len(ts)} download(s).")
        self.refresh()

    # ---- bulk actions (overflow menu) ----
    def _show_bulk_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}"
            f"QMenu::item:selected {{ background: {HOVER}; }}")
        menu.addAction("⏸  Pause all", self._pause_all)
        menu.addAction("▶  Resume all", self._resume_all)
        menu.addAction("✕  Cancel all", self._cancel_all)
        menu.addSeparator()
        menu.addAction("🗑  Clear all", self._clear_all)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def _pause_all(self):
        ts = [t for t in self.queue.tasks if t.status in (T.DOWNLOADING, T.QUEUED)]
        for t in ts:
            self.queue.pause_task(t)
        self._flash(f"Paused {len(ts)} download(s)." if ts else "Nothing to pause.")
        self.refresh()

    def _resume_all(self):
        ts = [t for t in self.queue.tasks if t.status in (T.PAUSED, T.ERROR)]
        for t in ts:
            self.queue.resume_task(t)
        self._flash(f"Resumed {len(ts)} download(s)." if ts else "Nothing to resume.")
        self.refresh()

    def _cancel_all(self):
        ts = [t for t in self.queue.tasks
              if t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED)]
        if not ts:
            self._flash("Nothing to cancel.")
            return
        if QMessageBox.question(
                self, "Cancel all", f"Cancel {len(ts)} active download(s)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for t in ts:
            self.queue.cancel_task(t)
        self._flash(f"Cancelled {len(ts)} download(s).")
        self.refresh()

    def _clear_all(self):
        if not self.queue.tasks:
            return
        if QMessageBox.question(
                self, "Clear all",
                "Remove ALL downloads from the list? Active ones are cancelled.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.queue.clear_all()
        self._save_state()
        self._flash("Cleared all downloads.")
        self.refresh()

    def _move_task(self, task, where):
        self.queue.move(task, where)
        self.refresh()

    def _apply_theme(self, name):
        """Switch palette at runtime and repaint everything."""
        apply_theme(name)
        self.theme = THEME
        self.setStyleSheet(build_qss())
        # one-shot inline styles on persistent header/footer widgets
        self.subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.total_speed.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {STATUS_COLORS[T.DOWNLOADING]};")
        self.status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.empty.setStyleSheet(
            f"color: {MUTED}; font-size: 14px; background: transparent;")
        self.speed_graph.update()
        self.refresh()

    def on_clear(self):
        self.queue.remove_finished()
        self._save_state()
        self.refresh()

    def on_open(self):
        try:
            os.startfile(self.save_dir)
        except OSError:
            subprocess.Popen(["explorer", self.save_dir])

    def on_settings(self):
        dlg = SettingsDialog(self, self.save_dir, self.max_concurrent, self.segments,
                             verify_tls=self.verify_tls, pair_token=self.pair_token,
                             theme=self.theme)
        if dlg.exec() != QDialog.Accepted:
            return
        d, conc, segs, verify, theme = dlg.values()
        if os.path.isdir(d):
            self.save_dir = d
        self.max_concurrent = conc
        self.segments = segs
        self.queue.max_concurrent = conc
        self.queue.segments = segs
        self.verify_tls = verify
        utils.VERIFY_TLS = verify
        if theme != self.theme:
            self._apply_theme(theme)
        self._save_settings()

    def _on_double_clicked(self, index):
        t = self._task_at(index)
        if t is not None:
            PropertiesDialog(self, t).exec()

    def _on_global_limit_changed(self, text):
        if "Unlimited" in text:
            bps = 0
        elif "KB/s" in text:
            bps = int(text.split()[0]) * 1024
        elif "MB/s" in text:
            bps = int(text.split()[0]) * 1024 * 1024
        else:
            bps = 0
        self.global_speed_limit = bps
        utils.global_limiter.set_limit(bps)
        self._save_tick = 10

    def _on_context_menu(self, pos):
        t = self._task_at(self.table.indexAt(pos))
        if not t: return

        menu = QMenu(self.table)
        menu.setStyleSheet(f"QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}"
                           f"QMenu::item:selected {{ background: {HOVER}; }}")
        
        limit_menu = menu.addMenu("Set Speed Limit...")
        
        actions = [
            ("Unlimited", 0),
            ("100 KB/s", 100 * 1024),
            ("500 KB/s", 500 * 1024),
            ("1 MB/s", 1024 * 1024),
            ("5 MB/s", 5 * 1024 * 1024),
            ("Custom...", -1)
        ]
        
        for name, bps in actions:
            act = limit_menu.addAction(name)
            act.setCheckable(True)
            if bps == t.speed_limit or (bps == -1 and t.speed_limit not in [a[1] for a in actions[:-1]]):
                act.setChecked(True)
            
            act.triggered.connect(lambda checked=False, val=bps, task=t: self._set_task_limit(task, val))
            
        if t.status == T.QUEUED:
            menu.addSeparator()
            menu.addAction("⬆  Move to top", lambda: self._move_task(t, "top"))
            menu.addAction("↑  Move up", lambda: self._move_task(t, "up"))
            menu.addAction("↓  Move down", lambda: self._move_task(t, "down"))
            menu.addAction("⬇  Move to bottom", lambda: self._move_task(t, "bottom"))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _set_task_limit(self, task, bps):
        if bps == -1:
            val, ok = QInputDialog.getInt(self, "Custom Speed Limit", 
                                          "Enter limit in KB/s (0 for unlimited):",
                                          value=int(task.speed_limit / 1024), min=0, max=999999)
            if ok:
                bps = val * 1024
            else:
                return
        task.set_speed_limit(bps)
        self._save_tick = 10

    # ------------------------------------------------------------- refresh loop
    def refresh(self):
        # browser handed us a download -> show the IDM-style dialog
        if self.pending and not self._dialog_open:
            item = self.pending.popleft()
            self._show_file_info(item.get("url", ""), item.get("filename", ""),
                                 item.get("headers"), flash=True)

        tasks = self._visible_tasks()
        self.empty.setVisible(len(tasks) == 0)
        self.empty.setGeometry(self.table.rect())

        # update the per-task smoothed speed (read by the model + the graph)
        now = time.time()
        total_bps = 0.0
        for t in tasks:
            if t.id not in self._speed:
                self._speed[t.id] = (t.downloaded, now, 0.0)
            last_dl, last_t, bps = self._speed[t.id]
            dt = now - last_t
            if t.status == T.DOWNLOADING and dt >= 0.4:
                inst = max(0.0, (t.downloaded - last_dl) / dt)
                bps = inst if bps <= 0 else 0.6 * inst + 0.4 * bps  # light smoothing
                self._speed[t.id] = (t.downloaded, now, bps)
            elif t.status != T.DOWNLOADING:
                bps = 0
                self._speed[t.id] = (t.downloaded, now, 0)
            if t.status == T.DOWNLOADING:
                total_bps += bps

        self.model.set_tasks(tasks)
        self.model.refresh_dynamic()
        self._refresh_nav_counts()

        self.speed_graph.add_value(total_bps)
        disp_bps = self.speed_graph.current()
        self.total_speed.setText(f"↓ {human_speed(disp_bps)}" if disp_bps > 1 else "")
        self.subtitle.setText(self._subtitle_text())
        self.status_lbl.setText(self._status_text())
        self._update_action_buttons()

        self._check_completions()

        # periodic autosave (every ~10 s) so progress survives crashes
        self._save_tick += 1
        if self._save_tick >= 20:
            self._save_tick = 0
            self._save_state()
            if getattr(self, "_settings_dirty", False):
                self._save_settings()
                self._settings_dirty = False

    def _subtitle_text(self):
        active = sum(1 for t in self.queue.tasks if t.status == T.DOWNLOADING)
        queued = sum(1 for t in self.queue.tasks if t.status == T.QUEUED)
        done = sum(1 for t in self.queue.tasks if t.status == T.COMPLETED)
        return f"{active} active · {queued} queued · {done} completed"

    def _status_text(self):
        if time.time() < getattr(self, "_flash_until", 0):
            return self._flash_text
        srv = f"Browser bridge: http://127.0.0.1:{PORT}" if not self._server_err \
            else f"Server ERROR: {self._server_err}"
        return (f"Save to: {self.save_dir}    |    {srv}"
                f"    |    v{APP_VERSION}")

    # ---------------------------------------------------------------- close
    def closeEvent(self, e):
        active = [t for t in self.queue.tasks
                  if t.status in (T.DOWNLOADING, T.QUEUED)]
        if active:
            ans = QMessageBox.question(
                self, "Exit",
                f"{len(active)} download(s) in progress.\n"
                "Pause them and exit? They will resume next time.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                e.ignore()
                return
            for t in active:
                self.queue.pause_task(t)
            # Wait (bounded) for in-flight workers to actually unwind so the
            # last chunk is durable on disk. A fixed sleep used to truncate
            # tail bytes under slow networks; this drains in the common case
            # and caps the wait so the app still exits if a worker hangs.
            QApplication.processEvents()
            if not self.queue.wait_active(timeout=8.0):
                # workers didn't drain in time — log to status bar so we know
                self._server_err = "shutdown timed out: some writes may be unflushed"
        self.queue.shutdown()
        self._save_state()
        self._save_settings()
        super().closeEvent(e)


def _self_test():
    """Headless smoke test of the frozen binary: construct the app, tick once,
    exit 0. Lets the installer/CI verify the build actually runs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    win = DownloadApp()
    win.refresh()
    win.queue.shutdown()
    QTimer.singleShot(0, app.quit)
    app.exec()
    print(f"selftest OK v{APP_VERSION}")


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"HyperFetch {APP_VERSION}")
        sys.exit(0)
    if "--selftest" in sys.argv:
        _self_test()
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = DownloadApp()
    win.show()
    sys.exit(app.exec())
