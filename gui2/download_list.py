"""DownloadList — a scrollable, grouped column of DownloadCardWidgets.

Groups into Active / Paused / Completed / Failed sections. Cards are cached by
task id and updated in place; the layout is rebuilt only when group membership
changes. Supports multi-select (single / ctrl-toggle / shift-range).
"""
from PySide6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt, Signal

import task as T
from gui2.download_card import DownloadCardWidget
from gui2.palette import COLORS
from gui.icons import themed_icon

_GROUPS = [
    ("Active",    (T.DOWNLOADING, T.QUEUED, T.SCHEDULED)),
    ("Paused",    (T.PAUSED,)),
    ("Completed", (T.COMPLETED,)),
    ("Failed",    (T.ERROR, T.CANCELLED)),
]


class DownloadList(QScrollArea):
    action = Signal(str, str)            # forwarded from cards
    selectionChanged = Signal(object)    # set of selected task ids
    quickAction = Signal(str)            # empty-state buttons: new/torrent/magnet
    blankClicked = Signal()              # left-click on empty list space (not a card)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadList")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._inner = QWidget(); self._inner.setObjectName("listInner")
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(2, 2, 8, 8); self._lay.setSpacing(10)
        self._lay.addStretch()
        self.setWidget(self._inner)

        self._cards = {}
        self._sig = None
        self._order = []                 # visible id order (for shift-range)
        self._selected = set()
        self._anchor = None

        self._empty = self._make_empty()
        self._lay.insertWidget(0, self._empty)

    def mousePressEvent(self, e):
        # cards accept their own clicks, so anything reaching here is empty space
        if e.button() == Qt.LeftButton:
            self.blankClicked.emit()
        super().mousePressEvent(e)

    # ---- empty state ----
    def _make_empty(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setAlignment(Qt.AlignCenter)
        v.setContentsMargins(0, 64, 0, 0); v.setSpacing(6)

        # illustration: brand-accent glyph in a soft rounded tile
        icon = QLabel(); icon.setAlignment(Qt.AlignCenter); icon.setFixedSize(96, 96)
        icon.setPixmap(themed_icon("download", COLORS['accent']).pixmap(52, 52))
        icon.setStyleSheet(
            f"background: {COLORS['surface2']}; border: 1px solid {COLORS['border']};"
            "border-radius: 24px;")
        ihold = QHBoxLayout(); ihold.addStretch(); ihold.addWidget(icon); ihold.addStretch()
        v.addLayout(ihold)
        v.addSpacing(6)

        title = QLabel("No downloads yet"); title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size:19px;font-weight:800;color:{COLORS['text']};background:transparent;")
        sub = QLabel("Paste a link, drop a file, or start one below."); sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"font-size:13px;color:{COLORS['muted']};background:transparent;")
        v.addWidget(title); v.addWidget(sub)
        v.addSpacing(14)

        # quick actions
        row = QHBoxLayout(); row.setSpacing(10); row.setAlignment(Qt.AlignCenter)
        for label, kind, icon_name, primary in (
                ("New Download", "new", "plus", True),
                ("Open Torrent", "torrent", "plus-circle", False),
                ("Open Magnet", "magnet", "magnet", False)):
            b = QPushButton("  " + label)
            if primary:
                b.setObjectName("primary")
            b.setIcon(themed_icon(icon_name, "white" if primary else "text"))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=kind: self.quickAction.emit(k))
            row.addWidget(b)
        rw = QWidget(); rw.setLayout(row)
        rh = QHBoxLayout(); rh.addStretch(); rh.addWidget(rw); rh.addStretch()
        v.addLayout(rh)

        hint = QLabel("or drag files & links anywhere in the window"); hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"font-size:11px;color:{COLORS['faint']};background:transparent;")
        v.addSpacing(10); v.addWidget(hint)
        return w

    def _header(self, title, count):
        h = QLabel(f"{title.upper()}   {count}")
        h.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;font-weight:800;letter-spacing:1px;background:transparent;padding:6px 2px 0;")
        return h

    # ---- selection ----
    def selected_ids(self):
        return set(self._selected)

    def set_selection(self, ids):
        self._selected = set(ids) & set(self._cards)
        # deterministic anchor for a later shift-range = last selected in visible order
        ordered = [i for i in self._order if i in self._selected]
        self._anchor = ordered[-1] if ordered else next(iter(self._selected), None)
        self._apply_selection()
        self.selectionChanged.emit(set(self._selected))

    def clear_selection(self):
        self.set_selection(set())

    def _on_chk_toggled(self, tid, checked):
        if checked:
            self._selected.add(tid)
        else:
            self._selected.discard(tid)
        self._anchor = tid
        self._apply_selection()
        self.selectionChanged.emit(set(self._selected))

    def _on_select(self, tid, mode):
        if mode == "toggle":
            self._selected ^= {tid}
            self._anchor = tid
        elif mode == "range" and self._anchor in self._order and tid in self._order:
            a, b = self._order.index(self._anchor), self._order.index(tid)
            lo, hi = sorted((a, b))
            self._selected = set(self._order[lo:hi + 1])
        else:                                    # single
            self._selected = {tid}
            self._anchor = tid
        self._apply_selection()
        self.selectionChanged.emit(set(self._selected))

    def _apply_selection(self):
        for cid, card in self._cards.items():
            is_sel = cid in self._selected
            card.set_selected(is_sel)
            # sync checkbox without retriggering the toggled signal
            if hasattr(card, 'chk'):
                card.chk.blockSignals(True)
                card.chk.setChecked(is_sel)
                card.chk.blockSignals(False)

    # ---- data ----
    def set_tasks(self, tasks, speeds, histories=None):
        histories = histories or {}
        buckets = []
        for title, statuses in _GROUPS:
            members = [t for t in tasks if t.status in statuses]
            if members:
                buckets.append((title, members))
        self._order = [t.id for _, members in buckets for t in members]

        sig = tuple((title, tuple(t.id for t in members)) for title, members in buckets)
        if sig != self._sig:
            self._rebuild(buckets)
            self._sig = sig

        for t in tasks:
            card = self._cards.get(t.id)
            if card:
                card.update_task(t, speeds.get(t.id, 0.0), histories.get(t.id))

        # prune selection for vanished tasks
        gone = self._selected - set(self._cards)
        if gone:
            self._selected -= gone
            self.selectionChanged.emit(set(self._selected))
        self._apply_selection()
        self._empty.setVisible(not tasks)

    def _rebuild(self, buckets):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is self._empty:
                continue
            if w is not None and not isinstance(w, DownloadCardWidget):
                w.deleteLater()
            elif w is not None:
                w.setParent(None)

        live = {t.id for _, members in buckets for t in members}
        for cid in list(self._cards):
            if cid not in live:
                self._cards.pop(cid).deleteLater()

        self._lay.addWidget(self._empty)
        sl_counter = 0
        for title, members in buckets:
            self._lay.addWidget(self._header(title, len(members)))
            for t in members:
                sl_counter += 1
                card = self._cards.get(t.id)
                if card is None:
                    card = DownloadCardWidget(t, sl_no=sl_counter)
                    card.action.connect(self.action)
                    card.selectRequested.connect(self._on_select)
                    card.chk.toggled.connect(lambda checked, tid=t.id: self._on_chk_toggled(tid, checked))
                    self._cards[t.id] = card
                    self._lay.addWidget(card)
                    card.fade_in()                       # soft entrance for new cards
                    continue
                card.sl_lbl.setText(f"#{sl_counter}")
                self._lay.addWidget(card)
        self._lay.addStretch()

    def _on_chk_toggled(self, tid, checked):
        if checked:
            self._selected.add(tid)
        else:
            self._selected.discard(tid)
        self._anchor = tid
        self._apply_selection()
        self.selectionChanged.emit(set(self._selected))
