"""HyperFetch v2 GUI shell (DownloadAppV2).

Owns the same backend wiring the v1 window did (settings, QueueManager, state
restore, embedded Flask server, 500ms refresh) but with a clean, widget-based
View: Sidebar | (top bar + grouped DownloadList). Backend modules are reused
untouched. Some dialogs are still the v1 ones (New Download / Settings /
Complete) until their v2 replacements land — the main screen is the rewrite.

Run with:  python main.py --v2
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
from gui.dialogs import PropertiesDialog
from gui2.dialogs.settings import SettingsDialogV2
from gui2.dialogs.complete import CompleteDialog
from gui2.toast import ToastManager
from gui2 import palette
from gui2.sidebar import Sidebar
from gui2.download_list import DownloadList
from gui2.details_drawer import DetailsDrawer
from gui2.dialogs.new_download import NewDownloadDialog

MAX_CONCURRENT = 3
SEGMENTS = 8


class DownloadAppV2(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("HyperFetch")
        self.setMinimumSize(940, 560)
        self.resize(1180, 720)               # default; _restore_window overrides if saved
        self.setAcceptDrops(True)
        for ic in (resource_path("assets", "icon.ico"), resource_path("assets", "icon.png")):
            if os.path.exists(ic):
                self.setWindowIcon(QIcon(ic))
                break

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
    def _load_settings(self):
        s = utils.load_json(self._settings_path, {})
        self._extras = dict(s)               # preserves UI-only prefs across saves
        self.save_dir = s.get("save_dir") or utils.default_download_dir()
        if not os.path.isdir(self.save_dir):
            self.save_dir = utils.default_download_dir()
        self.max_concurrent = int(s.get("max_concurrent", MAX_CONCURRENT))
        self.segments = int(s.get("segments", SEGMENTS))
        self.global_speed_limit = int(s.get("global_speed_limit", 0))
        utils.global_limiter.set_limit(self.global_speed_limit)
        self.verify_tls = bool(s.get("verify_tls", True))
        utils.VERIFY_TLS = self.verify_tls
        self.theme = s.get("theme", "dark")
        apply_theme(self.theme)                       # for reused v1 dialogs
        palette.set_accent(s.get("accent", "purple"))  # v2 widgets
        self.pair_token = utils.get_or_create_token()
        self.queues_config = s.get("queues", [{"name": "Main", "max_concurrent": self.max_concurrent}])
        self.scheduler_enabled = bool(s.get("scheduler_enabled", False))
        self.scheduler_start = s.get("scheduler_start", "02:00")
        self.scheduler_stop = s.get("scheduler_stop", "08:00")
        self._apply_network_settings()

    def _save_settings(self):
        data = dict(getattr(self, "_extras", {}))
        data.update({
            "save_dir": self.save_dir,
            "max_concurrent": self.max_concurrent,
            "segments": self.segments,
            "global_speed_limit": getattr(self, "global_speed_limit", 0),
            "verify_tls": getattr(self, "verify_tls", True),
            "theme": getattr(self, "theme", "dark"),
            "accent": next((k for k, v in palette.ACCENTS.items() if v == palette.COLORS["accent"]), "purple"),
            "queues": [{"name": q.name, "max_concurrent": q.max_concurrent} for q in self.queue.queues.values()],
            "scheduler_enabled": getattr(self, "scheduler_enabled", False),
            "scheduler_start": getattr(self, "scheduler_start", "02:00"),
            "scheduler_stop": getattr(self, "scheduler_stop", "08:00"),
        })
        utils.save_json(self._settings_path, data)

    def _load_state(self):
        for d in utils.load_json(self._state_path, []):
            try:
                self.queue.add_task(T.DownloadTask.from_dict(d), start=False)
            except (KeyError, TypeError, ValueError):
                continue

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
        self.sidebar_anim.finished.connect(lambda: self.sidebar.setFixedWidth(self._sidebar_target))

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

        self.sort = QComboBox()
        self.sort.addItems(["Sort: Added", "Sort: Name", "Sort: Size", "Sort: Progress"])
        self.sort.currentIndexChanged.connect(lambda *_: self.refresh())
        top.addWidget(self.sort)
        mlay.addLayout(top)

        self.list = DownloadList()
        self.list.action.connect(self._on_card_action)
        self.list.selectionChanged.connect(self._on_selection_changed)
        mlay.addWidget(self.list, 1)

        root.addWidget(main, 1)

        # details drawer overlays the right edge of the whole window
        self.drawer = DetailsDrawer(self)
        self.drawer.action.connect(self._on_card_action)
        self._toasts = ToastManager(self)

        # drag-drop overlay (shown while a link hovers over the window)
        self._drag_overlay = QLabel("⬇  Drop a link to add", self)
        self._drag_overlay.setAlignment(Qt.AlignCenter)
        self._drag_overlay.setStyleSheet(
            f"background: rgba(124,92,255,0.12); color: {palette.COLORS['text']};"
            f"border: 2px dashed {palette.COLORS['accent']}; border-radius: 16px;"
            "font-size: 20px; font-weight: 700;")
        self._drag_overlay.hide()

    # ------------------------------------------------------------- filtering
    def _set_filter(self, key):
        self._filter = key
        # keep the pills + sidebar in sync regardless of where the click came from
        if key in self.pills:
            self.pills[key].setChecked(True)
        self.sidebar.set_active(key if key in self.sidebar._rows else "")
        self.refresh()

    def _on_search(self, text):
        self._search = text.strip().lower()
        self.refresh()

    def _visible_tasks(self):
        tasks = list(self.queue.tasks)
        f = self._filter
        if f == "Active":
            tasks = [t for t in tasks if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED)]
        elif f == "Paused":
            tasks = [t for t in tasks if t.status == T.PAUSED]
        elif f == "Completed":
            tasks = [t for t in tasks if t.status == T.COMPLETED]
        elif f == "Failed":
            tasks = [t for t in tasks if t.status in (T.ERROR, T.CANCELLED)]
        elif f in utils.CATEGORIES or f == "Other":
            tasks = [t for t in tasks if utils.category_for(t.filename) == f]
        if self._search:
            tasks = [t for t in tasks if self._search in (t.filename or "").lower()]
        idx = self.sort.currentIndex()
        if idx == 1:
            tasks.sort(key=lambda t: (t.filename or "").lower())
        elif idx == 2:
            tasks.sort(key=lambda t: t.total_size, reverse=True)
        elif idx == 3:
            tasks.sort(key=lambda t: t.percent, reverse=True)
        else:
            tasks.sort(key=lambda t: getattr(t, "added", 0), reverse=True)
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
        for t in self.queue.tasks:
            if t.status == T.COMPLETED and t.id not in self._completed_seen:
                self._toasts.show("success", "Download Complete", t.filename or "download")
                if self.tray and self.tray.isVisible():
                    self.tray.showMessage("Download Complete", t.filename or "download",
                                          QSystemTrayIcon.Information, 4000)
                wc = self._extras.get("when_complete", "Show notification")
                if wc == "Open file" or self._extras.get("open_on_complete"):
                    self._open_file(t)
                elif wc == "Open folder":
                    self._open_folder(t)
                dlg = CompleteDialog(self, t)
                dlg.viewInList.connect(lambda *_: self._set_filter("All"))
                dlg.show()
            elif t.status == T.ERROR and t.id not in self._errored_seen:
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
    def _targets(self, t):
        """The task plus the rest of the selection when acting on a selected
        card (so pause/resume/cancel apply to all selected)."""
        sel = self.list.selected_ids()
        if t.id in sel and len(sel) > 1:
            return [x for x in (self.queue.get_task(i) for i in sel) if x]
        return [t]

    def _on_card_action(self, action, task_id):
        t = self.queue.get_task(task_id)
        if not t:
            return
        if action == "pause":
            for x in self._targets(t):
                self.queue.pause_task(x)
        elif action == "resume":
            for x in self._targets(t):
                self.queue.resume_task(x)
        elif action == "cancel":
            for x in self._targets(t):
                self.queue.cancel_task(x)
        elif action == "open":
            self._open_file(t)
        elif action == "folder":
            self._open_folder(t)
        elif action == "details":
            self.list.set_selection({t.id})
            self.drawer.open_for(t)
            return
        elif action == "more":
            self._card_menu(t)
            return
        self._save_state()
        self.refresh()

    def _do(self, fn, t):
        fn(t); self._save_state(); self.refresh()

    def _bulk(self, ts, fn):
        for x in ts:
            fn(x)
        self._save_state(); self.refresh()

    def _set_task_limit(self, t, bps):
        t.speed_limit = bps
        try:
            t._limiter.set_limit(bps)
        except Exception:
            pass
        self._save_state()

    def _move_task(self, t, where):
        self.queue.move(t, where); self.refresh()

    def _move_task_to_queue(self, t, name):
        self.queue.move_to_queue(t, name); self._save_state(); self.refresh()

    def _menu(self):
        m = QMenu(self)
        c = palette.COLORS
        m.setStyleSheet(
            f"QMenu{{background:{c['surface']};color:{c['text']};border:1px solid {c['border']};padding:4px;}}"
            f"QMenu::item{{padding:7px 16px;border-radius:6px;}}"
            f"QMenu::item:selected{{background:{c['surface2']};}}")
        return m

    def _card_menu(self, t):
        sel = self.list.selected_ids()
        # bulk menu when right-clicking inside a multi-selection
        if t.id in sel and len(sel) > 1:
            ts = [x for x in (self.queue.get_task(i) for i in sel) if x]
            m = self._menu()
            m.addAction(f"⏸  Pause {len(ts)} selected", lambda: self._bulk(ts, self.queue.pause_task))
            m.addAction(f"▶  Resume {len(ts)} selected", lambda: self._bulk(ts, self.queue.resume_task))
            m.addSeparator()
            m.addAction(f"🗑  Remove {len(ts)} selected", lambda: self._bulk(ts, self.queue.remove_task))
            m.exec(self.cursor().pos())
            return

        m = self._menu()
        if t.status == T.COMPLETED:
            m.addAction("📂  Open File", lambda: self._open_file(t))
            m.addAction("📁  Open Folder", lambda: self._open_folder(t))
            m.addSeparator()
        if t.status == T.DOWNLOADING:
            m.addAction("⏸  Pause", lambda: self._do(self.queue.pause_task, t))
        if t.status in (T.PAUSED, T.ERROR, T.SCHEDULED):
            m.addAction("▶  Resume", lambda: self._do(self.queue.resume_task, t))
        if t.status in (T.QUEUED, T.PAUSED, T.SCHEDULED, T.ERROR):
            m.addAction("🚀  Force Download", lambda: self._do(self.queue.force_start, t))
        if t.status != T.COMPLETED:
            sl = m.addMenu("Set Speed Limit")
            for label, bps in (("Unlimited", 0), ("100 Kb/s", 100 * 1000 // 8),
                               ("500 Kb/s", 500 * 1000 // 8), ("1 Mb/s", 1000 * 1000 // 8),
                               ("5 Mb/s", 5 * 1000 * 1000 // 8)):
                sl.addAction(label, lambda b=bps: self._set_task_limit(t, b))
        if t.status == T.QUEUED:
            m.addSeparator()
            for label, where in (("⬆  Move to top", "top"), ("↑  Move up", "up"),
                                 ("↓  Move down", "down"), ("⬇  Move to bottom", "bottom")):
                m.addAction(label, lambda w=where: self._move_task(t, w))
        if len(self.queue.queues) > 1:
            qm = m.addMenu("Move to Queue")
            for q in self.queue.queues.values():
                act = qm.addAction(q.name)
                if getattr(t, "queue_name", "Main") == q.name:
                    act.setEnabled(False)
                act.triggered.connect(lambda _=False, n=q.name: self._move_task_to_queue(t, n))
        m.addSeparator()
        m.addAction("ℹ  Properties", lambda: (self.list.set_selection({t.id}), self.drawer.open_for(t)))
        m.addAction("🔗  Copy URL", lambda: QApplication.clipboard().setText(t.url or ""))
        m.addAction("🗑  Remove", lambda: self._do(self.queue.remove_task, t))
        m.exec(self.cursor().pos())

    def _on_selection_changed(self, ids):
        pass        # reserved for a future bulk action bar; selection highlight is automatic

    def _open_file(self, t):
        target = t.save_path
        if not os.path.exists(target):
            folder = os.path.dirname(t.save_path) or "."
            target = folder if (_torrent.is_torrent_task(t.url, t.filename) and os.path.isdir(folder)) else ""
        if not target:
            return
        try:
            os.startfile(target)
        except OSError:
            pass

    def _open_folder(self, t):
        path = os.path.normpath(t.save_path)
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.path.exists(path):
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(os.path.dirname(path) or ".")
        except OSError:
            pass

    @staticmethod
    def _looks_like_url(s):
        s = (s or "").strip().lower()
        return s.startswith(("http://", "https://", "magnet:")) or s.endswith(".torrent")

    def _open_queues(self):
        from gui2.dialogs.queues import QueueManagerDialog
        QueueManagerDialog(self, self.queue).exec()
        self._save_settings()        # persist queue list + concurrencies
        self.refresh()

    def _new_download(self):
        clip = QApplication.clipboard().text().strip()
        prefill = clip if self._looks_like_url(clip) else ""
        self._add_download(prefill, "", None, flash=False)

    def _add_download(self, url, suggested, headers, flash=False):
        queues = list(self.queue.queues.keys()) or ["Main"]
        dlg = NewDownloadDialog(self, self.save_dir, queues, self.segments,
                                url=url, suggested=suggested, headers=headers)
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
        if existing and QMessageBox.question(
                self, "Already added",
                f"This URL is already in the list as “{existing.filename}”.\nAdd it again anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
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
        if dlg.exec() != QDialog.Accepted:
            return
        self._apply_settings(dlg.values())

    def _apply_settings(self, v):
        if os.path.isdir(v["save_dir"]):
            self.save_dir = v["save_dir"]
        self.max_concurrent = v["max_concurrent"]
        self.segments = v["segments"]
        self.queue.set_max_concurrent("Main", v["max_concurrent"])
        self.queue.segments = v["segments"]
        self.verify_tls = v["verify_tls"]
        utils.VERIFY_TLS = v["verify_tls"]
        # global speed limit (combo "Unlimited" / "N Mb/s")
        bps = 0
        if "Mb/s" in v.get("speed_limit", ""):
            try:
                bps = int(v["speed_limit"].split()[0]) * 1000 * 1000 // 8
            except ValueError:
                bps = 0
        self.global_speed_limit = bps
        utils.global_limiter.set_limit(bps)
        self.theme = v["theme"]
        apply_theme(self.theme)
        if palette.ACCENTS.get(v["accent"]) != palette.COLORS["accent"]:
            palette.set_accent(v["accent"])
            self.setStyleSheet(palette.qss())        # live accent re-skin (QSS widgets)
            self.sidebar.set_active(self._filter if self._filter in self.sidebar._rows else "All")
        self.scheduler_enabled = v["sched_en"]
        self.scheduler_start = v["sched_start"]
        self.scheduler_stop = v["sched_stop"]
        self._extras.update(v)
        self._apply_network_settings()
        self._save_settings()
        self.refresh()

    def _apply_network_settings(self):
        """Push persisted Network/Advanced prefs into the backend globals the
        downloader + torrent engine read each request/launch."""
        ex = self._extras
        mc = ex.get("max_connections")
        utils.MAX_CONNECTIONS = int(mc) if mc else 0
        utils.LISTEN_PORT = int(ex.get("listen_port", 0) or 0)
        utils.DISK_CACHE = bool(ex.get("disk_cache", True))
        utils.PREALLOCATE = bool(ex.get("preallocate", False))
        utils.HASH_CHECK = bool(ex.get("hash_check", False))
        utils.setup_logging(bool(ex.get("debug_log", False)))
        ctype = ex.get("connection_type", "Default (Auto)")
        purl = (ex.get("proxy") or "").strip()
        if ctype == "Direct":
            utils.PROXIES = {}                       # force direct, ignore env proxies
        elif purl:
            utils.PROXIES = {"http": purl, "https": purl}
        else:
            utils.PROXIES = None                     # auto / system / env

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drawer"):
            self.drawer.reposition()
        if hasattr(self, "_toasts"):
            self._toasts.reposition()
        if getattr(self, "_drag_overlay", None) and self._drag_overlay.isVisible():
            self._drag_overlay.setGeometry(self.rect().adjusted(40, 40, -40, -40))

    # ------------------------------------------------------------- shortcuts
    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search.setFocus)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new_download)
        # list-scoped so they don't hijack typing in the search box
        for seq, fn in ((Qt.Key_Delete, self._del_selected),
                        (Qt.Key_Space, self._space_selected),
                        (Qt.Key_Return, self._enter_selected)):
            sc = QShortcut(QKeySequence(seq), self.list)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _sel_tasks(self):
        return [x for x in (self.queue.get_task(i) for i in self.list.selected_ids()) if x]

    def _del_selected(self):
        ts = self._sel_tasks()
        if ts:
            self._bulk(ts, self.queue.remove_task)

    def _space_selected(self):
        for t in self._sel_tasks():
            if t.status == T.DOWNLOADING:
                self.queue.pause_task(t)
            elif t.status in (T.PAUSED, T.ERROR, T.SCHEDULED):
                self.queue.resume_task(t)
        self._save_state(); self.refresh()

    def _enter_selected(self):
        ts = self._sel_tasks()
        if len(ts) == 1:
            t = ts[0]
            self._open_file(t) if t.status == T.COMPLETED else self.drawer.open_for(t)

    # ------------------------------------------------------------- system tray
    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("HyperFetch")
        menu = self._menu()
        menu.addAction("Show HyperFetch", self._show_from_tray)
        menu.addAction("New Download", self._new_download)
        menu.addSeparator()
        menu.addAction("Quit", self._real_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _real_quit(self):
        self._quit_requested = True
        self.close()

    # ------------------------------------------------------------- scheduler
    def _check_scheduler(self):
        if not getattr(self, "scheduler_enabled", False):
            self._scheduler_active = False
            return
        import datetime
        now = datetime.datetime.now().time()
        cur = now.hour * 60 + now.minute
        try:
            sh, sm = map(int, self.scheduler_start.split(":"))
            eh, em = map(int, self.scheduler_stop.split(":"))
        except (ValueError, AttributeError):
            return
        start, stop = sh * 60 + sm, eh * 60 + em
        active = (start <= cur < stop) if start < stop else (cur >= start or cur < stop)
        if active:
            if not self._scheduler_active:        # window just opened: release scheduled
                for t in self.queue.tasks:
                    if t.status == T.SCHEDULED:
                        t.is_scheduled = False
                        self.queue.resume_task(t)
                self._scheduler_active = True
        else:
            # enforce every tick (also catches downloads added while out of window)
            for t in self.queue.tasks:
                if t.status in (T.DOWNLOADING, T.QUEUED):
                    self.queue.pause_task(t)
                    t.status = T.SCHEDULED
                    t.is_scheduled = True
            self._scheduler_active = False
        self.refresh()

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
        self.sidebar.set_collapsed(self._sidebar_collapsed)
        cur = self.sidebar.width()
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setMaximumWidth(16777215)        # free both bounds before tween
        self.sidebar_anim.stop()
        for a in (self._anim_min, self._anim_max):
            a.setStartValue(cur)
            a.setEndValue(target)
        self.sidebar_anim.start()

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
        if self.tray:
            self.tray.hide()
        super().closeEvent(e)


def run_v2():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtGui import QFont
    app.setFont(QFont("Segoe UI", 10))
    win = DownloadAppV2()
    win.show()
    return app.exec()


def _self_test_v2():
    """Headless smoke check: construct, tick once, exit 0."""
    import sys
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    win = DownloadAppV2()
    win.refresh()
    print(f"v2 selftest OK v{APP_VERSION}")
    return 0
