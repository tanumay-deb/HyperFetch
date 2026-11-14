"""What's new: the first start of a new version after an update shows that
version's notes (notes/<version>.md, shipped in the app and used as the GitHub
release's text). Shown once; a fresh install gets the Welcome window instead.
"""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout

import updater
import utils
from gui2.dialogs.common import DialogHeader
from gui2.palette import COLORS, DIALOG_MARGIN


def due(extras, version):
    """Whether this start is the first of `version` after an update: someone
    who has used the app (welcomed) and last ran an older version - or one
    from before versions were recorded. Never for a downgrade."""
    return bool(extras.get("welcomed")) and updater._newer(version, extras.get("last_version") or "0.0")


def notes_for(version):
    """The notes of `version`, or None when it has none."""
    try:
        with open(utils.resource_path("notes", version + ".md"), encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text or None


class WhatsNewDialog(QDialog):
    def __init__(self, parent, version, text):
        super().__init__(parent)
        self.setWindowTitle("What's new in " + version)
        self.setMinimumSize(560, 480)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self)
        v.setContentsMargins(*DIALOG_MARGIN)
        v.setSpacing(14)
        v.addWidget(DialogHeader("What's new in HyperFetch " + version))

        notes = QTextBrowser()
        notes.setObjectName("panel")
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(text)
        notes.document().setDocumentMargin(14)
        notes.setStyleSheet(f"color:{COLORS['text']};")
        v.addWidget(notes, 1)

        foot = QHBoxLayout()
        all_notes = QPushButton("All release notes")
        all_notes.clicked.connect(self._open_releases)
        foot.addWidget(all_notes)
        foot.addStretch()
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.close)
        foot.addWidget(close)
        v.addLayout(foot)

    def _open_releases(self):
        QDesktopServices.openUrl(QUrl("https://github.com/%s/releases" % updater.REPO))
