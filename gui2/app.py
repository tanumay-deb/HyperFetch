"""The HyperFetch main window (DownloadAppV2).

Owns the backend wiring (settings, QueueManager, state restore, embedded Flask
server, 500ms refresh) behind a widget-based view: Sidebar | (top bar + grouped
DownloadList). Task actions, settings application, keyboard shortcuts, and the
tray/scheduler live in mixins (gui2/app_settings.py, app_actions.py,
app_shortcuts.py, app_system.py) to keep this file focused on the view.

Run with:  python main.py
"""
import os
import time
import threading
from collections import deque

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QButtonGroup,
    QComboBox, QMenu, QApplication, QDialog, QInputDialog, QMessageBox,
    QLabel, QSystemTrayIcon
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QByteArray, QEvent
)
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QAction, QFont

import task as T
import utils
import torrent as _torrent
from queue_manager import QueueManager
from api_server import run_server, PORT

from gui.theme import APP_VERSION, apply_theme, resource_path, human_speed
from gui.icons import themed_icon
from gui.dialogs import PropertiesDialog
from gui2.dialogs.settings import SettingsDialogV2
from gui2.dialogs.complete import CompleteDialog
from gui2.toast import ToastManager
from gui2 import palette
from gui2.sidebar import Sidebar
from gui2.download_list import DownloadList
from gui2.details_drawer import DetailsDrawer
from gui2.dialogs.new_download import NewDownloadDialog
from gui2.app_settings import SettingsMixin
from gui2.app_actions import ActionsMixin
from gui2.app_shortcuts import ShortcutsMixin
from gui2.app_system import SystemMixin



