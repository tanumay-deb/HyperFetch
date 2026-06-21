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
    QCheckBox, QListWidget, QListWidgetItem, QTableView, QStyledItemDelegate, QStyle, QTimeEdit, QScrollArea, QStackedWidget, QSlider
)
from PySide6.QtCore import (
    Qt, QTimer, QModelIndex, QAbstractTableModel, QSortFilterProxyModel, QRect, QSize, QPropertyAnimation, QEasingCurve, QPoint, Property
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QBrush, QPen, QLinearGradient

import task as T
import utils
import torrent
import crash_reporter
import updater
from queue_manager import QueueManager
from api_server import run_server, PORT


from gui.theme import *
from gui.theme import _muted_label

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
        # magnet links are valid targets too (handled by the aria2c engine)
        ok = text.strip().lower().startswith(("http://", "https://", "magnet:"))
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

        folder = os.path.dirname(os.path.normpath(task.save_path)) or "."
        # Open File launches the real file/dir; for a torrent the save_path is a
        # placeholder and the payload is a folder, so open that folder instead
        # (Open Folder just needs the destination dir, always present).
        if os.path.exists(task.save_path):
            self._open_target = task.save_path
        elif torrent.is_torrent_task(task.url, task.filename) and os.path.isdir(folder):
            self._open_target = folder
        else:
            self._open_target = ""
        self.btn_open.setEnabled(bool(self._open_target))
        self.btn_folder.setEnabled(os.path.isdir(folder))

    def _open_file(self):
        target = getattr(self, "_open_target", "") or self.task.save_path
        try:
            os.startfile(target)
        except OSError:
            pass
        self.close()              # close the popup on any action

    def _open_folder(self):
        path = os.path.normpath(self.task.save_path)
        try:
            if os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(os.path.dirname(path) or ".")
        except OSError:
            try:
                os.startfile(os.path.dirname(path) or ".")
            except OSError:
                pass
        self.close()

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
        ]
        if t.error:
            rows.append(("Error", t.error))
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_muted_label(k), i, 0, Qt.AlignTop)
            if k == "URL":
                val = QLineEdit(v)
                val.setReadOnly(True)
                val.setCursorPosition(0)
                val.setStyleSheet("background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 4px; color: " + TEXT)
                grid.addWidget(val, i, 1)
            else:
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
        self._open_target = self._resolve_open_target(t)
        btn_file.setEnabled(t.status == T.COMPLETED and bool(self._open_target))
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

    def _resolve_open_target(self, t):
        """Path that 'Open File' should launch. The real file/dir if it exists;
        for a torrent (save_path is a placeholder, and the payload is a folder)
        fall back to the destination folder so it still opens something useful."""
        if os.path.exists(t.save_path):
            return t.save_path
        folder = os.path.dirname(t.save_path) or "."
        if torrent.is_torrent_task(t.url, t.filename) and os.path.isdir(folder):
            return folder
        return ""

    def _open_file(self):
        target = getattr(self, "_open_target", "") or self.t.save_path
        try:
            os.startfile(target)
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



