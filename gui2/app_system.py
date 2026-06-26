"""System tray + the time-window scheduler for the main window.

`SystemMixin` manages the tray icon/menu and the start/stop scheduling window
that pauses or releases downloads at set times.
"""
from PySide6.QtWidgets import QSystemTrayIcon

import task as T


class SystemMixin:
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
