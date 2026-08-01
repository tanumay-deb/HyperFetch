"""Countdown before HyperFetch powers the machine down or sleeps it.

Turning someone's computer off is not an action to take on a timer they cannot
see. The setting arms it; this dialog is the part that actually pulls the
trigger, and it stays cancellable for the whole countdown — including from the
keyboard, since the user may be walking past rather than sitting down.
"""
import subprocess
import sys

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar
)
from PySide6.QtCore import Qt, QTimer

from gui2.palette import COLORS, fpx
from gui2.dialogs.common import DialogHeader

# Long enough to notice and stop it, short enough to be useful for an overnight
# queue that finishes at 3am.
COUNTDOWN = 60


def power_command(action):
    """The OS command for ``action``, or None where it is not supported.

    Kept separate from the dialog so the mapping can be asserted in tests
    without anything being executed.
    """
    if sys.platform != "win32":
        return None
    if action == "Shut down":
        # /t 0 because our own countdown has already run; Windows would
        # otherwise add a second, redundant delay on top.
        return ["shutdown", "/s", "/t", "0"]
    if action == "Sleep":
        # SetSuspendState's first argument is Hibernate: 0 = sleep. Note this
        # hibernates instead if hibernation is enabled on the machine — a
        # Windows quirk, not something the app can override.
        return ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
    return None


class ShutdownDialog(QDialog):
    """Armed countdown with a Cancel that really cancels."""

    def __init__(self, parent=None, action="Shut down", seconds=COUNTDOWN):
        super().__init__(parent)
        self.setWindowTitle(f"{action} scheduled")
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.setMinimumWidth(420)
        self._action = action
        self._left = int(seconds)
        self._fired = False

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)
        v.addWidget(DialogHeader(f"{action} when finished"))

        self.msg = QLabel()
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet(f"color:{COLORS['text']};font-size:{fpx(13)};"
                               f"background:transparent;")
        v.addWidget(self.msg)

        self.bar = QProgressBar()
        self.bar.setRange(0, self._left)
        self.bar.setValue(self._left)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(
            f"QProgressBar{{background:{COLORS['surface2']};border:none;"
            f"border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{COLORS['warning']};"
            f"border-radius:3px;}}")
        v.addWidget(self.bar)

        row = QHBoxLayout()
        row.addStretch()
        # Cancel gets the emphasis, not the irreversible action: the accent
        # should not be pulling the eye toward the button that turns the machine
        # off. Enter cancels too — nothing here arms on a stray keypress.
        cancel = QPushButton("Keep running")
        cancel.setObjectName("primary")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        now = QPushButton(f"{action} now")
        now.clicked.connect(self._fire)
        row.addWidget(cancel)
        row.addWidget(now)
        v.addLayout(row)

        self._tick()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # ---- countdown ----
    def _tick(self):
        self.msg.setText(
            f"All downloads have finished. This PC will {self._action.lower()} "
            f"in {self._left} second{'' if self._left == 1 else 's'}.")
        self.bar.setValue(max(0, self._left))
        if self._left <= 0:
            self._fire()
            return
        self._left -= 1

    def _fire(self):
        if self._fired:
            return
        self._fired = True
        if hasattr(self, "_timer"):
            self._timer.stop()
        cmd = power_command(self._action)
        if cmd:
            try:
                subprocess.Popen(
                    cmd,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                pass
        self.accept()

    def reject(self):
        """Cancel — stop the timer first so a tick mid-close cannot still fire."""
        if hasattr(self, "_timer"):
            self._timer.stop()
        self._fired = True
        super().reject()

    def closeEvent(self, e):
        # Closing the window is a cancel, not a silent consent to shut down.
        self.reject()
        e.accept()
