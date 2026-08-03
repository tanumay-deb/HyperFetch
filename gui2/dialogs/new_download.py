"""New Download dialog (v2) — tabbed URL / Torrent / Magnet with Save-to,
Category, Queue and a collapsible Advanced section. Returns a plain dict via
values(); the app builds the DownloadTask from it.

Kept deliberately short. Every row here is a row between the user and the thing
they actually asked for, so anything that does not change the outcome is gone:
the in-window title (the title bar already says it), Priority (the queue runs
by concurrency, not by rank), and the Filename/Category fields on the torrent
tabs, where a torrent names itself and nothing is categorised.
"""
import os

from PySide6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QSpinBox, QCheckBox, QFileDialog, QFrame,
    QApplication
)
from PySide6.QtCore import Qt

import utils
from gui2.palette import COLORS
from gui.icons import themed_icon

URL_TAB, TORRENT_TAB, MAGNET_TAB = 0, 1, 2

# yt-dlp quality presets -> format string. Single-file (progressive) selectors so
# no ffmpeg merge is needed; "Best (auto)" defers to yt-dlp's default.
_YT_QUALITY = [
    ("Best (auto)", ""),
    ("2160p (4K)", "b[height<=2160]"),
    ("1440p", "b[height<=1440]"),
    ("1080p", "b[height<=1080]"),
    ("720p", "b[height<=720]"),
    ("480p", "b[height<=480]"),
    ("Audio only", "ba[ext=m4a]/bestaudio/best"),
]