class AnimatedToggle(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._thumb_pos = 2
        
        self.anim = QPropertyAnimation(self, b"thumb_pos", self)
        self.anim.setEasingCurve(QEasingCurve.InOutCirc)
        self.anim.setDuration(200)
        
        self.stateChanged.connect(self._on_state_change)

    def get_thumb_pos(self):
        return self._thumb_pos

    def set_thumb_pos(self, pos):
        self._thumb_pos = pos
        self.update()

    thumb_pos = Property(float, get_thumb_pos, set_thumb_pos)

    def _on_state_change(self, state):
        self.anim.stop()
        if state:
            self.anim.setEndValue(self.width() - 20)
        else:
            self.anim.setEndValue(2)
        self.anim.start()

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Track
        track_rect = QRect(0, 0, self.width(), self.height())
        if self.isChecked():
            p.setBrush(QColor(ACCENT))
            p.setPen(Qt.NoPen)
        else:
            p.setBrush(QColor(SURFACE))
            p.setPen(QColor(BORDER))
            
        p.drawRoundedRect(track_rect, 11, 11)
        
        # Thumb
        p.setBrush(QColor("white"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(self._thumb_pos, 2, 18, 18)
        p.end()

def _card_widget():
    w = QWidget()
    w.setStyleSheet(f"QWidget {{ background: {SURFACE_2}; border-radius: 8px; }}")
    return w

class SettingsSlider(QWidget):
    def __init__(self, min_val, max_val, current_val):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        
        self.val_lbl = QLabel(str(current_val))
        self.val_lbl.setFixedWidth(30)
        self.val_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(current_val)
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border-radius: 2px;
                height: 4px;
                background: {SURFACE};
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT};
                border-radius: 2px;
            }}
        """)
        
        self.max_lbl = QLabel(str(max_val))
        self.max_lbl.setStyleSheet(f"color: {MUTED};")
        
        self.slider.valueChanged.connect(lambda v: self.val_lbl.setText(str(v)))
        
        lay.addWidget(self.val_lbl)
        lay.addWidget(self.slider)
        lay.addWidget(self.max_lbl)

class SettingsDialog(QDialog):
    def __init__(self, parent, save_dir, max_concurrent, segments,
                 verify_tls=True, pair_token="", theme="dark",
                 sched_en=False, sched_start="02:00", sched_stop="08:00"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        # Style the dialog background
        self.setStyleSheet(f"QDialog {{ background: {BG}; }}")

        self.main_lay = QVBoxLayout(self)
        self.main_lay.setContentsMargins(0, 0, 0, 0)
        self.main_lay.setSpacing(0)
        
        # Top title bar (mockup shows "HyperFetch" and "Search settings...")
        # For simplicity, we just use a header
        
        content_lay = QHBoxLayout()
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        
        # --- Sidebar ---
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet(f"QWidget {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}")
        sb_lay = QVBoxLayout(self.sidebar)
        sb_lay.setContentsMargins(10, 20, 10, 20)
        sb_lay.setSpacing(8)
        
        title = QLabel("Settings")
        title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {TEXT}; padding-left: 10px; padding-bottom: 10px;")
        sb_lay.addWidget(title)
        
        self.nav = QListWidget()
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setStyleSheet(f"""
            QListWidget {{ background: transparent; outline: none; }}
            QListWidget::item {{ color: {MUTED}; padding: 10px 14px; border-radius: 8px; font-weight: 600; margin-bottom: 4px; }}
            QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
            QListWidget::item:hover:!selected {{ background: {HOVER}; color: {TEXT}; }}
        """)
        
        self.nav.addItem("⚙  Settings")
        self.nav.addItem("ℹ  About")
        self.nav.setCurrentRow(0)
        
        sb_lay.addWidget(self.nav)
        sb_lay.addStretch()
        
        content_lay.addWidget(self.sidebar)
        
        # --- Stacked Content ---
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"QStackedWidget {{ background: {BG}; }}")
        
        # PAGE 1: Settings
        self.page_settings = QScrollArea()
        self.page_settings.setWidgetResizable(True)
        self.page_settings.setFrameShape(QFrame.NoFrame)
        self.page_settings.setStyleSheet(f"QScrollArea {{ background: {BG}; }} QWidget#inner {{ background: {BG}; }}")
        
        inner_w = QWidget()
        inner_w.setObjectName("inner")
        s_lay = QVBoxLayout(inner_w)
        s_lay.setContentsMargins(30, 30, 40, 30)
        s_lay.setSpacing(24)
        
        # General
        s_lay.addWidget(self._sec_title("General"))
        gen_card = _card_widget()
        g_lay = QVBoxLayout(gen_card)
        g_lay.setSpacing(16)
        
        # Default download folder
        drow = QHBoxLayout()
        d_icon = QLabel("📁")
        d_icon.setStyleSheet(f"font-size: 20px; background: transparent;")
        drow.addWidget(d_icon)
        
        d_text = QVBoxLayout()
        d_text.setSpacing(2)
        d_lbl = QLabel("Default Download Folder")
        d_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        self.dir_edit = QLabel(save_dir)
        self.dir_edit.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        d_text.addWidget(d_lbl)
        d_text.addWidget(self.dir_edit)
        drow.addLayout(d_text)
        drow.addStretch()
        
        browse = QPushButton("Browse...")
        browse.setStyleSheet(f"background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;")
        browse.clicked.connect(self._browse)
        drow.addWidget(browse)
        g_lay.addLayout(drow)
        
        # Sliders
        sliders_row = QHBoxLayout()
        sliders_row.setSpacing(20)
        
        # Concurrent Downloads
        c_card = _card_widget()
        c_card.setStyleSheet(f"QWidget {{ background: {SURFACE}; border-radius: 8px; }}")
        c_lay = QVBoxLayout(c_card)
        c_title = QLabel("Concurrent Downloads")
        c_title.setStyleSheet(f"color: {TEXT}; font-weight: 600; background: transparent;")
        c_desc = QLabel("Maximum number of downloads at the same time.")
        c_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        self.concurrent_slider = SettingsSlider(1, 16, max_concurrent)
        c_lay.addWidget(c_title)
        c_lay.addWidget(c_desc)
        c_lay.addWidget(self.concurrent_slider)
        sliders_row.addWidget(c_card)
        
        # Connections
        s_card = _card_widget()
        s_card.setStyleSheet(f"QWidget {{ background: {SURFACE}; border-radius: 8px; }}")
        sl_lay = QVBoxLayout(s_card)
        s_title = QLabel("Connections per Download")
        s_title.setStyleSheet(f"color: {TEXT}; font-weight: 600; background: transparent;")
        s_desc = QLabel("Maximum connections for each download.")
        s_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        self.segments_slider = SettingsSlider(1, 16, segments if segments > 0 else 8)
        sl_lay.addWidget(s_title)
        sl_lay.addWidget(s_desc)
        sl_lay.addWidget(self.segments_slider)
        sliders_row.addWidget(s_card)
        
        g_lay.addLayout(sliders_row)
        s_lay.addWidget(gen_card)
        
        # Appearance
        s_lay.addWidget(self._sec_title("Appearance"))
        app_card = _card_widget()
        a_lay = QHBoxLayout(app_card)
        a_lay.addWidget(QLabel("🌙"))
        a_text = QVBoxLayout()
        a_lbl = QLabel("Theme")
        a_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        a_desc = QLabel("Choose your preferred theme.")
        a_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        a_text.addWidget(a_lbl)
        a_text.addWidget(a_desc)
        a_lay.addLayout(a_text)
        a_lay.addStretch()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.setCurrentText("Light" if theme == "light" else "Dark")
        a_lay.addWidget(self.theme_combo)
        s_lay.addWidget(app_card)
        
        # Security
        s_lay.addWidget(self._sec_title("Security"))
        sec_card = _card_widget()
        se_lay = QHBoxLayout(sec_card)
        se_lay.addWidget(QLabel("🛡️"))
        se_text = QVBoxLayout()
        se_lbl = QLabel("Verify HTTPS Certificates (Recommended)")
        se_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        se_desc = QLabel("Ensures secure connections by verifying website certificates.")
        se_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        se_text.addWidget(se_lbl)
        se_text.addWidget(se_desc)
        se_lay.addLayout(se_text)
        se_lay.addStretch()
        self.verify_chk = AnimatedToggle()
        self.verify_chk.setChecked(verify_tls)
        # Ensure initial state matches visually
        self.verify_chk._thumb_pos = self.verify_chk.width() - 20 if verify_tls else 2
        se_lay.addWidget(self.verify_chk)
        s_lay.addWidget(sec_card)
        
        # Browser Integration
        s_lay.addWidget(self._sec_title("Browser Integration"))
        br_card = _card_widget()
        b_lay = QHBoxLayout(br_card)
        b_lay.addWidget(QLabel("🧩"))
        b_text = QVBoxLayout()
        b_lbl = QLabel("Browser Extension Pairing Token")
        b_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        b_desc = QLabel("Use this token to pair your browser extension with HyperFetch.")
        b_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        b_text.addWidget(b_lbl)
        b_text.addWidget(b_desc)
        b_lay.addLayout(b_text)
        b_lay.addStretch()
        
        self.token_edit = QLineEdit(pair_token)
        self.token_edit.setReadOnly(True)
        self.token_edit.setFixedWidth(200)
        self.token_edit.setStyleSheet(f"background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px; color: {MUTED};")
        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet(f"background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.token_edit.text()))
        b_lay.addWidget(self.token_edit)
        b_lay.addWidget(copy_btn)
        s_lay.addWidget(br_card)
        
        # Scheduler
        sch_card = _card_widget()
        sc_lay = QHBoxLayout(sch_card)
        sc_lay.addWidget(QLabel("⏰"))
        sc_text = QVBoxLayout()
        sc_lbl = QLabel("Scheduler")
        sc_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        sc_desc = QLabel("Enable time-based scheduling")
        sc_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        sc_text.addWidget(sc_lbl)
        sc_text.addWidget(sc_desc)
        
        # Times
        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("Start at", styleSheet=f"color: {MUTED}; font-size: 11px; background: transparent;"))
        self.time_start = QTimeEdit()
        self.time_start.setDisplayFormat("HH:mm")
        try: h, m = map(int, sched_start.split(":"))
        except: h, m = 2, 0
        from PySide6.QtCore import QTime
        self.time_start.setTime(QTime(h, m))
        t_row.addWidget(self.time_start)
        
        t_row.addWidget(QLabel("Stop at", styleSheet=f"color: {MUTED}; font-size: 11px; background: transparent;"))
        self.time_stop = QTimeEdit()
        self.time_stop.setDisplayFormat("HH:mm")
        try: h, m = map(int, sched_stop.split(":"))
        except: h, m = 8, 0
        self.time_stop.setTime(QTime(h, m))
        t_row.addWidget(self.time_stop)
        t_row.addStretch()
        
        sc_text.addLayout(t_row)
        sc_lay.addLayout(sc_text)
        sc_lay.addStretch()
        self.sched_chk = AnimatedToggle()
        self.sched_chk.setChecked(sched_en)
        self.sched_chk._thumb_pos = self.sched_chk.width() - 20 if sched_en else 2
        sc_lay.addWidget(self.sched_chk)
        s_lay.addWidget(sch_card)
        
        s_lay.addStretch()
        self.page_settings.setWidget(inner_w)
        self.stack.addWidget(self.page_settings)
        
        # PAGE 2: About
        self.page_about = QScrollArea()
        self.page_about.setWidgetResizable(True)
        self.page_about.setFrameShape(QFrame.NoFrame)
        self.page_about.setStyleSheet(f"QScrollArea {{ background: {BG}; }} QWidget#about_inner {{ background: {BG}; }}")
        
        ab_inner = QWidget()
        ab_inner.setObjectName("about_inner")
        ab_lay = QVBoxLayout(ab_inner)
        ab_lay.setContentsMargins(30, 30, 40, 30)
        ab_lay.setSpacing(24)
        
        ab_lay.addWidget(self._sec_title("About"))
        about_card = _card_widget()
        abc_lay = QHBoxLayout(about_card)
        abc_lay.addWidget(QLabel("ℹ️"))
        abc_text = QVBoxLayout()
        abc_lbl = QLabel(f"HyperFetch v{APP_VERSION}")
        abc_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; background: transparent;")
        abc_desc = QLabel("Check for updates or access diagnostics.")
        abc_desc.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        abc_text.addWidget(abc_lbl)
        abc_text.addWidget(abc_desc)
        
        self._update_status = QLabel("")
        self._update_status.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        abc_text.addWidget(self._update_status)
        
        abc_lay.addLayout(abc_text)
        abc_lay.addStretch()
        
        btn_col = QVBoxLayout()
        chk_btn = QPushButton("Check for Updates")
        chk_btn.setStyleSheet(f"background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;")
        chk_btn.clicked.connect(self._on_check_updates)
        btn_col.addWidget(chk_btn)
        
        open_crash_btn = QPushButton("Open Crash Folder")
        open_crash_btn.setStyleSheet(f"background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;")
        open_crash_btn.clicked.connect(self._on_open_crashes)
        btn_col.addWidget(open_crash_btn)
        
        abc_lay.addLayout(btn_col)
        ab_lay.addWidget(about_card)
        ab_lay.addStretch()
        
        self.page_about.setWidget(ab_inner)
        self.stack.addWidget(self.page_about)
        
        content_lay.addWidget(self.stack)
        
        # Link nav to stack
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        
        self.main_lay.addLayout(content_lay)
        
        # Bottom Bar
        bot_lay = QHBoxLayout()
        bot_lay.setContentsMargins(20, 16, 30, 16)
        bot_lay.addStretch()
        
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"background: transparent; color: {TEXT}; padding: 8px 16px; border: none;")
        cancel.clicked.connect(self.reject)
        
        ok = QPushButton("Save Changes")
        ok.setObjectName("primary")
        ok.setStyleSheet(f"""
            QPushButton {{ background: {ACCENT}; color: white; border-radius: 8px; padding: 8px 24px; font-weight: 700; }}
            QPushButton:hover {{ background: #9D3CFF; }}
        """)
        ok.clicked.connect(self.accept)
        
        bot_lay.addWidget(cancel)
        bot_lay.addWidget(ok)
        
        # A thin line above buttons
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {SURFACE};")
        self.main_lay.addWidget(sep)
        self.main_lay.addLayout(bot_lay)

    def _sec_title(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {TEXT};")
        return lbl

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder", self.dir_edit.text())
        if folder:
            self.dir_edit.setText(folder)

    def values(self):
        return (
            self.dir_edit.text(),
            self.concurrent_slider.slider.value(),
            self.segments_slider.slider.value(),
            self.verify_chk.isChecked(),
            self.theme_combo.currentText().lower(),
            self.sched_chk.isChecked(),
            self.time_start.time().toString("HH:mm"),
            self.time_stop.time().toString("HH:mm")
        )

    def _on_check_updates(self):
        self._update_status.setText("Checking...")
        QApplication.processEvents()
        import urllib.request, json
        try:
            req = urllib.request.Request("https://api.github.com/repos/tanumay-deb/HyperFetch/releases/latest")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
                latest = data.get("tag_name", "")
                if latest and latest.lstrip("v") != APP_VERSION:
                    self._update_status.setText(f"Update available: {latest}")
                else:
                    self._update_status.setText("You are on the latest version.")
        except Exception:
            self._update_status.setText("Failed to check for updates.")

    def _on_open_crashes(self):
        folder = crash_reporter.CRASH_DIR
        if os.path.exists(folder):
            try:
                os.startfile(folder)
            except OSError:
                pass
