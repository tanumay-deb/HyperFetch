"""DownloadList — a scrollable, grouped column of DownloadCardWidgets.

Groups into Active / Paused / Completed / Failed sections. Cards are cached by
task id and updated in place; the layout is rebuilt only when group membership
changes. Supports multi-select (single / ctrl-toggle / shift-range).
"""
from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout, QLabel
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

    def __init__(self, parent=None):
        super().__init__(parent)
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

    # ---- empty state ----
    def _make_empty(self):
        w = QWidget(); v = QVBoxLayout(w); v.setAlignment(Qt.AlignCenter); v.setContentsMargins(0, 80, 0, 0)
        icon = QLabel(); icon.setAlignment(Qt.AlignCenter); icon.setStyleSheet("background: transparent;")
        icon.setPixmap(themed_icon("download", COLORS['faint']).pixmap(44, 44))
        v.addWidget(icon)
        for text, st in (("No downloads yet", f"font-size:17px;font-weight:700;color:{COLORS['text']};background:transparent;"),
                         ("Paste a URL or drag & drop a link to get started.", f"font-size:13px;color:{COLORS['muted']};background:transparent;")):
            l = QLabel(text); l.setStyleSheet(st); l.setAlignment(Qt.AlignCenter); v.addWidget(l)
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
    def set_tasks(self, tasks, speeds):
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
                card.update_task(t, speeds.get(t.id, 0.0))

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
                else:
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
