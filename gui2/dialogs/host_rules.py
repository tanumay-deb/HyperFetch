"""Per-host rules editor (Settings → Network).

Edits a {host: {"segments": int, "ytdlp": bool}} dict in place: per host, override
the segment count and/or force the yt-dlp engine. The host matches the exact host
or any subdomain (a rule for 'example.com' also covers 'cdn.example.com').
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton,
    QFrame, QCheckBox
)
from PySide6.QtCore import Qt

from gui2.palette import COLORS
from gui2.brand import BrandLogo
from gui.icons import themed_icon


class HostRulesDialog(QDialog):
    def __init__(self, parent, rules):
        super().__init__(parent)
        self.rules = rules                       # mutated in place
        self.setWindowTitle("Per-host rules")
        self.setMinimumWidth(520)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 18); v.setSpacing(14)
        head = QHBoxLayout(); head.addWidget(BrandLogo(20))
        title = QLabel("Per-host rules"); title.setObjectName("dlgTitle")
        head.addWidget(title); head.addStretch()
        v.addLayout(head)
        sub = QLabel("Override the segment count or force yt-dlp for specific hosts.")
        sub.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        v.addWidget(sub)

        self._rows = QVBoxLayout(); self._rows.setSpacing(8)
        v.addLayout(self._rows)
        self._rebuild()

        # add row
        add = QFrame(); add.setObjectName("panel")
        ah = QHBoxLayout(add); ah.setContentsMargins(12, 10, 12, 10); ah.setSpacing(8)
        self.host_edit = QLineEdit(); self.host_edit.setPlaceholderText("host (e.g. example.com)")
        self.seg_edit = QSpinBox(); self.seg_edit.setRange(1, 32); self.seg_edit.setValue(4); self.seg_edit.setFixedWidth(56)
        self.ytdlp_chk = QCheckBox("yt-dlp")
        add_btn = QPushButton("  Add"); add_btn.setIcon(themed_icon("plus", "white")); add_btn.setObjectName("primary"); add_btn.clicked.connect(self._add)
        self.host_edit.returnPressed.connect(self._add)
        ah.addWidget(self.host_edit, 1); ah.addWidget(QLabel("segs")); ah.addWidget(self.seg_edit)
        ah.addWidget(self.ytdlp_chk); ah.addWidget(add_btn)
        v.addWidget(add)

        foot = QHBoxLayout(); foot.addStretch()
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        foot.addWidget(close)
        v.addLayout(foot)

    def _rebuild(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self.rules:
            empty = QLabel("No rules yet — add one below.")
            empty.setStyleSheet(f"color:{COLORS['faint']};background:transparent;")
            self._rows.addWidget(empty)
            return
        for host in sorted(self.rules):
            self._rows.addWidget(self._row(host, self.rules[host]))

    def _row(self, host, rule):
        f = QFrame(); f.setObjectName("panel")
        h = QHBoxLayout(f); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        name = QLabel(host); name.setStyleSheet("font-weight:700;background:transparent;")
        spin = QSpinBox(); spin.setRange(1, 32); spin.setValue(int(rule.get("segments", 4) or 4)); spin.setFixedWidth(56)
        spin.valueChanged.connect(lambda n, hh=host: self.rules[hh].__setitem__("segments", n))
        yt = QCheckBox("yt-dlp"); yt.setChecked(bool(rule.get("ytdlp")))
        yt.toggled.connect(lambda on, hh=host: self.rules[hh].__setitem__("ytdlp", on))
        dele = QPushButton(); dele.setIcon(themed_icon("trash", "muted")); dele.setObjectName("iconbtn"); dele.setFixedSize(30, 28)
        dele.setToolTip("Remove rule"); dele.clicked.connect(lambda _=False, hh=host: self._del(hh))
        h.addWidget(name, 1); h.addWidget(QLabel("segs")); h.addWidget(spin); h.addWidget(yt); h.addWidget(dele)
        return f

    def _add(self):
        host = self.host_edit.text().strip().lower().lstrip(".")
        # tolerate a pasted URL — keep just the host
        if "//" in host:
            host = host.split("//", 1)[1]
        host = host.split("/")[0].split("@")[-1].split(":")[0]
        if host:
            self.rules[host] = {"segments": self.seg_edit.value(), "ytdlp": self.ytdlp_chk.isChecked()}
            self.host_edit.clear(); self.ytdlp_chk.setChecked(False)
            self._rebuild()

    def _del(self, host):
        self.rules.pop(host, None)
        self._rebuild()
