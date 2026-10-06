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
        self._update_url = url
        # into this app's own list, and without the New Download dialog: there
        # is nothing to choose about it
        self._add_download(url, url.split("?")[0].rsplit("/", 1)[-1], None, ask=False)
        self.sidebar.set_update("Downloading the update…", enabled=False)

    def _is_update(self, task):
        return bool(self._update_url) and getattr(task, "url", "") == self._update_url

    # ---- installing it ---------------------------------------------------------
    def _update_finished(self, task):
        """The installer is down. Check it is the file the release lists, then
        offer to run it."""
        info = self._update_info or {}
        version = _plain(info.get("version"))
        sha = info.get("sha256")
        if sha and not updater.file_matches(task.save_path, sha):
            # Not the release's file: cut short, or changed on the way. It is
            # not run, and the button asks for it again.
            log.warning("the downloaded update does not match the release's checksum")
            self._update_url = ""
            self._toasts.show("error", "Update not installed",
                              "What came down is not the file the release lists. "
                              "Try again.", secs=12)
            self.sidebar.set_update("Update to %s" % version)
            return
        self._update_ready = task.save_path
        self.sidebar.set_update("Install %s" % version)
        if self._ask_install(version):
            self._install_update(task.save_path)

    def _ask_install(self, version):
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Update ready")
        box.setText("HyperFetch %s is ready to install." % version)
        box.setInformativeText("HyperFetch closes while it installs. Downloads in "
                               "progress pick up where they stopped afterwards.")
        now = box.addButton("Install now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.setDefaultButton(now)
        box.exec()
        return box.clickedButton() is now

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
