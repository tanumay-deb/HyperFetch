"""Queue Manager dialog (v2, mockup #12) — create / delete queues and set each
queue's concurrency. Mutates the shared QueueManager directly.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton,
    QFrame, QWidget
)
from PySide6.QtCore import Qt

from gui2.palette import COLORS


class QueueManagerDialog(QDialog):
    def __init__(self, parent, queue):
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle("Queues")
        self.setMinimumWidth(460)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)
        title = QLabel("Queue Manager"); title.setObjectName("dlgTitle")
        sub = QLabel("Create queues and set how many downloads each runs at once.")
        sub.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        v.addWidget(title); v.addWidget(sub)

        self._rows = QVBoxLayout(); self._rows.setSpacing(8)
        v.addLayout(self._rows)
        self._rebuild()

        # add-queue row
        add = QFrame(); add.setObjectName("panel")
        ah = QHBoxLayout(add); ah.setContentsMargins(12, 10, 12, 10); ah.setSpacing(8)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("New queue name")
        self.conc_edit = QSpinBox(); self.conc_edit.setRange(1, 16); self.conc_edit.setValue(3); self.conc_edit.setFixedWidth(64)
        add_btn = QPushButton("＋ Add"); add_btn.setObjectName("primary"); add_btn.clicked.connect(self._add)
        self.name_edit.returnPressed.connect(self._add)
        ah.addWidget(self.name_edit, 1); ah.addWidget(QLabel("slots")); ah.addWidget(self.conc_edit); ah.addWidget(add_btn)
        v.addWidget(add)

        foot = QHBoxLayout(); foot.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        foot.addWidget(close)
        v.addLayout(foot)

    def _rebuild(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for q in self.queue.queues.values():
            self._rows.addWidget(self._row(q))

    def _row(self, q):
        f = QFrame(); f.setObjectName("panel")
        h = QHBoxLayout(f); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        name = QLabel(q.name); name.setStyleSheet("font-weight:700;background:transparent;")
        active = QLabel(f"{getattr(q, 'active', 0)} active")
        active.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
        spin = QSpinBox(); spin.setRange(1, 16); spin.setValue(getattr(q, "max_concurrent", 3)); spin.setFixedWidth(64)
        spin.valueChanged.connect(lambda n, name=q.name: self.queue.set_max_concurrent(name, n))
        h.addWidget(name, 1); h.addWidget(active); h.addWidget(QLabel("slots")); h.addWidget(spin)
        if q.name != "Main":
            dele = QPushButton("🗑"); dele.setObjectName("iconbtn"); dele.setFixedSize(30, 28)
            dele.setToolTip("Delete queue (its tasks move to Main)")
            dele.clicked.connect(lambda _=False, name=q.name: self._del(name))
            h.addWidget(dele)
        return f

    def _add(self):
        name = self.name_edit.text().strip()
        if name and self.queue.add_queue(name, self.conc_edit.value()):
            self.name_edit.clear()
            self._rebuild()

    def _del(self, name):
        self.queue.delete_queue(name)
        self._rebuild()
