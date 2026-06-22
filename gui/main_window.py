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
    QCheckBox, QListWidget, QListWidgetItem, QTableView, QStyledItemDelegate, QStyle, QListView,
    QSystemTrayIcon, QStackedWidget
)
from PySide6.QtCore import (
    Qt, QTimer, QModelIndex, QAbstractTableModel, QSortFilterProxyModel, QRect, QSize, QEvent, QPropertyAnimation, QEasingCurve
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QBrush, QPen, QLinearGradient, QKeySequence, QShortcut

import task as T
import utils
import torrent as _torrent
import crash_reporter
import updater
from queue_manager import QueueManager
from api_server import run_server, PORT


from gui.theme import *
from gui.theme import _muted_label
from gui.models import TaskTableModel
from gui.delegates import CardDelegate, SpeedGraphWidget
from gui.dialogs import FileInfoDialog, DownloadCompleteDialog, PropertiesDialog, SettingsDialog
from gui.icons import themed_icon

class DownloadApp(QWidget):
    COLS = ["File", "Size", "Progress", "Speed", "Status"]
    FILTERS = ["All", "Active", "Paused", "Done"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HyperFetch")
        self.setMinimumSize(850, 500)
        self.setAcceptDrops(True)
        self._app_icon = QIcon()
        for ic in (resource_path("assets", "icon.ico"),
                   resource_path("assets", "icon.png")):
            if os.path.exists(ic):
                self._app_icon = QIcon(ic)
                self.setWindowIcon(self._app_icon)
                break

        self._settings_path = os.path.join(utils.app_data_dir(), "settings.json")
        self._state_path = os.path.join(utils.app_data_dir(), "downloads.json")
        self._load_settings()

        self.queue = QueueManager(queues=self.queues_config, segments=self.segments)
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
        self.tray = None
        self._tray_notice_shown = False
        self._quit_requested = False

        self._build_ui()
        self._setup_tray()
        self._load_state()
        self._start_server()
        self._check_unsent_crashes()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        
        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.timeout.connect(self._check_scheduler)
        self._scheduler_timer.start(60000)
        QTimer.singleShot(1000, self._check_scheduler)

    def _check_unsent_crashes(self):
        """If the previous run left crash reports on disk, show a one-line
        flash and remember the count so the user can review them via the
        notification's 'Open' action. No network involvement."""
        reports = crash_reporter.unsent_reports()
        self._crash_dir = crash_reporter.crashes_dir()
        if reports:
            n = len(reports)
            self._flash(f"⚠ {n} crash report{'s' if n != 1 else ''} from last run — "
                        f"Settings → Open Crash Folder", secs=8)

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
        self.queues_config = s.get("queues", [{"name": "Main", "max_concurrent": self.max_concurrent}])
        self.scheduler_enabled = bool(s.get("scheduler_enabled", False))
        self.scheduler_start = s.get("scheduler_start", "02:00")
        self.scheduler_stop = s.get("scheduler_stop", "08:00")
        self._scheduler_active = False

    def _save_settings(self):
        widths = {}
        if hasattr(self, "table") and hasattr(self.table, "horizontalHeader"):
            h = self.table.horizontalHeader()
            for c in range(self.model.columnCount()):
                widths[str(c)] = self.table.columnWidth(c)
        else:
            widths = getattr(self, "column_widths", {})
        utils.save_json(self._settings_path, {
            "save_dir": self.save_dir,
            "max_concurrent": self.max_concurrent,
            "segments": self.segments,
            "global_speed_limit": getattr(self, "global_speed_limit", 0),
            "verify_tls": getattr(self, "verify_tls", True),
            "theme": getattr(self, "theme", "dark"),
            "column_widths": widths or self.column_widths,
            "queues": [{"name": q.name, "max_concurrent": q.max_concurrent} for q in getattr(self, "queue", type("T", (), {"queues": {}})).queues.values()] if hasattr(self, "queue") else self.queues_config,
            "scheduler_enabled": getattr(self, "scheduler_enabled", False),
            "scheduler_start": getattr(self, "scheduler_start", "02:00"),
            "scheduler_stop": getattr(self, "scheduler_stop", "08:00")
        })

    def _load_state(self):
        for d in utils.load_json(self._state_path, []):
            try:
                t = T.DownloadTask.from_dict(d)
            except (KeyError, TypeError, ValueError):
                continue
            self.queue.add_task(t, start=False)
        self._sweep_orphan_temps()

    def _sweep_orphan_temps(self):
        """Delete .hfdownload temp files in %TEMP% and the legacy save dir that
        don't belong to any persisted task. They're leftovers from a previous crash
        with no way to resume."""
        import tempfile
        known = {os.path.join(tempfile.gettempdir(), f"{t.id}.hfdownload") for t in self.queue.tasks}
        candidates = []
        
        # 1. Sweep legacy locations (save dir + subfolders)
        try:
            for entry in os.scandir(self.save_dir):
                if entry.is_file() and entry.name.endswith(".hfdownload"):
                    candidates.append(entry.path)
                elif entry.is_dir() and entry.name in utils.CATEGORIES:
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_file() and sub.name.endswith(".hfdownload"):
                                candidates.append(sub.path)
                    except OSError:
                        pass
        except OSError:
            pass

        # 2. Sweep %TEMP% (current staging area)
        try:
            for entry in os.scandir(tempfile.gettempdir()):
                if entry.is_file() and entry.name.endswith(".hfdownload"):
                    candidates.append(entry.path)
        except OSError:
            pass

        for path in candidates:
            if path not in known:
                try:
                    os.remove(path)
                except OSError:
                    pass

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
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Content stack: page 0 = downloads list. Settings is added as a
        # transient page in on_settings() so it lives INSIDE the app rather than
        # popping a separate modal window.
        self.content_stack = QStackedWidget()
        self.page_downloads = QWidget()
        dl_layout = QVBoxLayout(self.page_downloads)
        dl_layout.setContentsMargins(24, 20, 24, 10)
        dl_layout.setSpacing(16)

        # ---- top bar (Search only) ----
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)
        top_bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search downloads...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(32)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._on_search)
        self.search.setStyleSheet(f"background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0 10px; color: {TEXT};")
        top_bar.addWidget(self.search)
        
        # Keep old buttons hidden
        self.btn_pause = QPushButton()
        self.btn_resume = QPushButton()
        self.btn_cancel = QPushButton()
        self.btn_more = QPushButton()
        self.btn_clear = QPushButton()
        self.btn_open = QPushButton()
        self.btn_settings = QPushButton()
        self.limit_combo = QComboBox()

        dl_layout.addLayout(top_bar)

        # ---- filter pills & sort ----
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)
        
        self.filter_group = QButtonGroup(self)
        self.filter_pills = {}
        for i, text in enumerate(["All", "Active", "Paused", "Completed", "Failed"]):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("pill")
            if i == 0: btn.setChecked(True)
            self.filter_group.addButton(btn, i)
            self.filter_pills[text.lower()] = btn
            filter_bar.addWidget(btn)
            
        filter_bar.addStretch()
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort by", "Name", "Date Added", "Size", "Status"])
        self.sort_combo.setStyleSheet(f"QComboBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 10px; color: {TEXT}; }}")
        filter_bar.addWidget(self.sort_combo)
        
        self.filter_group.idClicked.connect(self._on_filter_pill_clicked)

        dl_layout.addLayout(filter_bar)

        # ---- virtualized list view (model/view: custom CardDelegate) ----
        self.model = TaskTableModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(TaskTableModel.SORT_ROLE)
        self.proxy.sort(2, Qt.AscendingOrder)

        self.list_view = QListView()
        self.list_view.setModel(self.proxy)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list_view.setAlternatingRowColors(False)
        self.list_view.setSpacing(6)
        
        from gui.delegates import CardDelegate
        self.card_delegate = CardDelegate(self.list_view)
        self.list_view.setItemDelegate(self.card_delegate)
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._on_context_menu)
        self.list_view.selectionModel().selectionChanged.connect(
            lambda *_: self._update_action_buttons())
        self.list_view.doubleClicked.connect(self._on_double_clicked)
        
        dl_layout.addWidget(self.list_view, 1)

        # We also override self.table so old references don't crash
        self.table = self.list_view

        # ---- empty state overlay ----
        self.empty = QWidget(self.list_view)
        empty_lay = QVBoxLayout(self.empty)
        empty_lay.setAlignment(Qt.AlignCenter)
        
        icon_lbl = QLabel("⬇")
        icon_lbl.setStyleSheet(f"font-size: 40px; color: {MUTED};")
        icon_lbl.setAlignment(Qt.AlignCenter)
        empty_lay.addWidget(icon_lbl)
        
        title_lbl = QLabel("No downloads yet")
        title_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {TEXT};")
        title_lbl.setAlignment(Qt.AlignCenter)
        empty_lay.addWidget(title_lbl)
        
        sub_lbl = QLabel("Paste a URL or drag & drop a link to get started.")
        sub_lbl.setStyleSheet(f"font-size: 13px; color: {MUTED};")
        sub_lbl.setAlignment(Qt.AlignCenter)
        empty_lay.addWidget(sub_lbl)
        
        self.empty.hide()

        self.content_stack.addWidget(self.page_downloads)
        main_layout.addWidget(self.content_stack, 1)

        root.addWidget(main, 1)

        self.btn_add.clicked.connect(self.on_add)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_resume.clicked.connect(self.on_resume)
        self.btn_cancel.clicked.connect(self._on_delete_key)   # Delete = cancel active + remove finished
        self.btn_more.clicked.connect(self._show_bulk_menu)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_open.clicked.connect(self.on_open)
        self.btn_settings.clicked.connect(self.on_settings)
        self._update_action_buttons()

    def _on_filter_pill_clicked(self, id):
        keys = ["all", "active", "paused", "completed", "failed"]
        if id < 0 or id >= len(keys): return
        key = keys[id]
        
        self.proxy.setFilterKeyColumn(2) # Status column
        
        if key == "all":
            self.proxy.setFilterRegularExpression("")
        elif key == "active":
            self.proxy.setFilterRegularExpression("^(Downloading|Queued|Scheduled)$")
        elif key == "paused":
            self.proxy.setFilterRegularExpression("^Paused$")
        elif key == "completed":
            self.proxy.setFilterRegularExpression("^Completed$")
        elif key == "failed":
            self.proxy.setFilterRegularExpression("^Error$")

    # ---- left nav rail (categories + status groups) ----

    def _get_nav_items(self):
        items = [
            None, # DOWNLOADS
            ("grid", "All", "All"),
            None, # CATEGORIES
            ("archive", "Compressed", "Compressed"),
            ("cog", "Programs", "Programs"),
            ("film", "Videos", "Video"),
            ("music", "Music", "Music"),
            ("file-text", "Documents", "Documents"),
            ("folder", "Other", "Other"),
            None, # QUEUES
            ("list", "Main", "Queue:Main")
        ]
        for q in sorted(self.queue.queues.keys()):
            if q != "Main":
                items.append(("list", q, f"Queue:{q}"))
        items.append(("plus", "Add Queue", "__addqueue__"))   # opens the new-queue dialog
        # Status (Active/Paused/Completed/Failed) lives in the top filter pills
        # now — no duplicate STATUS section in the sidebar.
        return items

    SECTION_TITLES = ["DOWNLOADS", "CATEGORIES", "QUEUES"]
    
    def _get_icon_for_sidebar(self, icon_name):
        mapping = {
            "grid": ("▦", "#B5B5B5"),
            "archive": ("📂", "#B388FF"),
            "cog": ("⚙", "#82B1FF"),
            "film": ("🎬", "#FF80AB"),
            "music": ("🎵", "#FF8A80"),
            "file-text": ("📄", "#80D8FF"),
            "folder": ("📁", "#B5B5B5"),
            "list": ("≡", "#B5B5B5"),
            "plus": ("＋", "#00E676"),
            "arrow-down": ("↓", "#00E676"),
            "pause": ("⏸", "#FF9100"),
            "check": ("✓", "#00E676"),
            "alert-circle": ("!", "#FF1744")
        }
        return mapping.get(icon_name, ("•", "#B5B5B5"))

    def _build_sidebar(self):
        self.rail = QFrame()
        self.rail.setObjectName("sidebar")
        self.rail.setMinimumWidth(0)
        self.rail.setMaximumWidth(260)
        lay = QVBoxLayout(self.rail)
        lay.setContentsMargins(16, 20, 16, 20)
        lay.setSpacing(8)

        # Header
        h_row = QHBoxLayout()
        h_row.setContentsMargins(0, 0, 0, 0)
        self.title_icon = QLabel("⚡")
        self.title_icon.setStyleSheet(f"color: {ACCENT}; font-size: 24px; font-weight: 800; background: transparent;")
        self.title_lbl = QLabel("HyperFetch")
        self.title_lbl.setStyleSheet(f"color: {TEXT}; font-size: 20px; font-weight: 800; background: transparent;")
        
        self.btn_collapse = QPushButton("◀")
        self.btn_collapse.setStyleSheet(f"color: {MUTED}; font-size: 16px; background: transparent; border: none; font-weight: 700;")
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.clicked.connect(self._toggle_sidebar)
        
        h_row.addWidget(self.title_icon)
        h_row.addWidget(self.title_lbl)
        h_row.addStretch()
        h_row.addWidget(self.btn_collapse)
        lay.addLayout(h_row)
        
        lay.addSpacing(16)

        # New Download Button
        self.btn_add = QPushButton("＋ New Download")
        self.btn_add.setObjectName("primary")
        self.btn_add.setMinimumHeight(44)
        lay.addWidget(self.btn_add)
        
        lay.addSpacing(8)

        from gui.delegates import SidebarItemDelegate, CircularSpeedGraphWidget
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setProperty("collapsed", False)
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.viewport().installEventFilter(self)
        self.nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self._on_nav_context_menu)
        self.nav.setItemDelegate(SidebarItemDelegate(self.nav))
        self.nav.setStyleSheet("QListWidget { background: transparent; outline: none; }")
        
        self._populate_nav()
        self.nav.setCurrentRow(1) # row 0 is a header, row 1 is 'All'
        self.nav.currentItemChanged.connect(self._on_nav)
        lay.addWidget(self.nav, 1)

        # ---- Speed Graph & Stats in Sidebar ----
        self.graph_container = QWidget()
        self.graph_container.setObjectName("graphContainer")
        self.graph_container.setStyleSheet(f"""
            QWidget#graphContainer {{
                background: {SURFACE_2}; 
                border-radius: 12px;
                border: 1px solid {BORDER};
            }}
        """)
        g_lay = QHBoxLayout(self.graph_container)
        g_lay.setContentsMargins(12, 16, 12, 16)
        
        self.speed_graph = CircularSpeedGraphWidget()
        self.speed_graph.setFixedSize(80, 80)
        g_lay.addWidget(self.speed_graph)
        
        self.g_text_container = QWidget()
        self.g_text_container.setStyleSheet("background: transparent;")  # show the card, not BG
        g_text = QVBoxLayout(self.g_text_container)
        g_text.setContentsMargins(0, 0, 0, 0)
        g_text.setSpacing(2)
        
        self.total_speed = QLabel("0 B/s")
        self.total_speed.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {TEXT}; background: transparent;")
        lbl_s_sub = QLabel("Current Speed")
        lbl_s_sub.setStyleSheet(f"font-size: 11px; color: {MUTED}; background: transparent;")
        g_text.addWidget(self.total_speed)
        g_text.addWidget(lbl_s_sub)
        
        g_text.addSpacing(8)
        
        self.total_connections = QLabel("0")
        self.total_connections.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {TEXT}; background: transparent;")
        lbl_c_sub = QLabel("Connections")
        lbl_c_sub.setStyleSheet(f"font-size: 11px; color: {MUTED}; background: transparent;")
        g_text.addWidget(self.total_connections)
        g_text.addWidget(lbl_c_sub)
        
        g_text.addStretch()
        g_lay.addWidget(self.g_text_container)
        
        lay.addWidget(self.graph_container)
        
        lay.addSpacing(16)
        
        # Settings bottom button
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setStyleSheet(f"""
            QPushButton {{ color: {TEXT}; text-align: left; padding: 12px 16px; font-weight: 700; font-size: 14px; border-radius: 8px; }}
            QPushButton:hover {{ background: {SURFACE_2}; }}
        """)
        self.btn_settings.clicked.connect(self.on_settings)
        lay.addWidget(self.btn_settings)
        
        # Animation properties
        self.sidebar_anim = QPropertyAnimation(self.rail, b"maximumWidth")
        self.sidebar_anim.setEasingCurve(QEasingCurve.InOutCirc)
        self.sidebar_anim.setDuration(300)
        
        return self.rail

    def _toggle_sidebar(self):
        collapsed = self.nav.property("collapsed")
        self.nav.setProperty("collapsed", not collapsed)
        
        if not collapsed:
            # Collapse it
            self.sidebar_anim.setStartValue(260)
            self.sidebar_anim.setEndValue(80)
            self.g_text_container.hide()
            self.title_lbl.hide()
            self.btn_settings.setText("⚙")
            self.btn_settings.setStyleSheet(f"""
                QPushButton {{ color: {TEXT}; text-align: center; padding: 12px; font-weight: 700; font-size: 16px; border-radius: 8px; }}
                QPushButton:hover {{ background: {SURFACE_2}; }}
            """)
            self.btn_collapse.setText("☰")
            self.graph_container.hide()        # hide the whole stats card (no blob)
        else:
            # Expand it
            self.sidebar_anim.setStartValue(80)
            self.sidebar_anim.setEndValue(260)
            self.g_text_container.show()
            self.title_lbl.show()
            self.btn_settings.setText("⚙  Settings")
            self.btn_settings.setStyleSheet(f"""
                QPushButton {{ color: {TEXT}; text-align: left; padding: 12px 16px; font-weight: 700; font-size: 14px; border-radius: 8px; }}
                QPushButton:hover {{ background: {SURFACE_2}; }}
            """)
            self.btn_collapse.setText("☰")
            self.graph_container.show()        # restore the stats card

        self.nav.viewport().update() # trigger redraw
        self.sidebar_anim.start()

    def _populate_nav(self):
        was_blocked = self.nav.blockSignals(True)
        current_key = self.nav.currentItem().data(Qt.UserRole) if self.nav.currentItem() else "All"
        self.nav.clear()
        self._nav_count_items = {}
        self._nav_groups = {}
        if not hasattr(self, '_nav_header_state'):
            self._nav_header_state = {}
        
        section_iter = iter(self.SECTION_TITLES)
        current_header_row = -1
        
        for entry in self._get_nav_items():
            if entry is None:
                title = next(section_iter, "").upper()
                hdr = QListWidgetItem(title)
                hdr.setFlags(Qt.NoItemFlags)
                hdr.setData(Qt.UserRole + 1, "header")
                hdr.setData(Qt.UserRole + 2, title)
                self.nav.addItem(hdr)
                current_header_row = self.nav.count() - 1
                self._nav_groups[current_header_row] = []
                if current_header_row not in self._nav_header_state:
                    self._nav_header_state[current_header_row] = True
                # show the expand/collapse chevron from the start (was only set
                # on click, so the show/hide affordance looked invisible)
                exp = self._nav_header_state[current_header_row]
                hdr.setText(f"{title}   {'⌄' if exp else '›'}")
                continue
                
            icon, label, key = entry
            it = QListWidgetItem()
            # We use data roles instead of standard label to pass to delegate
            it.setData(Qt.UserRole, key)
            it.setData(Qt.DisplayRole, label) # fallback
            
            icon_char, icon_color = self._get_icon_for_sidebar(icon)
            it.setData(Qt.UserRole + 3, (icon_char, icon_color, label))
            it.setData(Qt.UserRole + 4, 0) # default count
            
            self.nav.addItem(it)
            self._nav_count_items[key] = it
            if current_header_row != -1:
                self._nav_groups[current_header_row].append(self.nav.count() - 1)
                
            if key == current_key:
                self.nav.setCurrentItem(it)
                
        # Apply collapsed state
        for row, is_expanded in self._nav_header_state.items():
            for child_row in self._nav_groups.get(row, []):
                self.nav.setRowHidden(child_row, not is_expanded)
                
        self._refresh_nav_counts()
        self.nav.blockSignals(was_blocked)

    def _refresh_nav_counts(self):
        """Live per-item counts. Nav keys (from _get_nav_items) must match the
        keys used here: category keys = utils.CATEGORIES + "Other"; status keys
        = Active/Paused/Done/Failed. Counts are stored on Qt.UserRole+4 — the
        SidebarItemDelegate reads them from there (item text is ignored)."""
        counts = {k: 0 for k in self._nav_count_items}
        with self.queue.cond:
            tasks = list(self.queue.tasks)
        counts["All"] = len(tasks)
        for t in tasks:
            qkey = f"Queue:{getattr(t, 'queue_name', 'Main')}"
            if qkey in counts:
                counts[qkey] += 1
            cat = utils.category_for(t.filename)
            if cat in counts:
                counts[cat] += 1

            if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED):
                status_key = "Active"
            elif t.status == T.PAUSED:
                status_key = "Paused"
            elif t.status == T.COMPLETED:
                status_key = "Done"
            elif t.status in (T.ERROR, T.CANCELLED):
                status_key = "Failed"
            else:
                status_key = ""
            if status_key in counts:
                counts[status_key] += 1

        for k, it in self._nav_count_items.items():
            it.setData(Qt.UserRole + 4, counts.get(k, 0))

        # Home-page filter pills (separate widget set, by lowercase label).
        if hasattr(self, "filter_pills"):
            pill_counts = {
                "all": len(tasks),
                "active": sum(1 for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED)),
                "paused": sum(1 for t in tasks if t.status == T.PAUSED),
                "completed": sum(1 for t in tasks if t.status == T.COMPLETED),
                "failed": sum(1 for t in tasks if t.status in (T.ERROR, T.CANCELLED)),
            }
            for key, btn in self.filter_pills.items():
                btn.setText(f"{key.capitalize()}  {pill_counts.get(key, 0)}")

    def eventFilter(self, obj, event):
        if obj == self.nav.viewport() and event.type() == QEvent.MouseButtonRelease:
            item = self.nav.itemAt(event.pos())
            if item and item.flags() == Qt.NoItemFlags and item.data(Qt.UserRole + 1) == "header":
                row = self.nav.row(item)
                if row in self._nav_groups:
                    is_expanded = not self._nav_header_state.get(row, True)
                    self._nav_header_state[row] = is_expanded
                    title = item.data(Qt.UserRole + 2)
                    item.setText(f"{title}   {'⌄' if is_expanded else '›'}")
                    for child_row in self._nav_groups[row]:
                        self.nav.setRowHidden(child_row, not is_expanded)
                return True
        return super().eventFilter(obj, event)

    def _on_nav(self, current, _previous):
        if current is None:
            return
        key = current.data(Qt.UserRole)
        if not key:                # section header — ignore
            return
        if key == "__addqueue__":
            self._add_queue_dialog()
            return
        self._set_filter(key)

    def _on_nav_context_menu(self, pos):
        item = self.nav.itemAt(pos)
        if not item: return
        key = item.data(Qt.UserRole)
        if key and str(key).startswith("Queue:"):
            qname = str(key).split(":", 1)[1]
            if qname != "Main":
                menu = QMenu(self)
                menu.setStyleSheet(f"QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}"
                                   f"QMenu::item:selected {{ background: {HOVER}; }}")
                menu.addAction("🗑  Delete Queue", lambda: self._delete_queue(qname))
                menu.exec(self.nav.viewport().mapToGlobal(pos))

    def _delete_queue(self, qname):
        if QMessageBox.question(self, "Delete Queue", f"Delete queue '{qname}'?\n\nTasks in this queue will be moved to the Main queue.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.queue.delete_queue(qname)
            self._save_settings()
            self._populate_nav()
            self.refresh()

    def _add_queue_dialog(self):
        """Prompt for a queue name, create it, and select it in the nav."""
        name, ok = QInputDialog.getText(self, "New Queue", "Queue name:")
        name = (name or "").strip()
        if not ok or not name:
            self.nav.setCurrentRow(0)        # don't leave "＋ New Queue" selected
            return
        if not self.queue.add_queue(name, self.max_concurrent):
            self._flash(f"Queue '{name}' already exists.")
            self.nav.setCurrentRow(0)
            return
        self._save_settings()
        self._populate_nav()
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.UserRole) == f"Queue:{name}":
                self.nav.setCurrentRow(i)
                break
        self._flash(f"Created queue '{name}'.")

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
            qn = f.split(":", 1)[1]
            tasks = [t for t in tasks if getattr(t, "queue_name", "Main") == qn]
        elif f == "Active":
            tasks = [t for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED)]
        elif f == "Paused":
            tasks = [t for t in tasks if t.status == T.PAUSED]
        elif f == "Done":
            tasks = [t for t in tasks if t.status == T.COMPLETED]
        elif f == "Finished":
            tasks = [t for t in tasks if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED)]
        elif f == "Failed":
            tasks = [t for t in tasks if t.status in (T.ERROR, T.CANCELLED)]
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
        # Delete works on ANY selected row: active -> cancel, finished -> remove
        # from the list. Was disabled for completed/error, so they couldn't be cleared.
        self.btn_cancel.setEnabled(bool(ts))

    def _mark_settings_dirty(self):
        """Debounced settings save — the next refresh tick (within ~10s) writes
        out widths/options the user just changed. Cheap to call from a header
        resize without writing the JSON on every pixel of drag."""
        self._settings_dirty = True

    def _flash(self, msg, secs=2.5):
        """Show a transient one-line message in the status bar."""
        self._flash_text = msg
        self._flash_until = time.time() + secs
        if hasattr(self, "status_lbl"):
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
                t.status = T.SCHEDULED
                t.is_scheduled = True
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
            if self.tray and self.tray.isVisible():
                self.tray.showMessage("Download Complete", f"{task.filename} finished downloading.", QSystemTrayIcon.Information, 5000)

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



    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
            if hasattr(self, "empty") and self.empty.isVisible():
                self.empty.setStyleSheet(f"background: {SURFACE_2}; border: 2px dashed {ACCENT}; border-radius: 12px;")
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        if hasattr(self, "empty"):
            self.empty.setStyleSheet(f"color: {MUTED}; font-size: 14px; background: transparent; border: none;")

    def dropEvent(self, e):
        if hasattr(self, "empty"):
            self.empty.setStyleSheet(f"color: {MUTED}; font-size: 14px; background: transparent; border: none;")
        url = ""
        if e.mimeData().hasUrls():
            url = e.mimeData().urls()[0].toString()
        elif e.mimeData().hasText():
            url = e.mimeData().text().strip()
            
        if url.startswith("http"):
            self._show_file_info(url=url)


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
            if t.status == T.ERROR and "403" in (t.error or ""):
                new_url, ok = QInputDialog.getText(
                    self, "URL Expired",
                    f"The server denied access (403) to '{t.filename}'.\n"
                    "If the URL expired, paste a fresh one. The download restarts "
                    "from the new link (the old partial may be from a different file):",
                    QLineEdit.Normal, t.url)
                if ok and new_url.strip():
                    fresh = new_url.strip()
                    # same scheme gate as the API boundary — this manual path
                    # would otherwise hand file://, ftp://, etc. to requests.
                    if not fresh.lower().startswith(("http://", "https://")):
                        self._flash("URL must start with http:// or https://")
                        continue
                    t.url = fresh
                    t.error = ""
                    # the fresh URL may point to a different-sized resource;
                    # drop the old segment plan so run() re-probes instead of
                    # resuming against stale byte offsets (which would corrupt).
                    t.segments = []
                    t.total_size = 0
                    t.downloaded = 0
                else:
                    continue
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

    def _on_delete_key(self):
        # Cancel active ones, completely remove terminal ones from the list
        ts = self._selected_tasks()
        if not ts:
            return
            
        active = [t for t in ts if t.status in (T.DOWNLOADING, T.QUEUED, T.PAUSED)]
        terminal = [t for t in ts if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED)]
        
        if active:
            for t in active:
                self.queue.cancel_task(t)
            self._flash(f"Cancelled {len(active)} active download(s).")
            
        if terminal:
            for t in terminal:
                if t in self.queue.tasks:
                    self.queue.tasks.remove(t)
            self._save_state()
            self._flash(f"Removed {len(terminal)} completed/cancelled download(s).")
            
        self.refresh()

    def _toggle_pause_resume(self):
        ts = self._selected_tasks()
        if not ts: return
        if any(t.status in (T.DOWNLOADING, T.QUEUED) for t in ts):
            self.on_pause()
        else:
            self.on_resume()

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

    def _remove_task(self, task):
        if QMessageBox.question(
                self, "Remove", f"Remove '{task.filename}' from the list?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.queue.remove_task(task)
            self._save_state()
            self._flash("Task removed.")
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
        # use the arg, NOT the module-global THEME — that global is a stale
        # `import *` copy that never tracks apply_theme's rebind, so reading it
        # pinned self.theme to "dark" and broke switching back.
        self.theme = "light" if name == "light" else "dark"
        self.setStyleSheet(build_qss())
        # one-shot inline styles on persistent header/footer widgets
        if hasattr(self, "subtitle"):
            self.subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        if hasattr(self, "status_lbl"):
            self.status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        if hasattr(self, "empty"):
            self.empty.setStyleSheet(f"color: {MUTED}; font-size: 14px; background: transparent;")
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
        """Open Settings as an in-app page (swapped into the content stack)
        rather than a separate modal window. Reuses SettingsDialog with its
        window-ness stripped (Qt.Widget); its Save/Cancel buttons fire the
        dialog's accepted/rejected signals which we hook to apply + return."""
        # already showing a settings page? don't stack a second one
        if getattr(self, "_settings_page", None) is not None:
            self.content_stack.setCurrentWidget(self._settings_page)
            return
        dlg = SettingsDialog(self, self.save_dir, self.max_concurrent, self.segments,
                             verify_tls=self.verify_tls, pair_token=self.pair_token,
                             theme=self.theme,
                             sched_en=self.scheduler_enabled,
                             sched_start=self.scheduler_start,
                             sched_stop=self.scheduler_stop)
        dlg.setWindowFlags(Qt.Widget)              # embed as child, not a window
        dlg.setMinimumSize(0, 0)                   # let it fit the content pane
        self._settings_page = dlg
        self.content_stack.addWidget(dlg)
        self.content_stack.setCurrentWidget(dlg)

        def _close():
            self.content_stack.setCurrentWidget(self.page_downloads)
            self.content_stack.removeWidget(dlg)
            dlg.deleteLater()
            self._settings_page = None

        def _apply():
            d, conc, segs, verify, theme, s_en, s_start, s_stop = dlg.values()
            if os.path.isdir(d):
                self.save_dir = d
            self.max_concurrent = conc
            self.segments = segs
            # concurrency now lives per-Queue; update the actual scheduling source
            # (and notify the parked scheduler) instead of a dead QueueManager attr.
            self.queue.set_max_concurrent("Main", conc)
            self.queue.segments = segs
            self.verify_tls = verify
            utils.VERIFY_TLS = verify
            if theme != self.theme:
                self._apply_theme(theme)
            self.scheduler_enabled = s_en
            self.scheduler_start = s_start
            self.scheduler_stop = s_stop
            self._save_settings()
            self._check_scheduler()
            _close()

        dlg.accepted.connect(_apply)
        dlg.rejected.connect(_close)

    def _check_scheduler(self):
        if not getattr(self, "scheduler_enabled", False):
            self._scheduler_active = False
            return
            
        import datetime
        now = datetime.datetime.now().time()
        curr_mins = now.hour * 60 + now.minute
        
        try:
            sh, sm = map(int, self.scheduler_start.split(":"))
            eh, em = map(int, self.scheduler_stop.split(":"))
        except Exception:
            return
            
        start_mins = sh * 60 + sm
        stop_mins = eh * 60 + em
        
        is_active = False
        if start_mins < stop_mins:
            is_active = start_mins <= curr_mins < stop_mins
        else:
            is_active = curr_mins >= start_mins or curr_mins < stop_mins
            
        if is_active and not self._scheduler_active:
            self._scheduler_active = True
            for t in self.queue.tasks:
                if getattr(t, "is_scheduled", False) and t.status in (T.SCHEDULED, T.PAUSED):
                    self.queue.resume_task(t)
        elif not is_active and self._scheduler_active:
            self._scheduler_active = False
            for t in self.queue.tasks:
                if t.status in (T.QUEUED, T.DOWNLOADING):
                    self.queue.pause_task(t)
                    t.status = T.SCHEDULED
                    t.is_scheduled = True
                    
    def _on_double_clicked(self, index):
        t = self._task_at(index)
        if t is not None:
            PropertiesDialog(self, t).exec()

    def _on_global_limit_changed(self, text):
        if "Unlimited" in text:
            bps = 0
        elif "Kb/s" in text:
            bps = int(text.split()[0]) * 1000 // 8
        elif "Mb/s" in text:
            bps = int(text.split()[0]) * 1000 * 1000 // 8
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

        # --- open (finished downloads) ---
        if t.status == T.COMPLETED:
            menu.addAction("📂  Open File", lambda: self._open_task_file(t))
            menu.addAction("📁  Open Folder", lambda: self._open_task_folder(t))
            menu.addSeparator()

        # --- run control ---
        if t.status == T.DOWNLOADING:
            menu.addAction("⏸  Pause", lambda: self._ctx_pause(t))
        if t.status in (T.PAUSED, T.ERROR, T.SCHEDULED):
            menu.addAction("▶  Resume", lambda: self._ctx_resume(t))
        if t.status in (T.QUEUED, T.PAUSED, T.SCHEDULED, T.ERROR):
            menu.addAction("🚀  Force Download", lambda: self._force_download(t))

        # --- per-task speed limit (not meaningful once finished) ---
        if t.status != T.COMPLETED:
            limit_menu = menu.addMenu("Set Speed Limit...")
            actions = [
                ("Unlimited", 0),
                ("100 Kb/s", 100 * 1000 // 8),
                ("500 Kb/s", 500 * 1000 // 8),
                ("1 Mb/s", 1000 * 1000 // 8),
                ("5 Mb/s", 5 * 1000 * 1000 // 8),
                ("Custom...", -1)
            ]
            for name, bps in actions:
                act = limit_menu.addAction(name)
                act.setCheckable(True)
                if bps == t.speed_limit or (bps == -1 and t.speed_limit not in [a[1] for a in actions[:-1]]):
                    act.setChecked(True)
                act.triggered.connect(lambda checked=False, val=bps, task=t: self._set_task_limit(task, val))

        # --- reorder within a queue (only QUEUED tasks still waiting in the heap;
        #     move() is a no-op for running/paused/finished tasks) ---
        if t.status == T.QUEUED:
            menu.addSeparator()
            menu.addAction("⬆  Move to top", lambda: self._move_task(t, "top"))
            menu.addAction("↑  Move up", lambda: self._move_task(t, "up"))
            menu.addAction("↓  Move down", lambda: self._move_task(t, "down"))
            menu.addAction("⬇  Move to bottom", lambda: self._move_task(t, "bottom"))

        # --- move between queues ---
        menu.addSeparator()
        q_menu = menu.addMenu("Move to Queue")
        for q in self.queue.queues.values():
            act = q_menu.addAction(q.name)
            if getattr(t, "queue_name", "Main") == q.name:
                act.setCheckable(True)
                act.setChecked(True)
                act.setEnabled(False)
            act.triggered.connect(lambda checked=False, name=q.name, task=t: self._move_task_to_queue(task, name))

        # --- misc ---
        menu.addSeparator()
        menu.addAction("🔗  Copy URL", lambda: QApplication.clipboard().setText(t.url or ""))
        menu.addAction("ℹ  Properties", lambda: PropertiesDialog(self, t).exec())
        menu.addAction("🗑  Remove", lambda: self._remove_task(t))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _ctx_pause(self, t):
        self.queue.pause_task(t)
        self._save_tick = 10
        self.refresh()

    def _ctx_resume(self, t):
        self.queue.resume_task(t)
        self._save_tick = 10
        self.refresh()

    def _open_task_file(self, t):
        """Open the downloaded file; for a torrent (save_path is a placeholder,
        payload is a folder) open the destination folder instead."""
        target = t.save_path
        if not os.path.exists(target):
            folder = os.path.dirname(t.save_path) or "."
            target = folder if (_torrent.is_torrent_task(t.url, t.filename)
                                and os.path.isdir(folder)) else ""
        if not target:
            self._flash("File not found — it may have been moved or deleted.")
            return
        try:
            os.startfile(target)
        except OSError:
            pass

    def _open_task_folder(self, t):
        path = os.path.normpath(t.save_path)
        try:
            if os.path.isdir(path):
                os.startfile(path)                  # torrent folder: open its contents
            elif os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(os.path.dirname(path) or ".")
        except OSError:
            try:
                os.startfile(os.path.dirname(path) or ".")
            except OSError:
                pass

    def _force_download(self, task):
        self.queue.force_start(task)
        self._save_tick = 10
        self.refresh()
        
    def _move_task_to_queue(self, task, qname):
        # route through the queue manager so the scheduler is woken (a bare
        # field write left a QUEUED task idle in a now-free queue).
        self.queue.move_to_queue(task, qname)
        self._save_tick = 10
        self.refresh()

    def _set_task_limit(self, task, bps):
        if bps == -1:
            val, ok = QInputDialog.getInt(self, "Custom Speed Limit", 
                                          "Enter limit in Kb/s (0 for unlimited):",
                                          value=int(task.speed_limit * 8 / 1000), min=0, max=999999)
            if ok:
                bps = val * 1000 // 8
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
        total_conns = 0
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
                # Count connections (1 for HLS/non-segmented, or len(segments))
                if getattr(t, "is_hls", False) or not t.segments:
                    total_conns += 1
                else:
                    # Approximation: assume all created segments are active until completed
                    total_conns += sum(1 for s in t.segments if not s.complete)

        self.model.set_tasks(tasks)
        self.model.refresh_dynamic()
        self._refresh_nav_counts()

        self.speed_graph.add_value(total_bps)
        disp_bps = self.speed_graph.current()
        self.total_speed.setText(f"{human_speed(disp_bps)}" if disp_bps > 1 else "0 b/s")
        self.total_connections.setText(str(total_conns))
        
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
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon, self)
        self.tray.setToolTip("HyperFetch")
        menu = QMenu(self)
        menu.addAction("Show/Hide", self._toggle_window)
        menu.addAction("Quit", self._quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def _quit_app(self):
        self._quit_requested = True
        self.close()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_window()

    def closeEvent(self, e):
        if not self._quit_requested and self.tray and self.tray.isVisible():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Exit HyperFetch")
            msg_box.setText("Do you want to minimize to the taskbar or close the application?")
            min_btn = msg_box.addButton("Minimize to Tray", QMessageBox.AcceptRole)
            close_btn = msg_box.addButton("Close App", QMessageBox.DestructiveRole)
            cancel_btn = msg_box.addButton("Cancel", QMessageBox.RejectRole)
            msg_box.exec()

            clicked = msg_box.clickedButton()
            msg_box.deleteLater()   # parented to self -> would otherwise leak per close
            if clicked == min_btn:
                e.ignore()
                self.hide()
                if getattr(self, "_tray_notice_shown", False) is False:
                    self.tray.showMessage("HyperFetch", "Minimized to system tray. Right-click to quit.", 
                                          QSystemTrayIcon.Information, 3000)
                    self._tray_notice_shown = True
                return
            elif clicked == close_btn:
                self._quit_requested = True
            else:
                e.ignore()
                return

        # Proceed with actual shutdown
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
            # last chunk is durable on disk. Drive Qt's event loop while we
            # wait so the window stays responsive (a blocking cond.wait would
            # freeze paint/hover and trip Windows' "Not Responding").
            deadline = time.monotonic() + 8.0
            while self.queue.active > 0 and time.monotonic() < deadline:
                QApplication.processEvents()
                self.queue.wait_active(timeout=0.1)
            if self.queue.active > 0:
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

