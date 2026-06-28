"""CommandPalette — a Ctrl+K overlay to run any app action by typing.

Faster control: instead of hunting through the sidebar / menus, type a few
letters ("pau", "hist", "queue", "light") and hit Enter. The main window builds
the action list fresh each open so it reflects the current state.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt

from gui.icons import themed_icon
from gui2.palette import COLORS


class CommandPalette(QDialog):
    def __init__(self, parent, actions):
        super().__init__(parent)
        # actions: list of (label, keywords, icon_name, callable)
        self._actions = actions
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("palette")
        self.setFixedWidth(min(560, max(420, (parent.width() if parent else 560) - 160)))
        self.setStyleSheet(
            f"#palette {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 12px; }}"
            f"QLineEdit {{ background: {COLORS['surface2']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 10px 12px; font-size: 15px; }}"
            f"QListWidget {{ background: transparent; border: none; color: {COLORS['text']}; outline: 0; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 7px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent']}; color: white; }}")

        v = QVBoxLayout(self); v.setContentsMargins(12, 12, 12, 12); v.setSpacing(8)
        self.search = QLineEdit(); self.search.setPlaceholderText("Type a command…  (Esc to close)")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._activate_current)
        v.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.itemActivated.connect(lambda _it: self._activate_current())
        self.listw.itemClicked.connect(lambda _it: self._activate_current())
        v.addWidget(self.listw)

        self._filter("")
        self.search.setFocus()

    def _filter(self, text):
        q = text.strip().lower()
        self.listw.clear()
        for label, keywords, icon, _fn in self._actions:
            hay = (label + " " + keywords).lower()
            if all(tok in hay for tok in q.split()):
                it = QListWidgetItem(label)
                if icon:
                    it.setIcon(themed_icon(icon, "text"))
                self.listw.addItem(it)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Up):
            n = self.listw.count()
            if n:
                row = self.listw.currentRow()
                row = (row + (1 if e.key() == Qt.Key_Down else -1)) % n
                self.listw.setCurrentRow(row)
            e.accept(); return
        super().keyPressEvent(e)

    def _activate_current(self):
        it = self.listw.currentItem()
        if not it:
            return
        label = it.text()
        self.accept()
        for lbl, _kw, _ic, fn in self._actions:
            if lbl == label:
                fn()
                return
