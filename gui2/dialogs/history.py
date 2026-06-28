"""Download History + stats dashboard (v2). Reads the persisted ``history.json``
(via the ``history`` module) and shows lifetime totals plus a table of past
completed downloads, with Open Folder / Copy URL / Clear actions.
"""
import os
import subprocess

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication
)
from PySide6.QtCore import Qt

import history
from gui2.palette import COLORS
from gui2.dialogs.common import DialogHeader
from gui.icons import themed_icon
from gui.theme import human_size, humanize_age


class HistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download History")
        self.setMinimumSize(620, 480)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)

        # ---- header ----
        v.addWidget(DialogHeader("Download History"))

        # ---- stat cards ----
        self.cards = QHBoxLayout(); self.cards.setSpacing(12)
        v.addLayout(self.cards)

        # ---- table ----
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Size", "Category", "Completed"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda *_: self._open_folder())
        v.addWidget(self.table, 1)

        # ---- footer ----
        foot = QHBoxLayout()
        clear = QPushButton("  Clear History"); clear.setIcon(themed_icon("trash", "muted"))
        clear.clicked.connect(self._clear)
        foot.addWidget(clear); foot.addStretch()
        openf = QPushButton("  Open Folder"); openf.setIcon(themed_icon("open", "text"))
        openf.clicked.connect(self._open_folder)
        copy = QPushButton("  Copy URL"); copy.setIcon(themed_icon("link", "text"))
        copy.clicked.connect(self._copy_url)
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        foot.addWidget(openf); foot.addWidget(copy); foot.addWidget(close)
        v.addLayout(foot)

        self._reload()

    # ---- data ----
    def _reload(self):
        self._records = list(reversed(history.load()))   # most recent first
        st = history.stats()
        self._build_cards(st)
        self.table.setRowCount(0)
        for r in self._records:
            row = self.table.rowCount(); self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(r.get("filename", "")))
            self.table.setItem(row, 1, QTableWidgetItem(human_size(int(r.get("size", 0) or 0))))
            self.table.setItem(row, 2, QTableWidgetItem(r.get("category", "Other")))
            self.table.setItem(row, 3, QTableWidgetItem(humanize_age(r.get("completed_at", 0))))

    def _build_cards(self, st):
        while self.cards.count():
            it = self.cards.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        top_cat = max(st["by_category"].items(), key=lambda kv: kv[1])[0] if st["by_category"] else "—"
        self.cards.addWidget(self._stat_card("Total Downloaded", human_size(st["total_bytes"])))
        self.cards.addWidget(self._stat_card("Files Completed", str(st["count"])))
        self.cards.addWidget(self._stat_card("Top Category", top_cat))

    def _stat_card(self, label, value):
        f = QFrame(); f.setObjectName("panel")
        g = QVBoxLayout(f); g.setContentsMargins(16, 12, 16, 12); g.setSpacing(2)
        val = QLabel(value); val.setStyleSheet("font-size:22px;font-weight:800;background:transparent;")
        cap = QLabel(label); cap.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
        g.addWidget(val); g.addWidget(cap)
        return f

    # ---- actions ----
    def _selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def _open_folder(self):
        r = self._selected()
        if not r:
            return
        path = r.get("path", "")
        folder = os.path.dirname(path) if path else ""
        try:
            if path and os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif folder and os.path.isdir(folder):
                os.startfile(folder)  # noqa: S606 (Windows)
        except Exception:
            pass

    def _copy_url(self):
        r = self._selected()
        if r and r.get("url"):
            QApplication.clipboard().setText(r["url"])

    def _clear(self):
        history.clear()
        self._reload()