class DownloadAppV2(SettingsMixin, ActionsMixin, ShortcutsMixin, SystemMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("HyperFetch")
        self.setMinimumSize(940, 560)
        self.resize(1180, 720)               # default; _restore_window overrides if saved
        self.setAcceptDrops(True)
        from gui2.brand import brand_icon
        self.setWindowIcon(brand_icon())     # same logo as the in-app brand + settings

        self._settings_path = os.path.join(utils.app_data_dir(), "settings.json")
        self._state_path = os.path.join(utils.app_data_dir(), "downloads.json")
        self._load_settings()

        self.queue = QueueManager(queues=self.queues_config, segments=self.segments)
        self.pending = deque()
        self._speed = {}              # id -> (last_dl, last_t, bps)
        self._filter = "All"
        self._search = ""
        self._completed_seen = None
        self._errored_seen = None
        self._sidebar_collapsed = False
        self._scheduler_active = False
        self._clip_last = ""
        self._quit_requested = False

        self._build_ui()
        self._setup_tray()
        self._setup_shortcuts()
        self._apply_appearance()
        self._load_state()
        self._start_server()
        self._restore_window()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._check_scheduler)
        self._sched_timer.start(60000)
        QTimer.singleShot(1000, self._check_scheduler)
        self.refresh()

    # ------------------------------------------------------------- settings/state
    def _load_state(self):
        for d in utils.load_json(self._state_path, []):
            try:
                self.queue.add_task(T.DownloadTask.from_dict(d), start=False)
            except (KeyError, TypeError, ValueError):
                continue
        # Orphan .hfdownload sweep — clean up temp files from crashed sessions
        import glob, tempfile
        known_ids = {t.id for t in self.queue.tasks}
        for pattern_dir in (tempfile.gettempdir(),):
            for temp_file in glob.glob(os.path.join(pattern_dir, "*.hfdownload")):
                tid = os.path.splitext(os.path.basename(temp_file))[0]
                if tid not in known_ids:
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass

    def _save_state(self):
        utils.save_json(self._state_path,
                        [t.to_dict() for t in self.queue.tasks if t.status != T.CANCELLED])

    def _start_server(self):
        def serve():
            try:
                run_server(self.queue, self.save_dir, PORT, pending=self.pending, token=self.pair_token)
            except OSError:
                pass
        threading.Thread(target=serve, daemon=True).start()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        self.setStyleSheet(palette.qss())
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.filterChanged.connect(self._set_filter)
        self.sidebar.newDownload.connect(self._new_download)
        self.sidebar.openSettings.connect(self._open_settings)
        self.sidebar.toggleCollapse.connect(self._toggle_sidebar)
        self.sidebar.manageQueues.connect(self._open_queues)
        self.sidebar.openHistory.connect(self._open_history)
        root.addWidget(self.sidebar)
        # animate min AND max together so the width is exact every frame (child
        # min-widths can't fight it) -> a smooth slide instead of a jumpy reflow
        self._anim_min = QPropertyAnimation(self.sidebar, b"minimumWidth")
        self._anim_max = QPropertyAnimation(self.sidebar, b"maximumWidth")
        self.sidebar_anim = QParallelAnimationGroup(self)
        for a in (self._anim_min, self._anim_max):
            a.setDuration(260)
            a.setEasingCurve(QEasingCurve.InOutCubic)
            self.sidebar_anim.addAnimation(a)
        self._sidebar_target = 260
        self.sidebar_anim.finished.connect(self._on_sidebar_anim_done)

        main = QWidget()
        main.setObjectName("mainPane")
        mlay = QVBoxLayout(main)
        mlay.setContentsMargins(22, 18, 22, 14)
        mlay.setSpacing(14)

        # top bar
        top = QHBoxLayout()
        top.setSpacing(10)
        # (sidebar show/hide lives in the sidebar header — no duplicate here)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search downloads…")
        self.search.setToolTip("Search by name/URL, or filter with tokens:\n"
                               "status:downloading · category:video · size:>100mb")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)

        # filter pills
        self.pills = {}
        self.pill_group = QButtonGroup(self)
        for i, label in enumerate(["All", "Active", "Paused", "Completed", "Failed"]):
            b = QPushButton(label)
            b.setObjectName("pill")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            if i == 0:
                b.setChecked(True)
            self.pill_group.addButton(b, i)
            self.pills[label] = b
            b.clicked.connect(lambda _=False, k=label: self._set_filter(k))
            top.addWidget(b)

        self.sort = QPushButton("Sort: Added (↓)")
        self.sort.setObjectName("pill")
        self.sort.setCursor(Qt.PointingHandCursor)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self.sort)
        menu.setStyleSheet(f"QMenu {{ background: {palette.COLORS['surface']}; color: {palette.COLORS['text']}; border: 1px solid {palette.COLORS['border']}; }} QMenu::item:selected {{ background: {palette.COLORS['surface2']}; }}")
        for label in ["Added", "Name", "Size", "Progress"]:
            # Need to capture the loop variable properly
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False, k=label: self._on_sort_changed(k))
        self.sort.setMenu(menu)
        self._last_sort_idx = 0
        top.addWidget(self.sort)
        
        self.del_btn = QPushButton("Delete")
        self.del_btn.setObjectName("delBtn")
        self.del_btn.setStyleSheet(f"color: white; background: {palette.COLORS['error']}; font-weight: 600; padding: 6px 16px; border-radius: 6px; border: none;")
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.clicked.connect(self._del_selected)
        top.addWidget(self.del_btn)
        
        mlay.addLayout(top)

        self.list = DownloadList()
        self.list.action.connect(self._on_card_action)
        self.list.selectionChanged.connect(self._on_selection_changed)
        self.list.quickAction.connect(self._quick_action)
        self.list.blankClicked.connect(self._on_blank_clicked)
        mlay.addWidget(self.list, 1)

        root.addWidget(main, 1)

        # details drawer overlays the right edge of the whole window
        self.drawer = DetailsDrawer(self)
        self.drawer.action.connect(self._on_card_action)
        self._toasts = ToastManager(self)

        # drag-drop overlay (shown while a link hovers over the window)
        self._drag_overlay = QLabel("Drop a link to add", self)
        self._drag_overlay.setAlignment(Qt.AlignCenter)
        self._drag_overlay.setStyleSheet(
            f"background: rgba(124,92,255,0.12); color: {palette.COLORS['text']};"
            f"border: 2px dashed {palette.COLORS['accent']}; border-radius: 16px;"
            "font-size: 20px; font-weight: 700;")
        self._drag_overlay.hide()

    def _on_sort_changed(self, key):
        """Toggle ascending/descending when the same sort is clicked again."""
        keys = ["Added", "Name", "Size", "Progress"]
        idx = keys.index(key)
        
        if not hasattr(self, "_sort_asc"):
            self._sort_asc = False
            
        if idx == getattr(self, "_last_sort_idx", 0):
            self._sort_asc = not getattr(self, "_sort_asc", False)
        else:
            self._sort_asc = False
            self._last_sort_idx = idx
            
        arrow = "↑" if self._sort_asc else "↓"
        self.sort.setText(f"Sort: {key} ({arrow})")
        self.refresh()

    # ------------------------------------------------------------- filtering
    def _set_filter(self, key):
        self._filter = key
        self.sidebar.set_active(self._filter)
        self.refresh()

    def _on_search(self, text):
        self._search = text.strip().lower()
        self.refresh()

    def _visible_tasks(self):
        tasks = [t for t in self.queue.tasks if getattr(self, "_filter", "All") == "All" or 
                 (self._filter == "Active" and t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED)) or
                 (self._filter == "Paused" and t.status == T.PAUSED) or
                 (self._filter == "Completed" and t.status == T.COMPLETED) or
                 (self._filter == "Failed" and t.status in (T.ERROR, T.CANCELLED)) or
                 utils.category_for(t.filename) == self._filter]
                 
        if getattr(self, "_search", ""):
            from gui2 import search
            tasks = search.filter_tasks(tasks, self._search)

        idx = getattr(self, "_last_sort_idx", 0)
        asc = getattr(self, "_sort_asc", False)
        
        if idx == 1:   # Name
            tasks.sort(key=lambda t: t.filename.lower(), reverse=not asc)
        elif idx == 2: # Size
            tasks.sort(key=lambda t: t.total_size, reverse=not asc)
        elif idx == 3: # Progress
            tasks.sort(key=lambda t: t.percent, reverse=not asc)
        else:          # Added (default)
            tasks.sort(key=lambda t: getattr(t, "added", 0), reverse=not asc)
        return tasks

    # ------------------------------------------------------------- refresh loop
    def refresh(self):
        self._drain_pending()
        now = time.time()
        conns = 0
        total_bps = 0.0
        for t in self.queue.tasks:
            last_dl, last_t, bps = self._speed.get(t.id, (t.downloaded, now, 0.0))
            if t.status == T.DOWNLOADING and now > last_t:
                inst = (t.downloaded - last_dl) / (now - last_t)
                bps = 0.6 * bps + 0.4 * max(0.0, inst) if bps else max(0.0, inst)
            elif t.status != T.DOWNLOADING:
                bps = 0.0
            self._speed[t.id] = (t.downloaded, now, bps)
            if t.status == T.DOWNLOADING:
                total_bps += bps
                if _torrent.is_torrent_task(t.url, t.filename):
                    conns += getattr(t, "tor_conns", 0)
                else:
                    conns += sum(1 for s in t.segments if not s.complete)

        speeds = {tid: v[2] for tid, v in self._speed.items()}
        self.list.set_tasks(self._visible_tasks(), speeds)
        self.sidebar.set_counts(self._counts())
        self.sidebar.set_stats(total_bps, conns)
        if getattr(self, "_active_settings_dlg", None):
            self._active_settings_dlg.update_live(total_bps, conns)
        if self.drawer.isVisible() and self.drawer._tid:
            dt = self.queue.get_task(self.drawer._tid)
            if dt:
                self.drawer.update_live(dt, speeds.get(dt.id, 0.0))
            else:
                self.drawer.close_drawer()
        self._check_completions()

    def _counts(self):
        tasks = self.queue.tasks
        c = {"All": len(tasks)}
        for key in list(utils.CATEGORIES) + ["Other"]:
            c[key] = sum(1 for t in tasks if utils.category_for(t.filename) == key)
        c["Active"] = sum(1 for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED))
        c["Paused"] = sum(1 for t in tasks if t.status == T.PAUSED)
        c["Completed"] = sum(1 for t in tasks if t.status == T.COMPLETED)
        c["Failed"] = sum(1 for t in tasks if t.status in (T.ERROR, T.CANCELLED))
        return c

    def _check_completions(self):
        done = {t.id for t in self.queue.tasks if t.status == T.COMPLETED}
        errd = {t.id for t in self.queue.tasks if t.status in (T.ERROR, T.CANCELLED)}
        if self._completed_seen is None:           # seed on first tick; no popups on restore
            self._completed_seen, self._errored_seen = done, errd
            return
        wc = self._extras.get("when_complete", "Show notification")
        for t in self.queue.tasks:
            if t.status == T.COMPLETED and t.id not in self._completed_seen:
                try:
                    import history
                    history.record(t)
                except Exception:
                    pass
                if wc != "Do nothing":
                    self._toasts.show("success", "Download Complete", t.filename or "download")
                    if self.tray and self.tray.isVisible():
                        self.tray.showMessage("Download Complete", t.filename or "download",
                                              QSystemTrayIcon.Information, 4000)
                if wc == "Open file" or self._extras.get("open_on_complete"):
                    self._open_file(t)
                elif wc == "Open folder":
                    self._open_folder(t)
                if wc != "Do nothing":
                    dlg = CompleteDialog(self, t)
                    dlg.viewInList.connect(lambda *_: self._set_filter("All"))
                    dlg.show()
            elif t.status == T.ERROR and t.id not in self._errored_seen:
                if wc != "Do nothing":
                    self._toasts.show("error", "Download Failed", (t.error or t.filename or "")[:60])
                    if self.tray and self.tray.isVisible():
                        self.tray.showMessage("Download Failed", t.filename or "download",
                                              QSystemTrayIcon.Critical, 4000)
        self._completed_seen, self._errored_seen = done, errd

    def _drain_pending(self):
        while self.pending:
            item = self.pending.popleft()
            url = item.get("url", "")
            if not url:
                continue
            self._add_download(url, item.get("filename", ""), item.get("headers"), flash=True)

    # ------------------------------------------------------------- actions
    @staticmethod
    def _looks_like_url(s):
        s = (s or "").strip().lower()
        return s.startswith(("http://", "https://", "magnet:")) or s.endswith(".torrent")

    def _open_queues(self):
        from gui2.dialogs.queues import QueueManagerDialog
        QueueManagerDialog(self, self.queue).exec()
        self._save_settings()        # persist queue list + concurrencies
        self.refresh()

    def _open_history(self):
        from gui2.dialogs.history import HistoryDialog
        HistoryDialog(self).exec()

    def _new_download(self):
        clip = QApplication.clipboard().text().strip()
        prefill = clip if self._looks_like_url(clip) else ""
        self._add_download(prefill, "", None, flash=False)

    def _quick_action(self, kind):
        """Empty-state quick buttons: new / paste / torrent / magnet."""
        if kind in ("new", "paste"):
            self._new_download()                 # New Download dialog (prefills clipboard URL)
        elif kind == "torrent":
            from PySide6.QtWidgets import QFileDialog
            f, _ = QFileDialog.getOpenFileName(self, "Open Torrent", self.save_dir,
                                               "Torrent files (*.torrent)")
            if f:
                self._add_download(f, "", None)  # NewDownloadDialog opens on the Torrent tab
        elif kind == "magnet":
            clip = QApplication.clipboard().text().strip()
            # use the clipboard magnet if present (opens on the Magnet tab), else
            # the normal dialog so the user can paste one
            self._add_download(clip if clip.lower().startswith("magnet:") else "", "", None)

    def _add_download(self, url, suggested, headers, flash=False):
        queues = list(self.queue.queues.keys()) or ["Main"]
        dlg = NewDownloadDialog(self, self.save_dir, queues, self.segments,
                                url=url, suggested=suggested, headers=headers)
        dq = self._extras.get("default_queue")          # Settings -> Downloads
        if dq and dq in queues:
            dlg.q.setCurrentText(dq)
        dlg.start_now.setChecked(bool(self._extras.get("auto_start", True)))
        if flash:
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.raise_(); self.activateWindow()
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["url"]:
            return
        # duplicate detection — same URL already in the list
        existing = next((x for x in self.queue.tasks if x.url == v["url"]), None)
        if existing:
            ans = QMessageBox.question(
                self, "Already added",
                f"This URL is already in the list as “{existing.filename}”.\nAdd it again anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans not in (QMessageBox.Yes, QMessageBox.StandardButton.Yes):
                return
        folder = v["save_dir"] if os.path.isdir(v["save_dir"]) else self.save_dir
        if v["category"] != "Auto":
            folder = os.path.join(folder, v["category"])
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                folder = self.save_dir
        filename = utils.filename_from_url(v["url"], v["filename"] or suggested)
        save_path = utils.unique_path(folder, filename)
        t = T.DownloadTask(v["url"], save_path, filename=filename,
                           headers=v["headers"], priority=v["priority"],
                           queue_name=v["queue"])
        t.use_ytdlp = v.get("use_ytdlp", False)     # route through yt-dlp engine
        t.yt_format = v.get("yt_format", "")        # chosen quality/format string
        self.queue.segments = v["connections"]      # active per-download connections
        if v["start_now"]:
            self.queue.add_task(t)
        else:
            t.status = T.PAUSED
            self.queue.add_task(t, start=False)
        self.segments = v["connections"]
        self._save_state()
        self.refresh()

    def _open_settings(self):
        cur_accent = next((k for k, v in palette.ACCENTS.items() if v == palette.COLORS["accent"]), "purple")
        dlg = SettingsDialogV2(
            self, save_dir=self.save_dir, max_concurrent=self.max_concurrent,
            segments=self.segments, verify_tls=self.verify_tls, pair_token=self.pair_token,
            theme=self.theme, accent=cur_accent, sched_en=self.scheduler_enabled,
            sched_start=self.scheduler_start, sched_stop=self.scheduler_stop,
            extras=getattr(self, "_extras", {}))
        self._active_settings_dlg = dlg
        res = dlg.exec()
        self._active_settings_dlg = None
        if res != QDialog.Accepted:
            return
        self._apply_settings(dlg.values())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drawer"):
            self.drawer.reposition()
        if hasattr(self, "_toasts"):
            self._toasts.reposition()
        if getattr(self, "_drag_overlay", None) and self._drag_overlay.isVisible():
            self._drag_overlay.setGeometry(self.rect().adjusted(40, 40, -40, -40))

    # ------------------------------------------------------------- window state
    def _restore_window(self):
        g = self._extras.get("geometry")
        if g:
            try:
                self.restoreGeometry(QByteArray.fromBase64(g.encode()))
            except Exception:
                pass
        lf = self._extras.get("last_filter")
        if lf:
            self._set_filter(lf)

    def _check_clipboard(self):
        if not self._extras.get("clipboard_monitor", False):
            return
        txt = QApplication.clipboard().text().strip()
        if txt and txt != self._clip_last and self._looks_like_url(txt):
            self._clip_last = txt
            if not any(x.url == txt for x in self.queue.tasks):
                self._toasts.show("info", "Link detected", "Use + New Download to add it.")

    # ------------------------------------------------------------- sidebar collapse
    def _toggle_sidebar(self):
        self._sidebar_collapsed = not self._sidebar_collapsed
        target = 72 if self._sidebar_collapsed else 260
        self._sidebar_target = target
        # Collapsing: hide labels BEFORE shrinking so text never clips mid-slide.
        # Expanding: keep the rail look until the slide finishes (see _on_sidebar_anim_done),
        # otherwise labels pop in at narrow width and clip during the widen.
        if self._sidebar_collapsed:
            self.sidebar.set_collapsed(True)
        cur = self.sidebar.width()
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setMaximumWidth(16777215)        # free both bounds before tween
        self.sidebar_anim.stop()
        for a in (self._anim_min, self._anim_max):
            a.setStartValue(cur)
            a.setEndValue(target)
        self.sidebar_anim.start()

    def _on_sidebar_anim_done(self):
        self.sidebar.setFixedWidth(self._sidebar_target)
        if not self._sidebar_collapsed:
            self.sidebar.set_collapsed(False)         # reveal labels only once fully wide

    # ------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasText() or e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_overlay.setGeometry(self.rect().adjusted(40, 40, -40, -40))
            self._drag_overlay.show()
            self._drag_overlay.raise_()

    def dragLeaveEvent(self, _e):
        self._drag_overlay.hide()

    def dropEvent(self, e):
        self._drag_overlay.hide()
        md = e.mimeData()
        url = md.text().strip() if md.hasText() else (md.urls()[0].toString() if md.urls() else "")
        if url:
            self._add_download(url, "", None)

    # ------------------------------------------------------------- window events
    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and self.isMinimized():
            if self.tray and self.tray.isVisible() and self._extras.get("minimize_tray", True):
                QTimer.singleShot(0, self.hide)
        elif e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._check_clipboard()
        super().changeEvent(e)

    def closeEvent(self, e):
        # explicit Quit (tray menu) always exits
        if self._quit_requested:
            self._shutdown(e)
            return
        beh = self._extras.get("close_behavior", "Ask every time")
        have_tray = bool(self.tray and self.tray.isVisible())
        if beh == "Minimize to tray" and have_tray:
            e.ignore(); self.hide(); return
        if beh == "Exit" or not have_tray:
            self._shutdown(e); return

        # Ask: Minimize / Exit / Cancel (+ remember choice)
        from PySide6.QtWidgets import QCheckBox
        box = QMessageBox(self)
        box.setWindowTitle("Close HyperFetch")
        box.setIcon(QMessageBox.Question)
        box.setText("Minimize HyperFetch to the tray, or exit completely?")
        mini = box.addButton("Minimize to tray", QMessageBox.AcceptRole)
        quit_b = box.addButton("Exit", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        remember = QCheckBox("Remember my choice")
        box.setCheckBox(remember)
        box.exec()
        clicked = box.clickedButton()
        if clicked is mini:
            if remember.isChecked():
                self._extras["close_behavior"] = "Minimize to tray"
                self._save_settings()
            e.ignore(); self.hide()
        elif clicked is quit_b:
            if remember.isChecked():
                self._extras["close_behavior"] = "Exit"
            self._shutdown(e)
        else:                                   # Cancel / dialog dismissed
            e.ignore()

    def _shutdown(self, e):
        self._save_state()
        self._extras["geometry"] = bytes(self.saveGeometry().toBase64().data()).decode()
        self._extras["last_filter"] = self._filter
        self._save_settings()
        # graceful: pause active downloads, shutdown scheduler, wait for writes
        for t in self.queue.tasks:
            if t.status in (T.DOWNLOADING, T.QUEUED):
                t.request_pause()
        self.queue.shutdown()
        self.queue.wait_active(2.0)
        if self.tray:
            self.tray.hide()
        super().closeEvent(e)


def run_v2():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtGui import QFont
    app.setFont(QFont("Segoe UI", 10))
    win = DownloadAppV2()
    # launch behavior (Settings -> General -> On application launch)
    launch = win._extras.get("launch", "Show main window")
    if launch == "Start in tray" and win.tray and win.tray.isVisible():
        pass                                   # stay hidden in the tray
    elif launch == "Start minimized":
        win.showMinimized()
    else:
        win.show()
    return app.exec()


def _self_test_v2():
    """Headless smoke check: construct, tick once, exit 0."""
    import sys
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    win = DownloadAppV2()
    win.refresh()
    
    # Extended UI testing
    from gui2.dialogs.settings import SettingsDialogV2
    from gui2.dialogs.complete import CompleteDialog
    from gui2.palette import ACCENTS, COLORS
    import task as T
    
    cur_accent = next((k for k, v in ACCENTS.items() if v == COLORS["accent"]), "purple")
    s_dlg = SettingsDialogV2(
        win, save_dir=win.save_dir, max_concurrent=win.max_concurrent,
        segments=win.segments, verify_tls=win.verify_tls, pair_token=win.pair_token,
        theme=win.theme, accent=cur_accent, sched_en=win.scheduler_enabled,
        sched_start=win.scheduler_start, sched_stop=win.scheduler_stop,
        extras=getattr(win, "_extras", {}))
        
    dt = T.DownloadTask("http://test.com", "test.bin")
    c_dlg = CompleteDialog(win, dt)

    print(f"v2 selftest OK v{APP_VERSION}")
    return 0
