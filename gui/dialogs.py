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
    QCheckBox, QListWidget, QListWidgetItem, QTableView, QStyledItemDelegate, QStyle, QTimeEdit, QScrollArea
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
            ("Speed limit", f"{t.speed_limit * 8 // 1000} Kb/s" if t.speed_limit > 0 else "Unlimited"),
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
                 verify_tls=True, pair_token="", theme="dark",
                 sched_en=False, sched_start="02:00", sched_stop="08:00"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setMinimumHeight(550)

        self.main_lay = QVBoxLayout(self)
        self.main_lay.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        inner_w = QWidget()
        lay = QVBoxLayout(inner_w)
        lay.setSpacing(10)
        lay.setContentsMargins(22, 20, 22, 0)
        scroll.setWidget(inner_w)
        
        self.main_lay.addWidget(scroll)

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
        self.segments.setRange(0, 32)
        self.segments.setSpecialValueText("Auto")
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

        # ---- Scheduler section ----
        sep_s = QFrame()
        sep_s.setFrameShape(QFrame.HLine)
        sep_s.setStyleSheet(f"color: {BORDER};")
        lay.addWidget(sep_s)
        lay.addWidget(QLabel("⏰  Scheduler"))
        
        self.sched_chk = QCheckBox("Enable Time-based Scheduler")
        self.sched_chk.setChecked(sched_en)
        lay.addWidget(self.sched_chk)
        
        t_row = QHBoxLayout()
        t_row.addWidget(_muted_label("Start at:"))
        self.time_start = QTimeEdit()
        self.time_start.setDisplayFormat("HH:mm")
        try:
            h, m = map(int, sched_start.split(":"))
        except:
            h, m = 2, 0
        from PySide6.QtCore import QTime
        self.time_start.setTime(QTime(h, m))
        t_row.addWidget(self.time_start)
        
        t_row.addWidget(_muted_label(" Stop at:"))
        self.time_stop = QTimeEdit()
        self.time_stop.setDisplayFormat("HH:mm")
        try:
            h, m = map(int, sched_stop.split(":"))
        except:
            h, m = 8, 0
        self.time_stop.setTime(QTime(h, m))
        t_row.addWidget(self.time_stop)
        t_row.addStretch()
        lay.addLayout(t_row)

        # ---- Updates + Diagnostics ----
        sep_u = QFrame()
        sep_u.setFrameShape(QFrame.HLine)
        sep_u.setStyleSheet(f"color: {BORDER};")
        lay.addWidget(sep_u)

        urow = QHBoxLayout()
        urow.addWidget(_muted_label(f"Version {APP_VERSION}"))
        urow.addStretch()
        self._update_status = QLabel("")
        self._update_status.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        urow.addWidget(self._update_status)
        chk_btn = QPushButton("Check for Updates")
        chk_btn.clicked.connect(self._on_check_updates)
        urow.addWidget(chk_btn)
        lay.addLayout(urow)

        drow = QHBoxLayout()
        drow.addWidget(_muted_label("Diagnostics"))
        drow.addStretch()
        open_crash_btn = QPushButton("Open Crash Folder")
        open_crash_btn.clicked.connect(self._on_open_crashes)
        drow.addWidget(open_crash_btn)
        lay.addLayout(drow)

        brow = QHBoxLayout()
        brow.setContentsMargins(22, 10, 22, 18)
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        brow.addStretch()
        brow.addWidget(cancel)
        brow.addWidget(ok)
        self.main_lay.addLayout(brow)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Default download folder",
                                             self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _on_check_updates(self):
        """Hit the GitHub Releases API (cached 1h) and surface the result.
        If a newer tag is found, offer to open the release page in the browser
        — no silent install, no signed-binary swap, just a manual pointer."""
        self._update_status.setText("checking…")
        QApplication.processEvents()
        info = updater.check_for_update(APP_VERSION, force=True)
        if info is None:
            self._update_status.setText("offline or unavailable")
            return
        if info["available"]:
            self._update_status.setText(f"v{info['version']} available")
            if QMessageBox.question(
                    self, "Update available",
                    f"HyperFetch {info['version']} is out (you have {APP_VERSION}).\n"
                    "Open the release page in your browser?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
                import webbrowser
                webbrowser.open(info["url"])
        else:
            self._update_status.setText("up to date")

    def _on_open_crashes(self):
        d = crash_reporter.crashes_dir()
        # cross-platform "show folder in file manager"
        if sys.platform == "win32":
            os.startfile(d)
        elif sys.platform == "darwin":
            import subprocess; subprocess.Popen(["open", d])
        else:
            import subprocess; subprocess.Popen(["xdg-open", d])

    def values(self):
        theme = "light" if self.theme_combo.currentText() == "Light" else "dark"
        return (self.dir_edit.text().strip(), self.concurrent.value(),
                self.segments.value(), self.verify_chk.isChecked(), theme,
                self.sched_chk.isChecked(), self.time_start.time().toString("HH:mm"),
                self.time_stop.time().toString("HH:mm"))


# ====================================================================== main app
