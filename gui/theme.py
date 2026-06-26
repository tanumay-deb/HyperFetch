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
import crash_reporter
import updater
from queue_manager import QueueManager
from api_server import run_server, PORT


APP_VERSION = "1.2.1"


def resource_path(*parts):
    """Locate a bundled resource both in dev and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


SEGMENTS = 8
MAX_CONCURRENT = 3

# ---------------------------------------------------------------- theme palettes
DARK = dict(
    accent="#7c3aed", accent2="#8b5cf6",
    bg="#0b0e14", surface="#141722", surface2="#1c202d",
    border="#2a2f40", hover="#1e2333", pressed="#181c29",
    alt="#0f121a", sel="#252a3d",
    text="#f1f5f9", muted="#94a3b8",
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


# color names propagated to consumer modules on a theme switch (see below)
_COLOR_NAMES = ("ACCENT", "ACCENT_2", "BG", "SURFACE", "SURFACE_2",
                "BORDER", "HOVER", "PRESSED", "ALT", "SEL", "TEXT", "MUTED")
# modules that do `from gui.theme import *` and read the colors at paint/style
# time. After main.py was split into the gui/ package these copies stopped
# tracking apply_theme's rebinds, so a runtime theme switch left delegates /
# graph / dialogs stale. Re-pushing the values keeps the single-namespace
# `import *` ergonomics without the staleness.
_THEME_CONSUMERS = ("gui.delegates", "gui.models", "gui.dialogs", "gui.main_window")


def apply_theme(name):
    """Switch the active palette by reassigning the module-level colour globals
    AND propagating them to the consumer modules that imported them by value."""
    global THEME, ACCENT, ACCENT_2, BG, SURFACE, SURFACE_2
    global BORDER, HOVER, PRESSED, ALT, SEL, TEXT, MUTED
    THEME = "light" if name == "light" else "dark"
    p = palette_for(THEME)
    ACCENT, ACCENT_2 = p["accent"], p["accent2"]
    BG, SURFACE, SURFACE_2 = p["bg"], p["surface"], p["surface2"]
    BORDER, HOVER, PRESSED = p["border"], p["hover"], p["pressed"]
    ALT, SEL = p["alt"], p["sel"]
    TEXT, MUTED = p["text"], p["muted"]

    g = globals()
    for modname in _THEME_CONSUMERS:
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        for n in _COLOR_NAMES:
            if hasattr(mod, n):
                setattr(mod, n, g[n])

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
    bits = n * 8
    for unit in ("b", "Kb", "Mb", "Gb", "Tb"):
        if bits < 1000:
            return f"{bits:.0f} {unit}" if unit == "b" else f"{bits:.1f} {unit}"
        bits /= 1000
    return f"{bits:.1f} Pb"


def human_speed(bytes_per_sec):
    if bytes_per_sec <= 0:
        return ""
    bits_per_sec = bytes_per_sec * 8
    for unit in ("b/s", "Kb/s", "Mb/s", "Gb/s"):
        if bits_per_sec < 1000:
            return f"{bits_per_sec:.0f} {unit}" if unit == "b/s" else f"{bits_per_sec:.1f} {unit}"
        bits_per_sec /= 1000
    return f"{bits_per_sec:.1f} Tb/s"


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
    # a near-stalled speed makes (bytes_left / bps) explode (e.g. "18808h");
    # anything beyond ~4 days is meaningless noise -> show "—".
    if secs >= 99 * 3600:
        return "—"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _muted_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 600;"
                      "background: transparent;")
    return lbl


