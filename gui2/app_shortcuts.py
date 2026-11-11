"""Keyboard shortcuts + multi-select bulk actions for the main window.

`ShortcutsMixin` wires Delete / Space / Return / Ctrl+A (scoped to the download
list) and the selection-aware behaviours they trigger.
"""
import os

from PySide6.QtCore import Qt

import task as T


class ShortcutsMixin:
    def _setup_shortcuts(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        self._list_shortcuts()
        # command palette — application-wide so it opens from anywhere
        sc_cmd = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_cmd.activated.connect(self._open_command_palette)

    def _list_shortcuts(self):
        """The list's keys. They belong to the list, and go with it when the
        window is built again (_reskin)."""
        from PySide6.QtGui import QKeySequence, QShortcut
        for seq, fn in (("Delete", self._del_selected),
                        ("Space", self._space_selected),
                        ("Return", self._enter_selected)):
            sc = QShortcut(QKeySequence(seq), self.list)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        sc_all = QShortcut(QKeySequence("Ctrl+A"), self.list)
        sc_all.setContext(Qt.WidgetWithChildrenShortcut)
        sc_all.activated.connect(self._select_all_cards)

    def _select_all_cards(self):
        if not hasattr(self, "list") or not hasattr(self.list, "_cards"):
            return
        for w in self.list._cards.values():
            if hasattr(w, "chk"):
                w.chk.setChecked(True)

    def _sel_tasks(self):
        return [x for x in (self.queue.get_task(i) for i in self.list.selected_ids()) if x]

    def _del_selected(self):
        self._delete_tasks(self._sel_tasks())

    def _delete_tasks(self, ts):
        """Confirm, then remove — the single entry point for deleting anything.

        Every route here has to ask first: the context menu's Remove used to
        call queue.remove_task straight, so it neither confirmed nor offered to
        delete the files, and the same word meant two different things
        depending on where you clicked it.
        """
        ts = [t for t in (ts or []) if t]
        if not ts:
            return

        finished = sum(1 for t in ts if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED))
        downloading = len(ts) - finished

        from gui2.dialogs.delete import DeleteDialog
        dlg = DeleteDialog(finished=finished, downloading=downloading, parent=self)
        if not dlg.exec():
            return

        delete_disk = dlg.deleteDisk.isChecked()
        jobs = []
        for t in ts:
            # read first: an unfinished torrent's files are known by aria2's
            # control file, which the removal and cleanup below take away
            targets = self._payload_paths(t) if delete_disk else []
            self.queue.remove_task(t)
            # always clear the engine's leftovers (aria2 .aria2 control file
            # + our saved metadata) — they are useless once the task is gone
            try:
                import torrent as _tor
                if _tor.is_torrent_task(t.url, t.filename):
                    _tor.cleanup_artifacts(t)
            except Exception:
                pass
            if targets:
                jobs.append((t, targets))

        # the user asked for this, so an empty result is legitimate
        self._allow_empty_save = True
        self._save_state()
        self.refresh()
        if jobs:
            # A running download's files are still open: deleted once its
            # engine lets go, off this thread (it froze the window, 2026-11-04)
            import threading
            threading.Thread(target=self._delete_files, args=(jobs,),
                             name="delete", daemon=True).start()

    def _delete_files(self, jobs):
        """[(task, paths)] of downloads just removed: wait for each one's
        worker to end and, for a torrent, for aria2 to close its files and
        forget it (torrent._let_go), then delete. On a thread; what could not
        be deleted goes to _finish_deletes."""
        import time
        import torrent as _tor
        failed = []
        for t, targets in jobs:
            end = time.monotonic() + 60
            while getattr(t, "_worker_alive", False) and time.monotonic() < end:
                time.sleep(0.2)
            if _tor.is_torrent_task(t.url, t.filename):
                ih = _tor.task_infohash(t)
                if ih and any(x is not t and _tor.task_infohash(x) == ih
                              for x in list(self.queue.tasks)):
                    continue                # added again meanwhile: its files now
                try:
                    _tor._let_go(t)
                    _tor.cleanup_artifacts(t)   # aria2 writes its control file as it stops
                except Exception:
                    pass
            for path in targets:
                err = self._remove_path(path, attempts=10, delay=0.5)
                if err:
                    failed.append((os.path.basename(path) or path, self._why_not_deleted(err)))
        self._deletes.append(failed)

    @staticmethod
    def _why_not_deleted(err):
        """What to tell the user of a delete that failed. It always said
        "another program has them open" - also of a file Windows refused for
        want of permission, or one marked read-only (2026-11-11)."""
        win = getattr(err, "winerror", None)
        if win in (32, 33):                  # sharing / lock violation
            return "another program has them open"
        if win == 5 or (win is None and isinstance(err, PermissionError)):
            return "Windows refused: no permission, or they are read-only"
        return str(getattr(err, "strerror", "") or err)

    def _finish_deletes(self):
        """What delete threads could not delete, said on the window's thread."""
        while self._deletes:
            failed = self._deletes.popleft()
            if failed:
                # Never silent: "delete from disk" that quietly did nothing is
                # worse than an error, because the files are still taking up
                # space and the user believes they are gone.
                why = {}
                for name, reason in failed:          # by reason, in order
                    why.setdefault(reason, []).append(name)
                self._toasts.show(
                    "error", "Could not delete some files",
                    " ".join("%s%s — %s." % (", ".join(names[:3]),
                                             "…" if len(names) > 3 else "", reason)
                             for reason, names in why.items())
                    + " They are still on disk.")

    @staticmethod
    def _payload_paths(t):
        """Everything on disk that belongs to this download.

        save_path alone is not enough. For a torrent it stays at the
        placeholder the task was created with — literally
        "…\\Downloads\\download.bin" — until _resolve_save_path runs on
        COMPLETION. So for anything paused or unfinished it names a file that
        never existed, and "also delete from disk" quietly removed nothing while
        the real payload, a folder named after the torrent beside it, stayed put.

        The torrent's real entry is its filename, which the engine sets from the
        first non-[METADATA] file it sees.
        """
        import torrent as _tor
        import yt_dl
        tor = _tor.is_torrent_task(t.url, t.filename)
        if t.status != T.COMPLETED and not tor and not yt_dl.is_ytdlp_task(t):
            # an unfinished plain download has nothing at its save path - its
            # bytes are in a temp file, which remove_task takes - so what is
            # there is another file: one of the same name that finished first
            return []
        # An unfinished torrent's files have aria2's control file beside them
        # (<name>.aria2) until it finishes. One of its name without that is
        # somebody else's - the file aria2 refused to start over, say - and was
        # deleted with it (2026-11-01).
        # One that finished once (completed_at) points at its own files, also
        # while a Force Recheck or more ticked files queue it again.
        unproven = tor and t.status != T.COMPLETED and not t.completed_at

        def ours(p):
            return not unproven or os.path.isfile(p + ".aria2")

        out = []
        save = getattr(t, "save_path", "") or ""
        base = os.path.dirname(save) or "."
        if save and os.path.exists(save) and ours(save):
            out.append(save)

        name = (getattr(t, "filename", "") or "").strip()
        # "torrent" and "download" are the engine's own placeholders; joining
        # either would point at something that is not this download's payload
        # A torrent still being written in the unfinished-downloads folder is
        # there, not in its own folder (task.unfinished_in).
        work = getattr(t, "unfinished_in", "") or ""
        if work and name and name.lower() not in ("torrent", "download", "download.bin"):
            payload = os.path.join(work, name)
            real = os.path.abspath(payload)
            if (real.startswith(os.path.abspath(work) + os.sep) and os.path.exists(real)
                    and ours(payload) and real not in map(os.path.abspath, out)):
                out.append(payload)
        if name and name.lower() not in ("torrent", "download", "download.bin"):
            payload = os.path.join(base, name)
            real = os.path.abspath(payload)
            # never step outside the download folder, and never delete the
            # folder itself — one bad join here would take the lot
            if (os.path.abspath(base) != real
                    and real.startswith(os.path.abspath(base) + os.sep)
                    and os.path.exists(real) and ours(payload)
                    and real not in map(os.path.abspath, out)):
                out.append(payload)
        return out

    @staticmethod
    def _remove_path(path, attempts=5, delay=0.4):
        """Delete a file or a whole torrent folder, retrying briefly. None
        when it is gone, else the error that kept it.

        remove_task only ASKS the engine to stop; aria2 still has the files open
        for a moment after, and on Windows an open handle makes the delete fail
        outright. One attempt therefore left the payload on disk exactly when
        the user had just asked for it to go.
        """
        import shutil
        import time as _time
        for attempt in range(attempts):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)         # not ignore_errors: we report
                else:
                    os.remove(path)
                return None
            except OSError as e:
                if not os.path.exists(path):
                    return None                 # something else got there first
                if attempt == attempts - 1:
                    return e                    # what stopped it, for the message
                _time.sleep(delay)

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
