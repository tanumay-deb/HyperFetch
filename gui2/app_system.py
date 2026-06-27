"""System tray + the time-window scheduler for the main window.

`SystemMixin` manages the tray icon/menu and the start/stop scheduling window
that pauses or releases downloads at set times.
"""
import datetime

from PySide6.QtWidgets import QSystemTrayIcon

import task as T
import utils


def _in_window(start_hhmm, stop_hhmm):
    """Is the current local time within [start, stop)? Handles windows that wrap
    past midnight. Returns None if the times are unparseable."""
    try:
        sh, sm = map(int, (start_hhmm or "").split(":"))
        eh, em = map(int, (stop_hhmm or "").split(":"))
    except (ValueError, AttributeError):
        return None
    now = datetime.datetime.now().time()
    cur = now.hour * 60 + now.minute
    start, stop = sh * 60 + sm, eh * 60 + em
    if start == stop:
        return False
    return (start <= cur < stop) if start < stop else (cur >= start or cur < stop)


class SystemMixin:
    # ------------------------------------------------------------- speed throttle
    def _throttle_bps(self):
        """The speed limit to enforce right now: the scheduled throttle when its
        window is active, otherwise the normal global limit."""
        ex = self._extras
        if ex.get("throttle_enabled") and _in_window(ex.get("throttle_start", "09:00"),
                                                     ex.get("throttle_stop", "17:00")):
            lim = ex.get("throttle_limit", "1 Mb/s")
            if "Mb/s" in lim:
                try:
                    return int(lim.split()[0]) * 1000 * 1000 // 8
                except ValueError:
                    pass
        return getattr(self, "global_speed_limit", 0)

    def _apply_throttle(self):
        utils.global_limiter.set_limit(self._throttle_bps())
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
        self._apply_throttle()              # speed throttle runs regardless of the start/stop scheduler
        if not getattr(self, "scheduler_enabled", False):
            self._scheduler_active = False
            return
        active = _in_window(self.scheduler_start, self.scheduler_stop)
        if active is None:
            return
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
