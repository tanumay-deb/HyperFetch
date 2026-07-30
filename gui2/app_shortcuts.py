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
        ts = self._sel_tasks()
        if not ts:
            return

        finished = sum(1 for t in ts if t.status in (T.COMPLETED, T.ERROR, T.CANCELLED))
        downloading = len(ts) - finished

        from gui2.dialogs.delete import DeleteDialog
        dlg = DeleteDialog(finished=finished, downloading=downloading, parent=self)
        if dlg.exec():
            delete_disk = dlg.deleteDisk.isChecked()
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
                path = getattr(t, "save_path", None)
                if delete_disk and path and os.path.exists(path):
                    try:
                        # a torrent's payload is a DIRECTORY; os.remove only
                        # handles files, so multi-file torrents used to survive
                        # "also delete from disk" untouched
                        if os.path.isdir(path):
                            import shutil
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                    except OSError:
                        pass
            # the user asked for this, so an empty result is legitimate
            self._allow_empty_save = True
            self._save_state()
            self.refresh()

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
