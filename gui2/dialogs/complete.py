"""Download Complete popup (v2, mockup #4) — celebratory card with file stats
and Open File / Open Folder / View in List / Close.
"""
import os
import time

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal

import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta
from gui2.palette import COLORS


class CompleteDialog(QDialog):
    viewInList = Signal(str)

    def __init__(self, parent, t):
        super().__init__(parent)
        self.setWindowTitle("Download Complete")
        self.setMinimumWidth(380)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.t = t

        v = QVBoxLayout(self); v.setContentsMargins(24, 22, 24, 20); v.setSpacing(10)
        v.setAlignment(Qt.AlignHCenter)

        check = QLabel("✓")
        check.setAlignment(Qt.AlignCenter)
        check.setFixedSize(74, 74)
        check.setStyleSheet(
            f"background:{COLORS['success']}33;color:{COLORS['success']};"
            f"border:2px solid {COLORS['success']};border-radius:37px;font-size:34px;font-weight:800;")
        v.addWidget(check, 0, Qt.AlignHCenter)

        title = QLabel("Download Completed!"); title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:18px;font-weight:800;background:transparent;")
        sub = QLabel("The file has been downloaded successfully."); sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        v.addWidget(title); v.addWidget(sub)

        panel = QFrame(); panel.setObjectName("panel")
        pg = QVBoxLayout(panel); pg.setContentsMargins(14, 12, 14, 12); pg.setSpacing(8)
        top = QHBoxLayout()
        ic = QLabel("🧲" if _torrent.is_torrent_task(t.url, t.filename) else "📄")
        ic.setStyleSheet("font-size:22px;background:transparent;")
        nm = QVBoxLayout(); nm.setSpacing(0)
        name = QLabel(t.filename or "download"); name.setStyleSheet("font-weight:700;background:transparent;")
        size = QLabel(human_size(t.total_size or t.downloaded)); size.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
        nm.addWidget(name); nm.addWidget(size)
        top.addWidget(ic); top.addLayout(nm, 1)
        pg.addLayout(top)

        # stats (best-effort from available data)
        elapsed = max(1e-9, time.time() - getattr(t, "added", 0)) if getattr(t, "added", 0) else 0
        avg = (t.downloaded / elapsed) if elapsed else 0
        for label, value in (
            ("Downloaded", human_size(t.downloaded)),
            ("Total Time", fmt_eta(elapsed) if elapsed else "—"),
            ("Average Speed", human_speed(avg) if avg else "—"),
            ("Connections", str(len(t.segments) or "—")),
        ):
            r = QHBoxLayout()
            l = QLabel(label); l.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
            x = QLabel(value); x.setStyleSheet("font-weight:600;background:transparent;")
            r.addWidget(l); r.addStretch(); r.addWidget(x)
            pg.addLayout(r)
        v.addWidget(panel)

        row = QHBoxLayout()
        of = QPushButton("📂  Open File"); of.clicked.connect(self._open_file)
        ofd = QPushButton("📁  Open Folder"); ofd.clicked.connect(self._open_folder)
        vl = QPushButton("View in List"); vl.clicked.connect(lambda: (self.viewInList.emit(self.t.id), self.accept()))
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        for b in (of, ofd, vl):
            row.addWidget(b)
        row.addStretch(); row.addWidget(close)
        v.addLayout(row)

    def _target(self):
        p = self.t.save_path
        if os.path.exists(p):
            return p
        folder = os.path.dirname(p) or "."
        if _torrent.is_torrent_task(self.t.url, self.t.filename) and os.path.isdir(folder):
            return folder
        return ""

    def _open_file(self):
        tgt = self._target()
        if tgt:
            try:
                os.startfile(tgt)
            except OSError:
                pass
        self.accept()

    def _open_folder(self):
        path = os.path.normpath(self.t.save_path)
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.path.exists(path):
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(os.path.dirname(path) or ".")
        except OSError:
            pass
        self.accept()
