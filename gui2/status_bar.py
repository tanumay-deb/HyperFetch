"""The thin bar along the bottom of the window (asked 2026-10-29): what this
session has downloaded and uploaded, the version, and a button that
switches between dark and light.

The switch restarts the app, as the theme setting does (widgets bake their
colours in when they are built), so the totals are handed to the new
process on its command line: a theme switch is not a new session.
"""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton

import task as T
from gui.icons import themed_icon
from gui.theme import APP_VERSION, human_size
from gui2.palette import COLORS, active_theme, fpx

CARRY = "--session-bytes="              # down,up - what a restart hands on


class SessionCount:
    """Bytes downloaded since the app opened: what each download gained while
    it ran, every kind of download alike. A drop - a video starting on its
    sound, a recheck - is not taken off, and what a recheck finds on disk is
    not counted. A download removed from the list keeps what it brought."""

    def __init__(self, start=0):
        self.down = start
        self._seen = {}                 # task id -> its bytes at the last tick
        self._checking = set()          # ids that were being checked then

    def tick(self, tasks):
        seen, checking = {}, set()
        for t in tasks:
            now = int(t.downloaded or 0)
            was = self._seen.get(t.id)
            if getattr(t, "verifying", False):
                checking.add(t.id)
            elif (was is not None and now > was and t.status == T.DOWNLOADING
                  and t.id not in self._checking):
                self.down += now - was
            seen[t.id] = now
        self._seen, self._checking = seen, checking


def carried(argv):
    """(down, up) a restart handed on, or (0, 0)."""
    for a in argv:
        if a.startswith(CARRY):
            try:
                down, up = (max(0, int(x)) for x in a[len(CARRY):].split(","))
                return down, up
            except ValueError:
                break
    return 0, 0


def restart_args(argv, down, up):
    """The arguments the replacement process starts with: these, marked as
    a restart, with the session's totals."""
    keep = [a for a in argv[1:] if a != "--restarted" and not a.startswith(CARRY)]
    return keep + ["--restarted", "%s%d,%d" % (CARRY, down, up)]


class StatusBar(QFrame):
    themeClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(24)
        self.setStyleSheet(
            f"#statusBar {{ background: {COLORS['surface']}; border-top: 1px solid {COLORS['border']}; }}"
            f"QLabel {{ color: {COLORS['muted']}; font-size: {fpx(11)}; background: transparent; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 6, 0)
        h.setSpacing(12)
        self.totals = QLabel()
        self.totals.setToolTip("Downloaded and uploaded since HyperFetch was opened")
        h.addWidget(self.totals)
        h.addStretch(1)
        h.addWidget(QLabel(f"v{APP_VERSION}"))
        to = "light" if active_theme() == "dark" else "dark"
        self.theme_btn = QToolButton()
        self.theme_btn.setAutoRaise(True)
        self.theme_btn.setFixedSize(20, 20)
        self.theme_btn.setIconSize(QSize(14, 14))
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setStyleSheet("QToolButton { border: none; background: transparent; }")
        self.theme_btn.setIcon(themed_icon("sun" if to == "light" else "moon", "muted"))
        self.theme_btn.setToolTip(f"Switch to the {to} theme (HyperFetch restarts)")
        self.theme_btn.clicked.connect(self.themeClicked)
        h.addWidget(self.theme_btn)
        self.set_totals(0, 0)

    def set_totals(self, down, up):
        size = lambda n: human_size(n) if n else "0 B"
        self.totals.setText(f"↓ {size(down)}    ↑ {size(up)}")
