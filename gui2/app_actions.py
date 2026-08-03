"""Task action handlers + the download-card context menu for the main window.

`ActionsMixin` turns UI signals (pause/resume/cancel/move/menu) into queue
calls. Mixed into `DownloadAppV2`; runs on the live window via `self`.
"""
import os

from PySide6.QtWidgets import QMenu, QInputDialog, QLineEdit, QApplication

import task as T
import utils
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
        elif action.startswith("select_files:"):
            indices = action.split(":", 1)[1]
            self.queue.change_torrent_files(task_id, indices)
        elif action == "trackers_changed":
            # The drawer already updated the magnet URI; persist it below.
            pass
        elif action == "details":
            self.list.set_selection({t.id})
            self.drawer.open_for(t)
            self._position_action_bar()      # re-dodge now that the drawer is visible
            return
        elif action == "delete":
            # drawer quick-action: same confirm dialog as the bulk bar
            self.list.set_selection({t.id})
            self._del_selected()
            if not self.queue.get_task(t.id):        # actually removed
                self.drawer.close_drawer()
            return
        elif action == "more":
            self._card_menu(t)
            return
        self._save_state()
        self.refresh()

    def _do(self, fn, t):
        # a user action may legitimately leave the list empty
        self._allow_empty_save = True
        fn(t); self._save_state(); self.refresh()

    def _bulk(self, ts, fn):
        self._allow_empty_save = True
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

    def _rename_task(self, t):
        """Change the display name — and the file on disk when it's completed.
        In-flight tasks only retarget save_path: bytes live in the id-keyed
        .hfdownload temp, so finalize simply lands on the new name."""
        import utils
        new, ok = QInputDialog.getText(self, "Rename", "New file name:",
                                       QLineEdit.Normal, t.filename)
        new = utils.safe_filename((new or "").strip())
        if not ok or not new or new == t.filename:
            return
        d = os.path.dirname(t.save_path) or "."
        if t.status == T.COMPLETED and os.path.exists(t.save_path):
            dest = utils.unique_path(d, new)
            try:
                os.rename(t.save_path, dest)
            except OSError as e:
                self._toasts.show("error", "Rename failed", str(e))
                return
            t.save_path = dest
            t.filename = os.path.basename(dest)
        else:
            t.save_path = utils.unique_path(d, new)
            t.filename = os.path.basename(t.save_path)
        t.log_event("Renamed")
        self._save_state()
        self.refresh()

    def _move_task_files(self, t):
        """Move a download to another folder.

        Completed or partially-downloaded files are moved on disk. An in-flight
        HTTP download only needs its destination retargeted: the bytes live in
        an id-keyed .hfdownload temp until finalize, so nothing has to move. A
        running torrent is the exception — aria2 owns its --dir and cannot be
        repointed mid-download — so that one asks for a pause first.
        """
        import shutil
        import utils
        from PySide6.QtWidgets import QFileDialog

        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        if is_tor and t.status in (T.DOWNLOADING, T.QUEUED):
            self._toasts.show("info", "Pause first",
                              "Pause this torrent before moving it — the engine "
                              "cannot change folder while it is running.")
            return

        cur = t.save_path or ""
        start_dir = os.path.dirname(cur) if cur else self.save_dir
        dest_dir = QFileDialog.getExistingDirectory(self, "Move download to…", start_dir)
        if not dest_dir or os.path.normpath(dest_dir) == os.path.normpath(start_dir):
            return

        name = os.path.basename(cur) or t.filename
        target = utils.unique_path(dest_dir, name)

        if cur and os.path.exists(cur):
            try:
                shutil.move(cur, target)         # handles files and folders
            except OSError as e:
                self._toasts.show("error", "Move failed", str(e))
                return
        # nothing on disk yet (queued/paused HTTP): just retarget the destination
        t.save_path = target
        t.filename = os.path.basename(target)
        t.log_event(f"Moved to {dest_dir}")
        self._save_state()
        self.refresh()
        self._toasts.show("success", "Moved", t.filename)

    def _restart_task(self, t):
        """Re-download from byte 0. Only offered on non-running tasks so we
        never race a live worker over the temp file."""
        if t.status in (T.DOWNLOADING, T.QUEUED):
            return
        t.reset_progress()
        self.queue.add_task(t)
        self._save_state()
        self.refresh()

    # video containers worth offering to play mid-download
    _VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts",
                   ".mpg", ".mpeg", ".wmv", ".flv"}

    def _playable_file(self, t):
        """The biggest video file in this torrent that has bytes on disk.

        Biggest because a release's feature file is what someone wants to watch,
        not the sample or the trailer. Requires real progress: opening a
        preallocated but empty file just hands the player zeroes.
        """
        best, best_len = "", 0
        rows = getattr(t, "file_progress", None) or []
        for f in rows:
            path = f.get("path") or ""
            if os.path.splitext(path)[1].lower() not in self._VIDEO_EXTS:
                continue
            if int(f.get("completed") or 0) <= 0:
                continue
            if int(f.get("length") or 0) >= best_len and os.path.exists(path):
                best, best_len = path, int(f.get("length") or 0)
        if best:
            return best
        if rows:
            # The engine listed every file and none of them has data yet.
            # Falling through to the disk here would hand back a preallocated,
            # empty file — the exact thing the completed>0 check rejects.
            return ""
        # Legacy engine reports no per-file progress, so fall back to the disk.
        root = t.save_path or ""
        root = root if os.path.isdir(root) else os.path.dirname(root)
        if not os.path.isdir(root):
            return ""
        try:
            for base, _dirs, files in os.walk(root):
                for name in files:
                    if os.path.splitext(name)[1].lower() not in self._VIDEO_EXTS:
                        continue
                    p = os.path.join(base, name)
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        continue
                    if size > best_len:
                        best, best_len = p, size
        except OSError:
            return ""
        return best

    def _play_partial(self, t):
        """Open the part-downloaded video in the system player."""
        path = self._playable_file(t)
        if not path:
            self._toasts.show("info", "Nothing to preview yet",
                              "No video file has data on disk yet.")
            return
        if not getattr(utils, "TORRENT_PREVIEW", False):
            # Without head/tail priority the opening pieces may simply not be
            # there yet, so say why it might not play rather than letting the
            # player fail silently.
            self._toasts.show(
                "warning", "Preview not optimised",
                "Turn on Settings → Advanced → Preview while downloading so the "
                "start of each file is fetched first.")
        try:
            os.startfile(path)
        except OSError as e:
            self._toasts.show("error", "Could not open", str(e)[:70])

    def _recheck_torrent(self, t):
        """Re-hash a torrent's data against the .torrent's piece hashes.

        The point is to repair rather than restart: aria2 keeps every piece that
        verifies and re-fetches only the ones that do not, so a payload damaged
        by a bad disk or a half-written file costs minutes instead of the whole
        download again. A completed torrent has to leave COMPLETED first, or
        resume_task would refuse it as already finished.
        """
        if t.status == T.DOWNLOADING:
            return
        t.force_recheck = True
        if t.status == T.COMPLETED:
            t.status = T.PAUSED
        t.error = ""
        t.log_event("Force recheck requested")
        self.queue.resume_task(t)
        self._save_state()
        self.refresh()
        self._toasts.show("info", "Rechecking",
                          f"Verifying {t.filename or 'torrent'} against its piece hashes…")

    def _force_recheck(self, t):
        """Re-run SHA-256 verification on a completed file in the background;
        the drawer's Integrity section shows the result on the next tick."""
        if t.status != T.COMPLETED or not os.path.isfile(t.save_path or ""):
            return
        t.hash_status = ""
        t.sha256 = ""

        def work():
            from downloader import Downloader
            try:
                Downloader(t)._verify_hash(always_digest=True)
            except Exception:
                t.hash_status = "nohash"
        import threading
        threading.Thread(target=work, daemon=True).start()

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
            # through _delete_tasks, so it confirms and can delete the files —
            # calling queue.remove_task here did neither
            m.addAction(ico("trash"), f"Delete {len(ts)} selected…",
                        lambda: self._delete_tasks(ts))
            m.exec(self.cursor().pos())
            return

        m = self._build_card_menu(t)
        m.exec(self.cursor().pos())

    def _build_card_menu(self, t):
        """Assemble the context menu WITHOUT showing it.

        Split from _card_menu so it can be tested: exec() blocks on real input,
        so any test that opened the menu would hang, and the only way to cover
        it was to call the individual handlers instead. That is precisely how a
        NameError in one branch shipped — right-click raised before the menu
        appeared, and every handler test still passed.
        """
        ico = lambda n: themed_icon(n, "text")
        # Defined up here because the very first branch below needs it. It used
        # to be initialised further down, so adding the Play-preview branch
        # raised NameError and the context menu simply never opened.
        is_tor = _torrent.is_torrent_task(t.url, t.filename)

        m = self._menu()
        if t.status == T.COMPLETED:
            m.addAction(ico("open"), "Open File", lambda: self._open_file(t))
            m.addAction(ico("folder"), "Open Folder", lambda: self._open_folder(t))
            m.addSeparator()
        elif t.status == T.DOWNLOADING and is_tor and self._playable_file(t):
            # The point of head/tail piece priority: a video with its start
            # already fetched is watchable now. Without somewhere to click, that
            # setting only ever produced a differently-ordered download.
            m.addAction(ico("video"), "Play preview", lambda: self._play_partial(t))
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
        m.addAction(ico("document"), "Rename", lambda: self._rename_task(t))
        m.addAction(ico("folder"), "Move to…", lambda: self._move_task_files(t))
        if t.status in (T.PAUSED, T.ERROR, T.CANCELLED, T.COMPLETED) and not is_tor:
            m.addAction(ico("history"), "Restart", lambda: self._restart_task(t))
        if t.status == T.COMPLETED and not is_tor:
            m.addAction(ico("check"), "Force Recheck", lambda: self._force_recheck(t))
        if is_tor and t.status in (T.COMPLETED, T.PAUSED, T.ERROR):
            m.addAction(ico("check"), "Force Recheck",
                        lambda: self._recheck_torrent(t))
        m.addSeparator()
        m.addAction(ico("info"), "Properties", lambda: (self.list.set_selection({t.id}), self.drawer.open_for(t)))
        m.addAction(ico("link"), "Copy URL", lambda: QApplication.clipboard().setText(t.url or ""))
        m.addAction(ico("trash"), "Delete…", lambda: self._delete_tasks([t]))
        return m

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
