"""Task action handlers + the download-card context menu for the main window.

`ActionsMixin` turns UI signals (pause/resume/cancel/move/menu) into queue
calls. Mixed into `DownloadAppV2`; runs on the live window via `self`.
"""
import os

from PySide6.QtWidgets import QMenu, QInputDialog, QLineEdit, QApplication

import task as T
import torrent as _torrent
from gui.icons import themed_icon
from gui2 import palette


class ActionsMixin:
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
            self._position_action_bar()      # re-dodge now that the drawer is visible
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

    def _refresh_address(self, t):
        new_url, ok = QInputDialog.getText(self, "Refresh Address",
                                           "Enter the new download URL:",
                                           QLineEdit.Normal, t.url)
        if ok and new_url.strip() and new_url.strip() != t.url:
            t.url = new_url.strip()
            t.error = None
            self.queue.resume_task(t)
            self._save_state()
            self.refresh()

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
        ico = lambda n: themed_icon(n, "text")
        if t.id in sel and len(sel) > 1:
            ts = [x for x in (self.queue.get_task(i) for i in sel) if x]
            m = self._menu()
            m.addAction(ico("pause"), f"Pause {len(ts)} selected", lambda: self._bulk(ts, self.queue.pause_task))
            m.addAction(ico("play"), f"Resume {len(ts)} selected", lambda: self._bulk(ts, self.queue.resume_task))
            m.addSeparator()
            m.addAction(ico("trash"), f"Remove {len(ts)} selected", lambda: self._bulk(ts, self.queue.remove_task))
            m.exec(self.cursor().pos())
            return

        m = self._menu()
        if t.status == T.COMPLETED:
            m.addAction(ico("open"), "Open File", lambda: self._open_file(t))
            m.addAction(ico("folder"), "Open Folder", lambda: self._open_folder(t))
            m.addSeparator()
        if t.status == T.DOWNLOADING:
            m.addAction(ico("pause"), "Pause", lambda: self._do(self.queue.pause_task, t))
        if t.status in (T.PAUSED, T.ERROR, T.SCHEDULED):
            m.addAction(ico("play"), "Resume", lambda: self._do(self.queue.resume_task, t))
            if t.status in (T.PAUSED, T.ERROR):
                m.addAction(ico("link"), "Refresh Address", lambda: self._refresh_address(t))
        if t.status in (T.QUEUED, T.PAUSED, T.SCHEDULED, T.ERROR):
            m.addAction(ico("force"), "Force Download", lambda: self._do(self.queue.force_start, t))
        if t.status != T.COMPLETED:
            sl = m.addMenu("Set Speed Limit")
            for label, bps in (("Unlimited", 0), ("100 Kb/s", 100 * 1000 // 8),
                               ("500 Kb/s", 500 * 1000 // 8), ("1 Mb/s", 1000 * 1000 // 8),
                               ("5 Mb/s", 5 * 1000 * 1000 // 8)):
                sl.addAction(label, lambda b=bps: self._set_task_limit(t, b))
        if t.status == T.QUEUED:
            m.addSeparator()
            for ic_name, label, where in (("arrow-top", "Move to top", "top"), ("arrow-up", "Move up", "up"),
                                          ("arrow-down", "Move down", "down"), ("arrow-bottom", "Move to bottom", "bottom")):
                m.addAction(ico(ic_name), label, lambda w=where: self._move_task(t, w))
        if len(self.queue.queues) > 1:
            qm = m.addMenu("Move to Queue")
            for q in self.queue.queues.values():
                act = qm.addAction(q.name)
                if getattr(t, "queue_name", "Main") == q.name:
                    act.setEnabled(False)
                act.triggered.connect(lambda _=False, n=q.name: self._move_task_to_queue(t, n))
        m.addSeparator()
        m.addAction(ico("info"), "Properties", lambda: (self.list.set_selection({t.id}), self.drawer.open_for(t)))
        m.addAction(ico("link"), "Copy URL", lambda: QApplication.clipboard().setText(t.url or ""))
        m.addAction(ico("trash"), "Remove", lambda: self._do(self.queue.remove_task, t))
        m.exec(self.cursor().pos())

    def _on_selection_changed(self, ids):
        # contextual bulk-action bar: visible only while something is selected
        bar = getattr(self, "action_bar", None)
        if bar is not None:
            if ids:
                ts = [x for x in (self.queue.get_task(i) for i in ids) if x]
                st = {x.status for x in ts}
                bar.set_count(len(ids))
                bar.set_applicable(
                    open_=(len(ts) == 1 and T.COMPLETED in st),
                    pause=T.DOWNLOADING in st,
                    resume=bool(st & {T.PAUSED, T.ERROR, T.QUEUED, T.SCHEDULED}),
                    force=bool(st & {T.QUEUED, T.PAUSED, T.ERROR, T.SCHEDULED}),
                    move=(len(self.queue.queues) > 1 and T.QUEUED in st),
                )
                self._position_action_bar()
                bar.show(); bar.raise_()
            else:
                bar.hide()
        # when the details drawer is open, selecting another single card retargets it
        if self.drawer.isVisible() and len(ids) == 1:
            t = self.queue.get_task(next(iter(ids)))
            if t:
                self.drawer.retarget(t)

    def _bar_bulk(self, fn):
        ts = [x for x in (self.queue.get_task(i) for i in self.list.selected_ids()) if x]
        if ts:
            self._bulk(ts, fn)

    def _bar_open(self):
        ts = [x for x in (self.queue.get_task(i) for i in self.list.selected_ids()) if x]
        if len(ts) == 1 and ts[0].status == T.COMPLETED:
            self._open_file(ts[0])

    def _bar_move_menu(self):
        ts = [x for x in (self.queue.get_task(i) for i in self.list.selected_ids()) if x]
        if not ts or len(self.queue.queues) <= 1:
            return
        m = self._menu()
        for q in self.queue.queues.values():
            m.addAction(q.name, lambda n=q.name: self._bulk(ts, lambda x: self.queue.move_to_queue(x, n)))
        m.exec(self.action_bar.move_btn.mapToGlobal(self.action_bar.move_btn.rect().bottomLeft()))

    def _on_blank_clicked(self):
        # left-click on empty list space clears the selection and closes the drawer
        if self.drawer.isVisible():
            self.drawer.close_drawer()
        self.list.clear_selection()

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
