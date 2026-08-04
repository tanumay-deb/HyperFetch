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
        for seq, fn in (("Delete", self._del_selected),
                        ("Space", self._space_selected),
                        ("Return", self._enter_selected)):
            sc = QShortcut(QKeySequence(seq), self.list)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        sc_all = QShortcut(QKeySequence("Ctrl+A"), self.list)
        sc_all.setContext(Qt.WidgetWithChildrenShortcut)
        sc_all.activated.connect(self._select_all_cards)

        # command palette — application-wide so it opens from anywhere
        sc_cmd = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_cmd.activated.connect(self._open_command_palette)

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
        failed = []
        for t in ts:
            self.queue.remove_task(t)
            # always clear the engine's leftovers (aria2 .aria2 control file
            # + our saved metadata) — they are useless once the task is gone
            try:
                import torrent as _tor
                if _tor.is_torrent_task(t.url, t.filename):
                    _tor.cleanup_artifacts(t)
            except Exception:
                pass
            if delete_disk:
                targets = self._payload_paths(t)
                if not targets:
                    continue
                for path in targets:
                    if not self._remove_path(path):
                        failed.append(os.path.basename(path) or path)

        # the user asked for this, so an empty result is legitimate
        self._allow_empty_save = True
        self._save_state()
        self.refresh()
        if failed:
            # Never silent: "delete from disk" that quietly did nothing is
            # worse than an error, because the files are still taking up space
            # and the user believes they are gone.
            self._toasts.show(
                "error", "Could not delete some files",
                ", ".join(failed[:3]) + ("…" if len(failed) > 3 else "")
                + " — still in use. Try again in a moment.")

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
        out = []
        save = getattr(t, "save_path", "") or ""
        base = os.path.dirname(save) or "."
        if save and os.path.exists(save):
            out.append(save)

        name = (getattr(t, "filename", "") or "").strip()
        # "torrent" and "download" are the engine's own placeholders; joining
        # either would point at something that is not this download's payload
        if name and name.lower() not in ("torrent", "download", "download.bin"):
            payload = os.path.join(base, name)
            real = os.path.abspath(payload)
            # never step outside the download folder, and never delete the
            # folder itself — one bad join here would take the lot
            if (os.path.abspath(base) != real
                    and real.startswith(os.path.abspath(base) + os.sep)
                    and os.path.exists(real) and real not in map(os.path.abspath, out)):
                out.append(payload)
        return out

    @staticmethod
    def _remove_path(path, attempts=5, delay=0.4):
        """Delete a file or a whole torrent folder, retrying briefly.

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
                return True
            except OSError:
                if not os.path.exists(path):
                    return True                 # something else got there first
                if attempt == attempts - 1:
                    return False
                _time.sleep(delay)
        return False

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
