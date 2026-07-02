"""First-run welcome — explains what's running and walks the user through pairing
the browser extension (the one bit of setup that isn't obvious). Shown once.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QApplication
)
from PySide6.QtCore import Qt, QTimer

from gui2.palette import COLORS, DIALOG_MARGIN
from gui2.dialogs.common import DialogHeader

_STORE_URL = "https://chromewebstore.google.com/detail/hyperfetch/finojjembpabfbincabngboedegokdlm"
_STEPS = [
    f"Install the <b>HyperFetch</b> extension from the "
    f"<a href='{_STORE_URL}' style='color:#8b5cf6;'>Chrome Web Store</a> "
    "(Chrome, Edge or Brave).",
    "Click the HyperFetch extension icon and paste the pairing token below.",
    "Right-click any link → <b>Download with HyperFetch</b>, or turn on capture in the popup.",
]


class WelcomeDialog(QDialog):
    openSettings = None   # set by caller to a callable, optional

    def __init__(self, parent, token, on_open_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome")
        self.setMinimumWidth(560)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._on_open_settings = on_open_settings

        v = QVBoxLayout(self); v.setContentsMargins(*DIALOG_MARGIN); v.setSpacing(14)
        v.addWidget(DialogHeader("Welcome to HyperFetch"))

        intro = QLabel("HyperFetch is running and downloads start here will use "
                       "multiple connections for speed. To send downloads straight "
                       "from your browser, pair the extension once:")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        v.addWidget(intro)

        steps = QFrame(); steps.setObjectName("panel")
        sv = QVBoxLayout(steps); sv.setContentsMargins(16, 14, 16, 14); sv.setSpacing(10)
        for i, txt in enumerate(_STEPS, 1):
            row = QHBoxLayout(); row.setSpacing(10)
            num = QLabel(str(i)); num.setFixedSize(22, 22); num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(f"background:{COLORS['accent']};color:white;border-radius:11px;font-weight:800;")
            lbl = QLabel(txt); lbl.setWordWrap(True); lbl.setTextFormat(Qt.RichText)
            lbl.setOpenExternalLinks(True)        # store link opens in the browser
            lbl.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
            row.addWidget(num, 0, Qt.AlignTop); row.addWidget(lbl, 1)
            sv.addLayout(row)
        v.addWidget(steps)

        # pairing token
        v.addWidget(self._label("Pairing token"))
        trow = QHBoxLayout()
        self.tok = QLineEdit(token); self.tok.setReadOnly(True)
        copy = QPushButton("Copy"); copy.setCursor(Qt.PointingHandCursor)
        copy.setStyleSheet(
            f"QPushButton {{ background: {COLORS['surface2']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 6px 16px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['card_hover']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['accent']}; color: white; }}")

        def _copy():
            QApplication.clipboard().setText(token)
            copy.setText("Copied ✓")
            QTimer.singleShot(1400, lambda: copy.setText("Copy"))
        copy.clicked.connect(_copy)
        trow.addWidget(self.tok, 1); trow.addWidget(copy)
        v.addLayout(trow)

        foot = QHBoxLayout()
        st = QPushButton("Open Settings → Browser"); st.clicked.connect(self._open_settings)
        foot.addWidget(st); foot.addStretch()
        done = QPushButton("  Get started"); done.setObjectName("primary"); done.clicked.connect(self.accept)
        foot.addWidget(done)
        v.addLayout(foot)

    def _label(self, text):
        l = QLabel(text); l.setObjectName("fieldLabel"); return l

    def _open_settings(self):
        self.accept()
        if callable(self._on_open_settings):
            self._on_open_settings()
