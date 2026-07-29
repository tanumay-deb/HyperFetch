"""Download History + stats dashboard (v2). Reads the persisted ``history.json``
(via the ``history`` module) and shows lifetime totals plus a searchable,
sortable table of past completed downloads, with Open File / Open Folder /
Copy URL / Clear actions.
"""
import os
import subprocess

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QStackedWidget, QWidget
)
from PySide6.QtCore import Qt

import history
from gui2 import search as _search
from gui2.palette import COLORS, fpx
from gui2.dialogs.common import DialogHeader
from gui.icons import themed_icon
from gui.theme import human_size, humanize_age


class _SortItem(QTableWidgetItem):
    """Table cell that sorts on a supplied key rather than its display text —
    "1.2 GB" must sort above "900 MB", and "2 days ago" below "just now"."""

    def __init__(self, text, key):
        super().__init__(text)
        self._key = key

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            try:
                return self._key < other._key
            except TypeError:
                pass
        return super().__lt__(other)


class HistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download History")
        self.setMinimumSize(720, 540)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)

        # ---- header ----
        v.addWidget(DialogHeader("Download History"))

        # ---- stat cards ----
        self.cards = QHBoxLayout(); self.cards.setSpacing(12)
        v.addLayout(self.cards)

        # ---- search ----
        row = QHBoxLayout(); row.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search history…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Search by name/URL, or filter with tokens:\n"
                               "category:video · ext:zip · size:>100mb · date:7d")
        self.search.textChanged.connect(self._apply_filter)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{COLORS['muted']};font-size: {fpx(12)};background:transparent;")
        row.addWidget(self.search, 1); row.addWidget(self.count)
        v.addLayout(row)

        # ---- table / empty state ----
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Size", "Category", "Completed"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSortIndicator(3, Qt.DescendingOrder)          # newest first
        self.table.itemDoubleClicked.connect(lambda *_: self._open_file())
        self.table.itemSelectionChanged.connect(self._sync_buttons)

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{COLORS['muted']};font-size: {fpx(13)};background:transparent;")

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        v.addWidget(self.stack, 1)

        # ---- footer ----
        foot = QHBoxLayout()
        clear = QPushButton("  Clear History"); clear.setIcon(themed_icon("trash", "muted"))
        clear.clicked.connect(self._clear)
        foot.addWidget(clear); foot.addStretch()
        self.btn_open = QPushButton("  Open File"); self.btn_open.setIcon(themed_icon("open", "text"))
        self.btn_open.clicked.connect(self._open_file)
        self.btn_folder = QPushButton("  Open Folder"); self.btn_folder.setIcon(themed_icon("folder", "text"))
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_copy = QPushButton("  Copy URL"); self.btn_copy.setIcon(themed_icon("link", "text"))
        self.btn_copy.clicked.connect(self._copy_url)
        close = QPushButton("Close"); close.setObjectName("primary"); close.clicked.connect(self.accept)
        for b in (self.btn_open, self.btn_folder, self.btn_copy, close):
            foot.addWidget(b)
        v.addLayout(foot)

        self._reload()

    # ---- data ----
    def _reload(self):
        self._records = list(reversed(history.load()))   # most recent first
        self._build_cards(history.stats())
        self._apply_filter()

    def _apply_filter(self):
        query = self.search.text()
        rows = _search.filter_records(self._records, query)
        self._fill(rows)
        total = len(self._records)
        if not total:
            self.count.setText("")
        elif len(rows) == total:
            self.count.setText(f"{total} download{'' if total == 1 else 's'}")
        else:
            self.count.setText(f"{len(rows)} of {total}")

        if rows:
            self.stack.setCurrentWidget(self.table)
        else:
            self.empty.setText(
                "No downloads yet — completed downloads are recorded here."
                if not total else
                f"Nothing matches “{query.strip()}”.\n"
                "Try a plain word, or a token like category:video, ext:zip, size:>100mb")
            self.stack.setCurrentWidget(self.empty)
        self._sync_buttons()

    def _fill(self, rows):
        # sorting must be off while inserting, or Qt re-sorts mid-populate and
        # the row indexes shift under us
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount(); self.table.insertRow(i)
            size = int(r.get("size", 0) or 0)
            when = float(r.get("completed_at", 0) or 0)
            name = QTableWidgetItem(r.get("filename", ""))
            # the record travels WITH the row: after filtering or sorting, the
            # visible row number no longer indexes self._records
            name.setData(Qt.UserRole, r)
            path = r.get("path", "")
            if path:
                name.setToolTip(path)
            missing = bool(path) and not os.path.exists(path)
            if missing:
                name.setForeground(Qt.gray)
                name.setToolTip(f"{path}\n(file no longer at this location)")
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, _SortItem(human_size(size), size))
            self.table.setItem(i, 2, QTableWidgetItem(r.get("category", "Other")))
            self.table.setItem(i, 3, _SortItem(humanize_age(when) or "—", when))
        self.table.setSortingEnabled(True)

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
        val = QLabel(value); val.setStyleSheet(f"font-size:{fpx(22)};font-weight:800;background:transparent;")
        cap = QLabel(label); cap.setStyleSheet(f"color:{COLORS['muted']};font-size: {fpx(11)};background:transparent;")
        g.addWidget(val); g.addWidget(cap)
        return f

    # ---- actions ----
    def _selected(self):
        """The record behind the selected row — read from the row itself, so it
        stays correct however the table is filtered or sorted."""
        items = self.table.selectedItems()
        if not items:
            return None
        return self.table.item(items[0].row(), 0).data(Qt.UserRole)

    def _sync_buttons(self):
        r = self._selected()
        path = (r or {}).get("path", "")
        on_disk = bool(path) and os.path.exists(path)
        self.btn_open.setEnabled(on_disk)
        self.btn_folder.setEnabled(bool(r))
        self.btn_copy.setEnabled(bool(r and r.get("url")))

    def _open_file(self):
        r = self._selected()
        path = (r or {}).get("path", "")
        if path and os.path.exists(path):
            try:
                os.startfile(path)  # noqa: S606 (Windows)
            except OSError:
                pass
        else:
            self._open_folder()          # file moved/deleted: show where it was

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
        self.search.clear()
        self._reload()
