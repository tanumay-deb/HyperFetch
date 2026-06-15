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
    QCheckBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QBrush, QPen, QLinearGradient

import task as T
import utils
from queue_manager import QueueManager
from api_server import run_server, PORT

APP_VERSION = "1.0.1"


def resource_path(*parts):
    """Locate a bundled resource both in dev and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


SEGMENTS = 8
MAX_CONCURRENT = 3

ACCENT = "#6366f1"          # indigo
ACCENT_2 = "#8b5cf6"        # violet
BG = "#0b1220"              # window background
SURFACE = "#111a2e"         # cards / table
SURFACE_2 = "#1a2540"       # hover / inputs
BORDER = "#243352"
HOVER = "#223054"
TEXT = "#e5e9f5"
MUTED = "#8b97b8"

STATUS_COLORS = {
    T.DOWNLOADING: "#34d399",
    T.COMPLETED:   "#38bdf8",
    T.PAUSED:      "#fbbf24",
    T.QUEUED:      "#94a3b8",
    T.ERROR:       "#f87171",
    T.CANCELLED:   "#64748b",
}

QSS = f"""
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
QPushButton:hover {{ background: #223054; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #1b2747; }}
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
QTableWidget {{
    background: {SURFACE};
    alternate-background-color: #141e36;
    border: 1px solid {BORDER};
    border-radius: 12px;
    gridline-color: transparent;
    outline: none;
}}
QTableWidget::item {{ padding: 0 10px; border: none; }}
QTableWidget::item:selected {{ background: #26335c; color: {TEXT}; }}
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


def _muted_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 600;"
                      "background: transparent;")
    return lbl


class SpeedGraphWidget(QWidget):
    def __init__(self, parent=None, max_points=120):
        super().__init__(parent)
        self.max_points = max_points
        self.data = [0] * max_points
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_value(self, val):
        self.data.append(val)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        max_val = max(self.data)
        if max_val == 0:
            max_val = 1
        max_val *= 1.1  # 10% headroom

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
        self.btn_later = QPushButton("Add")
        self.btn_now = QPushButton("⬇  Download")
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
                 verify_tls=True, pair_token=""):
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

        note = _muted_label("New values apply to downloads started afterwards.")
        lay.addWidget(note)

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
        return (self.dir_edit.text().strip(), self.concurrent.value(),
                self.segments.value(), self.verify_chk.isChecked())


# ====================================================================== main app
class DownloadApp(QWidget):
    COLS = ["File", "Size", "Progress", "Speed", "Status"]
    FILTERS = ["All", "Active", "Done"]

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
        self._dialog_open = False
        self._save_tick = 0
        self._completed_seen = None   # seeded on first refresh; tracks done IDs
        self._complete_popup = None   # the live "Download Completed" popup

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
        self.pair_token = utils.get_or_create_token()

    def _save_settings(self):
        utils.save_json(self._settings_path, {
            "save_dir": self.save_dir,
            "max_concurrent": self.max_concurrent,
            "segments": self.segments,
            "global_speed_limit": getattr(self, "global_speed_limit", 0),
            "verify_tls": getattr(self, "verify_tls", True),
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
        self.setStyleSheet(QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 14)
        root.setSpacing(14)
        
        splitter = QSplitter(Qt.Vertical)
        
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        # ---- header: title + live total speed ----
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("⚡ HyperFetch")
        title.setStyleSheet("font-size: 21px; font-weight: 800;")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box)
        
        self.speed_graph = SpeedGraphWidget()
        head.addWidget(self.speed_graph, 1)

        self.total_speed = QLabel("")
        self.total_speed.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {STATUS_COLORS[T.DOWNLOADING]};")
        self.total_speed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.total_speed)
        
        top_layout.addLayout(head)
        splitter.addWidget(top_widget)

        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(14)

        # ---- toolbar: actions left, filters right ----
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_add = QPushButton("＋  Add URL")
        self.btn_add.setObjectName("primary")
        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_resume = QPushButton("▶  Resume")
        self.btn_cancel = QPushButton("✕  Cancel")
        for b in (self.btn_add, self.btn_pause, self.btn_resume, self.btn_cancel):
            bar.addWidget(b)

        bar.addStretch()

        self.limit_combo = QComboBox()
        self.limit_combo.addItems(["Unlimited", "100 KB/s", "500 KB/s", "1 MB/s", "5 MB/s", "10 MB/s"])
        
        limit_txt = "Unlimited"
        if getattr(self, "global_speed_limit", 0) > 0:
            mb = self.global_speed_limit / (1024*1024)
            if mb >= 1: limit_txt = f"{int(mb)} MB/s"
            else: limit_txt = f"{int(self.global_speed_limit / 1024)} KB/s"
        self.limit_combo.setCurrentText(limit_txt)
        
        self.limit_combo.currentTextChanged.connect(self._on_global_limit_changed)
        bar.addWidget(_muted_label("Global Limit:"))
        bar.addWidget(self.limit_combo)

        self._filter_group = QButtonGroup(self)
        for name in self.FILTERS:
            chip = QPushButton(name)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(name == "All")
            chip.clicked.connect(lambda _, n=name: self._set_filter(n))
            self._filter_group.addButton(chip)
            bar.addWidget(chip)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {BORDER};")
        bar.addWidget(sep)

        self.btn_clear = QPushButton("🗑")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setToolTip("Clear finished downloads from the list")
        self.btn_open = QPushButton("📂")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setToolTip("Open download folder")
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setToolTip("Settings")
        bar.addWidget(self.btn_clear)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_settings)
        bottom_layout.addLayout(bar)

        # ---- table ----
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, width in ((1, 150), (2, 200), (3, 110), (4, 150)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, width)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.cellDoubleClicked.connect(self.on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        bottom_layout.addWidget(self.table)
        
        splitter.addWidget(bottom_widget)
        splitter.setSizes([80, 420])
        root.addWidget(splitter)

        # ---- empty state ----
        self.empty = QLabel("No downloads yet.\nAdd a URL or grab one from the browser.",
                            self.table)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {MUTED}; font-size: 14px; background: transparent;")

        # ---- footer ----
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        root.addWidget(self.status_lbl)

        self.btn_add.clicked.connect(self.on_add)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_resume.clicked.connect(self.on_resume)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_open.clicked.connect(self.on_open)
        self.btn_settings.clicked.connect(self.on_settings)

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
        if self._filter == "Active":
            keep = (T.DOWNLOADING, T.QUEUED, T.PAUSED)
            return [t for t in tasks if t.status in keep]
        if self._filter == "Done":
            keep = (T.COMPLETED, T.ERROR, T.CANCELLED)
            return [t for t in tasks if t.status in keep]
        return tasks

    def _selected_task(self):
        row = self.table.currentRow()
        for tid, r in self._rows.items():
            if r == row:
                return next((t for t in self.queue.tasks if t.id == tid), None)
        return None

    # ---------------------------------------------------------------- dialogs
    def _show_file_info(self, url="", suggested="", headers=None):
        """Run the IDM-style dialog and queue the result."""
        self._dialog_open = True
        try:
            dlg = FileInfoDialog(self, url, self.save_dir, suggested, headers)
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
        t = self._selected_task()
        if t:
            self.queue.pause_task(t)

    def on_resume(self):
        t = self._selected_task()
        if t:
            self.queue.resume_task(t)

    def on_cancel(self):
        t = self._selected_task()
        if t:
            self.queue.cancel_task(t)

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
                             verify_tls=self.verify_tls, pair_token=self.pair_token)
        if dlg.exec() != QDialog.Accepted:
            return
        d, conc, segs, verify = dlg.values()
        if os.path.isdir(d):
            self.save_dir = d
        self.max_concurrent = conc
        self.segments = segs
        self.queue.max_concurrent = conc
        self.queue.segments = segs
        self.verify_tls = verify
        utils.VERIFY_TLS = verify
        self._save_settings()

    def on_row_double_clicked(self, row, _col):
        for tid, r in self._rows.items():
            if r == row:
                t = next((x for x in self.queue.tasks if x.id == tid), None)
                if t:
                    PropertiesDialog(self, t).exec()
                return

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
        item = self.table.itemAt(pos)
        if not item: return
        row = item.row()
        task_id = self.table.item(row, 0).data(Qt.UserRole)
        t = self.queue.get_task(task_id)
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
                                 item.get("headers"))

        tasks = self._visible_tasks()
        if self.table.rowCount() != len(tasks):
            self.table.setRowCount(len(tasks))
        self._rows = {}
        self.empty.setVisible(len(tasks) == 0)
        self.empty.setGeometry(self.table.rect())

        now = time.time()
        total_bps = 0.0
        for row, t in enumerate(tasks):
            self._rows[t.id] = row

            # speed from downloaded delta (seed the baseline on first sight,
            # otherwise dt stays 0 forever and the speed never shows)
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

            self._set(row, 0, t.filename)
            self.table.item(row, 0).setData(Qt.UserRole, t.id)
            size = f"{human_size(t.downloaded)} / {human_size(t.total_size)}" \
                if t.total_size else human_size(t.downloaded)
            self._set(row, 1, size)
            self._set_progress(row, t)
            self._set(row, 3, human_speed(bps) if t.status == T.DOWNLOADING else "")
            self._set_status(row, t)

        self.speed_graph.add_value(total_bps)
        self.total_speed.setText(f"↓ {human_speed(total_bps)}" if total_bps > 0 else "")
        self.subtitle.setText(self._subtitle_text())
        self.status_lbl.setText(self._status_text())

        self._check_completions()

        # periodic autosave (every ~10 s) so progress survives crashes
        self._save_tick += 1
        if self._save_tick >= 20:
            self._save_tick = 0
            self._save_state()

    def _set(self, row, col, text):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)
        if item.text() != text:
            item.setText(text)

    def _set_progress(self, row, t):
        box = self.table.cellWidget(row, 2)
        if box is None or not isinstance(box.layout(), QHBoxLayout) \
                or not isinstance(box.findChild(QProgressBar), QProgressBar):
            box = QWidget()
            box.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(box)
            lay.setContentsMargins(10, 0, 6, 0)
            lay.setSpacing(8)
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            pct = QLabel("")
            pct.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;"
                              "background: transparent;")
            pct.setFixedWidth(34)
            lay.addWidget(bar, 1)
            lay.addWidget(pct)
            self.table.setCellWidget(row, 2, box)
        bar = box.findChild(QProgressBar)
        pct = box.findChild(QLabel)
        if t.total_size <= 0 and t.status == T.DOWNLOADING:
            bar.setRange(0, 0)  # indeterminate
            pct.setText("…")
        else:
            # size-less downloads finish with percent==0; show done as done
            val = 100 if t.status == T.COMPLETED else t.percent
            bar.setRange(0, 100)
            bar.setValue(val)
            pct.setText(f"{val}%")

    def _set_status(self, row, t):
        box = self.table.cellWidget(row, 4)
        if box is None or box.findChild(QLabel) is None:
            box = QWidget()
            box.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(box)
            lay.setContentsMargins(6, 8, 10, 8)
            pill = QLabel()
            pill.setAlignment(Qt.AlignCenter)
            lay.addWidget(pill)
            self.table.setCellWidget(row, 4, box)
        pill = box.findChild(QLabel)
        color = STATUS_COLORS.get(t.status, TEXT)
        text = t.error[:18] if t.status == T.ERROR and t.error else t.status
        if pill.text() != text:
            pill.setText(text)
        c = QColor(color)
        pill.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},0.16);"
            f"color: {color}; border-radius: 10px;"
            "padding: 3px 12px; font-weight: 700; font-size: 11px;")
        if t.status == T.ERROR and t.error:
            pill.setToolTip(t.error)

    def _subtitle_text(self):
        active = sum(1 for t in self.queue.tasks if t.status == T.DOWNLOADING)
        queued = sum(1 for t in self.queue.tasks if t.status == T.QUEUED)
        done = sum(1 for t in self.queue.tasks if t.status == T.COMPLETED)
        return f"{active} active · {queued} queued · {done} completed"

    def _status_text(self):
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
            time.sleep(0.5)  # give workers a beat to stop and flush
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
