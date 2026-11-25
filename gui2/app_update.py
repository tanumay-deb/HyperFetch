"""A new version: told about it, fetched here, checked, installed.

`UpdateMixin` is mixed into `DownloadAppV2`. The app used to learn of a new
version only when somebody opened Settings -> About and pressed Check for
Updates, and then sent the installer to the browser. A copy left running for a
month kept its month-old yt-dlp - which is what stops YouTube downloads - and
said nothing.

It looks once a little after starting and once a day (Settings -> General ->
"Check for updates automatically"), on a thread of its own; the 500 ms tick
picks the answer up, so nothing here touches a widget off the GUI thread.
"""
import logging
import os
import threading
import time
from collections import deque

import task as T
import updater
from gui.theme import APP_VERSION

log = logging.getLogger("hyperfetch.gui")

CHECK_AFTER = 20                # seconds after starting: the window has settled
CHECK_EVERY = 24 * 60 * 60


def _plain(version):
    return (version or "").lstrip("vV")


class UpdateMixin:
    def _init_update(self):
        self._update_started = time.time()
        self._update_looked_at = 0.0    # when the last look began; 0: not yet
        self._update_info = None        # the last answer (set by the look's thread)
        self._update_seen = ""          # the version already announced
        self._update_url = ""           # the installer, once it is in the list
        self._update_ready = ""         # ...and where it is, once down and checked
        self._install_box = None        # "Update ready", while it is open
        # installers being checked off this thread: path -> what to do with
        # the answer, and the answers in, taken up by the next tick
        self._update_checks = {}
        self._update_answers = deque()

    # ---- finding out -----------------------------------------------------------
    def _update_due(self, now):
        if not self._extras.get("auto_update_check", True):
            return False
        if not self._update_looked_at:
            return now - self._update_started >= CHECK_AFTER
        return now - self._update_looked_at >= CHECK_EVERY

    def _look_for_update(self):
        try:
            info = updater.check_for_update(APP_VERSION)
        except Exception:
            log.exception("could not look for an update")
            return
        if info:
            self._update_info = info

    def _update_tick(self, now=None):
        """From the window's tick: start a look when one is due, and announce
        a new version the first time it is known."""
        now = time.time() if now is None else now
        while self._update_answers:                 # installers checked meanwhile
            path, ok = self._update_answers.popleft()
            then = self._update_checks.pop(path, None)
            if then:
                then(ok)
        if self._update_due(now):
            self._update_looked_at = now
            threading.Thread(target=self._look_for_update, daemon=True,
                             name="update-check").start()
        info = self._update_info
        if not (info and info.get("available")):
            return
        version = _plain(info.get("version"))
        if not version or version == self._update_seen:
            return
        self._update_seen = version
        self.sidebar.set_update("Update to %s" % version)
        self._toasts.show("info", "HyperFetch %s is out" % version,
                          "Update, in the sidebar, downloads it here.", secs=10)

    # ---- fetching it -----------------------------------------------------------
    def _start_update(self, info):
        """Settings -> About found one and its button was pressed."""
        if info:
            self._update_info = info
        self._on_update_clicked()

    def _on_update_clicked(self):
        if self._update_ready:
            self._install_update(self._update_ready)
            return
        info = self._update_info or {}
        url = info.get("installer") or ""
        if not url:
            # a release with nothing to install attached: its page says why
            if info.get("url"):
                __import__("webbrowser").open(info["url"])
            return
        if url == self._update_url:
            return                      # already on its way
        # Fetched in an earlier session and "Later" pressed: the app forgets it
        # was checked, and adding it again asked "Already added" - its default
        # button added nothing, and this one stayed on "Downloading the
        # update…" (2026-11-03). One finished is checked again and offered; one
        # that stopped part way goes on.
        old = next((t for t in self.queue.tasks
                    if t.url == url and t.status != T.CANCELLED), None)
        if old is not None and old.status == T.COMPLETED:
            if os.path.isfile(old.save_path or ""):
                if info.get("sha256"):
                    # checked, then offered; not the release's file: fetched again
                    self._update_url = url
                    self._update_finished(old, again=self._fetch_update_again)
                else:
                    self._update_finished(old)    # nothing to check it against: says so
                return
            self.queue.remove_task(old)       # gone
            old = None
        self._fetch_update(url, old)

    def _fetch_update_again(self, old):
        """The installer an earlier session fetched is not the release's file:
        it goes, and the release's is fetched."""
        self.queue.remove_task(old)
        self._fetch_update(self._update_url, None)

    def _fetch_update(self, url, old):
        self._update_url = url
        if old is not None:
            self.queue.resume_task(old)
        else:
            # into this app's own list, and without the New Download dialog:
            # there is nothing to choose about it
            self._add_download(url, url.split("?")[0].rsplit("/", 1)[-1], None, ask=False)
        self.sidebar.set_update("Downloading the update…", enabled=False)

    def _is_update(self, task):
        return bool(self._update_url) and getattr(task, "url", "") == self._update_url

    # ---- installing it ---------------------------------------------------------
    def _update_finished(self, task, again=None):
        """The installer is down. Check it is the file the release lists, then
        offer to run it. The check reads the whole installer, so it is made
        on a thread (_check_file) and its answer taken up by the next tick:
        on this one, inside the window's refresh, the window froze for it
        (sweep 4, 2026-11-25). `again`: what to do with a file that is not
        the release's, instead of saying so - fetch it again (the button)."""
        info = self._update_info or {}
        version = _plain(info.get("version"))
        sha = info.get("sha256")
        if not sha:
            # no checksum listed is not a pass: what cannot be checked is not run
            log.warning("the release lists no checksum for the update; not run")
            self._not_installed(version, "The release lists no checksum to check it "
                                         "against. Install it from the HyperFetch "
                                         "website instead.")
            return
        path = task.save_path
        if path in self._update_checks:
            return                          # being checked already
        self.sidebar.set_update("Checking the update…", enabled=False)
        self._check_file(path, sha, lambda ok: self._update_checked(
            task, version, ok, again))

    def _check_file(self, path, sha, then):
        """Hash `path` on a thread; `then(ok)` runs on this one, from the
        next tick (_update_tick). The thread gets the path and the answers'
        queue, never the window."""
        self._update_checks[path] = then
        answers = self._update_answers

        def work():
            try:
                ok = updater.file_matches(path, sha)
            except Exception:
                log.exception("could not check the update")
                ok = False
            answers.append((path, ok))

        threading.Thread(target=work, daemon=True, name="update-check-file").start()

    def _update_checked(self, task, version, ok, again):
        if ok:
            self._update_ready = task.save_path
            self.sidebar.set_update("Install %s" % version)
            self._ask_install(version)
            return
        if again is not None:
            again(task)
            return
        # Not the release's file: cut short, or changed on the way. It is not
        # run, and the button asks for it again.
        log.warning("the downloaded update does not match the release's checksum")
        self._not_installed(version, "What came down is not the file the release "
                                     "lists. Try again.")

    def _not_installed(self, version, why):
        self._update_url = ""
        self._toasts.show("error", "Update not installed", why, secs=12)
        self.sidebar.set_update("Update to %s" % version)

    def _ask_install(self, version):
        """Put "Install now / Later", without waiting for the answer
        (_install_answered takes it). It was exec()'d inside the 500 ms
        refresh, whose timer kept firing under it: another copy of the
        question every half second, and a "Shut down when done" countdown
        that opened over them could not be clicked (2026-11-11)."""
        if self._install_box is not None:
            return                      # already asked
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Update ready")
        box.setText("HyperFetch %s is ready to install." % version)
        box.setInformativeText("HyperFetch closes while it installs. Downloads in "
                               "progress pick up where they stopped afterwards.")
        self._install_now = box.addButton("Install now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.setDefaultButton(self._install_now)
        box.setWindowModality(Qt.NonModal)   # a countdown beside it stays clickable
        box.finished.connect(self._install_answered)   # bound: no closure over the box
        self._install_box = box
        box.show()

    def _install_answered(self, *_):
        box, self._install_box = self._install_box, None
        if box is not None:
            install = box.clickedButton() is self._install_now
            box.setParent(None)
            box.deleteLater()
            if install and self._update_ready:
                self._install_update(self._update_ready)

    def _run_installer(self, path):
        os.startfile(path)

    def _install_update(self, path):
        try:
            self._run_installer(path)
        except OSError as e:
            self._toasts.show("error", "Could not start the installer", str(e)[:90])
            return
        # The installer will not replace a running HyperFetch; it would stop
        # and ask for it to be closed.
        self._real_quit()