class NewDownloadDialog(QDialog):
    def __init__(self, parent, save_dir, queues, segments,
                 url="", suggested="", headers=None, categorize=True):
        super().__init__(parent)
        self.setWindowTitle("New Download")
        self.setMinimumWidth(430)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._headers = dict(headers or {})
        self._categorize = categorize
        # A magnet or .torrent has no useful suggested filename — callers derive
        # one from the URL and get literally "magnet:". Left in the box it was
        # not just ugly: switching to the URL tab afterwards would have submitted
        # "magnet:" as the download's name.
        low = (url or "").lower()
        if low.startswith("magnet:") or low.endswith(".torrent"):
            suggested = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        # No in-window title: the title bar already reads "New Download", and a
        # second copy of it was the tallest thing on the dialog.

        # ---- tabs: URL / Torrent / Magnet ----
        self.tabs = QTabWidget()
        pad = (0, 10, 0, 2)
        # URL
        url_tab = QWidget(); ut = QVBoxLayout(url_tab); ut.setContentsMargins(*pad); ut.setSpacing(5)
        urow = QHBoxLayout(); urow.setSpacing(6)
        self.url_edit = QLineEdit(url); self.url_edit.setPlaceholderText("https://example.com/file.zip")
        self.url_edit.setClearButtonEnabled(True)
        paste = QPushButton(); paste.setIcon(themed_icon("clipboard", "text")); paste.setObjectName("iconbtn"); paste.setFixedSize(34, 34)
        paste.setToolTip("Paste"); paste.clicked.connect(self._paste)
        urow.addWidget(self.url_edit, 1); urow.addWidget(paste)
        ut.addLayout(urow)
        self.tabs.addTab(url_tab, themed_icon("link", "muted"), "URL")
        # Torrent
        tor_tab = QWidget(); tt = QVBoxLayout(tor_tab); tt.setContentsMargins(*pad); tt.setSpacing(5)
        trow = QHBoxLayout(); trow.setSpacing(6)
        self.tor_edit = QLineEdit(); self.tor_edit.setPlaceholderText("Select a .torrent file…"); self.tor_edit.setReadOnly(True)
        browse_tor = QPushButton("Browse…"); browse_tor.clicked.connect(self._browse_torrent)
        trow.addWidget(self.tor_edit, 1); trow.addWidget(browse_tor)
        tt.addLayout(trow)
        self.tabs.addTab(tor_tab, themed_icon("plus-circle", "muted"), "Torrent")
        # Magnet
        mag_tab = QWidget(); mt = QVBoxLayout(mag_tab); mt.setContentsMargins(*pad); mt.setSpacing(5)
        self.mag_edit = QLineEdit(); self.mag_edit.setPlaceholderText("magnet:?xt=urn:btih:…")
        self.mag_edit.setClearButtonEnabled(True)
        mt.addWidget(self.mag_edit)
        self.tabs.addTab(mag_tab, themed_icon("magnet", "muted"), "Magnet")
        lay.addWidget(self.tabs)

        # ---- Save to ----
        lay.addWidget(self._label("Save to"))
        srow = QHBoxLayout(); srow.setSpacing(6)
        self.dir_edit = QLineEdit(save_dir)
        browse_dir = QPushButton("Browse…"); browse_dir.clicked.connect(self._browse_dir)
        srow.addWidget(self.dir_edit, 1); srow.addWidget(browse_dir)
        lay.addLayout(srow)

        # ---- Filename (URL downloads only) ----
        self.name_lbl = self._label("Filename (optional)")
        self.name_edit = QLineEdit(suggested); self.name_edit.setPlaceholderText("auto-detected")
        lay.addWidget(self.name_lbl)
        lay.addWidget(self.name_edit)

        # ---- Category | Queue ----
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(3)
        self.cat_lbl = self._label("Category")
        grid.addWidget(self.cat_lbl, 0, 0)
        grid.addWidget(self._label("Queue"), 0, 1)
        self.cat = QComboBox(); self.cat.addItems(["Auto"] + list(utils.CATEGORIES) + ["Other"])
        self.q = QComboBox(); self.q.addItems(queues or ["Main"])
        grid.addWidget(self.cat, 1, 0); grid.addWidget(self.q, 1, 1)
        lay.addLayout(grid)

        # Destination hint — only when it differs from the folder shown directly
        # above it. Repeating "Saved to: <the path you just read>" is a line of
        # noise on every non-categorised download.
        self.dest_hint = QLabel("")
        self.dest_hint.setStyleSheet(f"color:{COLORS['muted']};background:transparent;font-size:11px;")
        self.dest_hint.setWordWrap(True)
        lay.addWidget(self.dest_hint)
        for sig in (self.name_edit.textChanged, self.url_edit.textChanged,
                    self.cat.currentTextChanged, self.dir_edit.textChanged):
            sig.connect(self._update_dest_hint)
        self.tabs.currentChanged.connect(self._sync_tab_fields)

        # ---- advanced (collapsible) ----
        self.adv_btn = QPushButton("  Advanced Options"); self.adv_btn.setObjectName("ghost")
        self.adv_btn.setIcon(themed_icon("chevron-right", "muted"))
        self.adv_btn.setStyleSheet(f"text-align: left; color: {COLORS['muted']}; font-weight: 700;")
        self.adv_btn.setCursor(Qt.PointingHandCursor); self.adv_btn.clicked.connect(self._toggle_adv)
        lay.addWidget(self.adv_btn)
        self.adv = QFrame(); self.adv.setObjectName("panel"); self.adv.setVisible(False)
        av = QGridLayout(self.adv); av.setContentsMargins(14, 12, 14, 12); av.setHorizontalSpacing(12)
        av.addWidget(self._label("Connections"), 0, 0)
        self.conns = QSpinBox(); self.conns.setRange(1, 32); self.conns.setValue(int(segments))
        av.addWidget(self.conns, 1, 0)
        av.addWidget(self._label("Referer"), 0, 1)
        self.referer = QLineEdit(self._headers.get("Referer", "")); av.addWidget(self.referer, 1, 1)
        av.addWidget(self._label("User-Agent"), 2, 0, 1, 2)
        self.ua = QLineEdit(self._headers.get("User-Agent", "")); av.addWidget(self.ua, 3, 0, 1, 2)
        from yt_dl import is_ytdlp_url
        self.use_ytdlp = QCheckBox("Use yt-dlp (YouTube & video sites)")
        self.use_ytdlp.setChecked(is_ytdlp_url(url))
        av.addWidget(self.use_ytdlp, 4, 0, 1, 2)
        # quality picker — only meaningful for yt-dlp downloads
        av.addWidget(self._label("Quality (yt-dlp)"), 5, 0)
        self.quality = QComboBox(); self.quality.addItems([q[0] for q in _YT_QUALITY])
        self.quality.setToolTip(
            "Video/audio quality for media-page (yt-dlp) downloads.\n"
            "Merged with the bundled ffmpeg (falls back to ≤720p if ffmpeg is missing).")
        self.quality.setEnabled(self.use_ytdlp.isChecked())
        self.use_ytdlp.toggled.connect(self.quality.setEnabled)
        av.addWidget(self.quality, 5, 1)
        # auto-tick when a known media URL is typed/pasted (never auto-untick)
        self.url_edit.textChanged.connect(
            lambda t: self.use_ytdlp.setChecked(True) if is_ytdlp_url(t) else None)
        lay.addWidget(self.adv)

        # ---- footer ----
        self.start_now = QCheckBox("Start download immediately"); self.start_now.setChecked(True)
        lay.addWidget(self.start_now)
        self.skip_next = QCheckBox("Don't show this again")
        self.skip_next.setToolTip(
            "Downloads are added straight away with these defaults.\n"
            "Turn back on in Settings → Downloads.")
        lay.addWidget(self.skip_next)

        foot = QHBoxLayout(); foot.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        dl = QPushButton("  Download"); dl.setIcon(themed_icon("download", "white")); dl.setObjectName("primary"); dl.clicked.connect(self._accept)
        foot.addWidget(cancel); foot.addWidget(dl)
        lay.addLayout(foot)

        # focus the tab matching the incoming url + prefill that tab's field
        if url.lower().startswith("magnet:"):
            self.url_edit.clear()
            self.mag_edit.setText(url)
            self.tabs.setCurrentIndex(MAGNET_TAB)
        elif url.lower().endswith(".torrent"):
            self.url_edit.clear()
            self.tor_edit.setText(url)
            self.tabs.setCurrentIndex(TORRENT_TAB)
        self._sync_tab_fields()

    # ---- helpers ----
    def _label(self, text):
        l = QLabel(text); l.setObjectName("fieldLabel"); return l

    def _sync_tab_fields(self, *_):
        """Hide the fields that do nothing on the current tab.

        A torrent takes its name and layout from its own metadata, so Filename
        and Category decide nothing there — leaving them visible invites the
        user to fill in a value that is then ignored.
        """
        url_tab = self.tabs.currentIndex() == URL_TAB
        for w in (self.name_lbl, self.name_edit, self.cat_lbl, self.cat):
            w.setVisible(url_tab)
        self._update_dest_hint()
        self.adjustSize()

    def _update_dest_hint(self):
        """Name the real destination only when it is NOT the folder already
        shown above — i.e. when an auto category adds a subfolder. Otherwise it
        just repeats the line above it."""
        base = self.dir_edit.text().strip()
        cat = self.cat.currentText()
        name = self.name_edit.text().strip()
        if not name:                                   # URL tab: derive the auto name
            try:
                name = utils.filename_from_url(self.url_edit.text().strip())
            except Exception:
                name = ""
        sub = ""
        if self.tabs.currentIndex() == URL_TAB:        # only the URL tab categorises
            if cat == "Auto":
                if self._categorize and name:
                    c = utils.category_for(name)
                    sub = "" if c == "Other" else c
            else:
                sub = cat                              # explicit pick (incl. "Other")
        if sub:
            self.dest_hint.setText(f"Saved to:  {os.path.join(base, sub)}")
            self.dest_hint.setVisible(True)
        else:
            self.dest_hint.setVisible(False)

    def _paste(self):
        self.url_edit.setText(QApplication.clipboard().text().strip())

    def _browse_torrent(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select .torrent", self.dir_edit.text(), "Torrent (*.torrent)")
        if f:
            self.tor_edit.setText(f)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _toggle_adv(self):
        on = not self.adv.isVisible()
        self.adv.setVisible(on)
        self.adv_btn.setIcon(themed_icon("chevron-down" if on else "chevron-right", "muted"))
        self.adjustSize()

    def _source_url(self):
        i = self.tabs.currentIndex()
        if i == 0:
            return self.url_edit.text().strip()
        if i == 1:
            return self.tor_edit.text().strip()
        return self.mag_edit.text().strip()

    def _accept(self):
        if not self._source_url():
            self.tabs.currentWidget().setFocus()
            return
        self.accept()

    def values(self):
        h = dict(self._headers)
        if self.referer.text().strip():
            h["Referer"] = self.referer.text().strip()
        if self.ua.text().strip():
            h["User-Agent"] = self.ua.text().strip()
        torrent_tab = self.tabs.currentIndex() != URL_TAB
        return {
            "url": self._source_url(),
            "save_dir": self.dir_edit.text().strip(),
            # a torrent names itself from its metadata, so whatever is sitting in
            # the box (often a useless "magnet:") must not be forced onto it
            "filename": "" if torrent_tab else self.name_edit.text().strip(),
            "category": "Auto" if torrent_tab else self.cat.currentText(),
            "queue": self.q.currentText(),
            # Priority is gone from the UI: the queue runs by concurrency, and a
            # rank the scheduler barely acts on was one more decision to make.
            "priority": 0,
            "skip_dialog": self.skip_next.isChecked(),
            "connections": self.conns.value(),
            "start_now": self.start_now.isChecked(),
            "use_ytdlp": self.use_ytdlp.isChecked(),
            "yt_format": dict(_YT_QUALITY).get(self.quality.currentText(), ""),
            "headers": h,
        }
