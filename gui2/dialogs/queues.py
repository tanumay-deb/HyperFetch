"""Queue Manager dialog — create / delete queues and set each one's concurrency.

Mutates the shared QueueManager directly.

The hard part of this screen is not the controls, it is the idea: a queue is a
set of N slots, and downloads wait until one frees up. The old layout put a bare
spinbox next to the word "slots" and listed every task in identical grey text,
so nothing on screen told you how full a queue was, which downloads were moving,
or what the number would change. The rewrite leads with a slot meter, says
"Runs N at a time" instead of "slots", and colours each task by state so the
list reads at a glance.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton,
    QFrame, QWidget, QScrollArea, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt

import task as T
from gui2.palette import COLORS, DIALOG_MARGIN, fpx
from gui2.dialogs.common import DialogHeader
from gui.icons import themed_icon
from gui.theme import human_size

_STATE_COLOR = {
    T.DOWNLOADING: COLORS["accent"], T.PAUSED: COLORS["warning"],
    T.ERROR: COLORS["error"], T.QUEUED: COLORS["muted"],
    T.SCHEDULED: COLORS["info"], T.COMPLETED: COLORS["success"],
    T.CANCELLED: COLORS["muted"],
}
BUSY = (T.DOWNLOADING, T.QUEUED, T.SCHEDULED)


class SlotMeter(QWidget):
    """One block per slot, filled for each download currently running.

    This is the whole concept in one widget: how many can run at once, and how
    many are running now. A number alone ("3") never conveyed that.
    """

    def __init__(self, used=0, total=1, parent=None):
        super().__init__(parent)
        self._used, self._total = used, total
        self.setFixedHeight(10)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, used, total):
        self._used, self._total = used, max(1, total)
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = max(1, self._total)
        gap = 3
        # cap the drawn blocks so a queue set to 16 does not become a smear
        shown = min(total, 16)
        w = (self.width() - gap * (shown - 1)) / shown
        for i in range(shown):
            x = i * (w + gap)
            filled = i < self._used
            # An empty slot has to READ as empty. surface2 sits almost on top of
            # the panel colour, so "2 of 3" looked identical to "2 of 2" — the
            # one thing the meter exists to show.
            p.setBrush(QColor(COLORS["accent"] if filled else COLORS["border2"]))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x), 0, max(2, int(w)), self.height(), 3, 3)
        p.end()


class QueueManagerDialog(QDialog):
    def __init__(self, parent, queue):
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle("Queues")
        self.setMinimumSize(560, 520)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        v = QVBoxLayout(self)
        v.setContentsMargins(*DIALOG_MARGIN)
        v.setSpacing(12)
        v.addWidget(DialogHeader("Queue Manager"))

        sub = QLabel("Each queue runs a set number of downloads at once. "
                     "The rest wait their turn.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{COLORS['muted']};font-size:{fpx(12)};"
                          f"background:transparent;")
        v.addWidget(sub)

        # queues scroll: a queue with thirty downloads must not push the Add row
        # and Close button off the bottom of the dialog
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self._rows = QVBoxLayout(inner)
        self._rows.setContentsMargins(0, 0, 6, 0)
        self._rows.setSpacing(10)
        self._rows.addStretch()
        self._scroll.setWidget(inner)
        v.addWidget(self._scroll, 1)
        self._rebuild()

        # ---- add a queue ----
        add = QFrame(); add.setObjectName("panel")
        ah = QHBoxLayout(add)
        ah.setContentsMargins(12, 10, 12, 10)
        ah.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("New queue name")
        self.conc_edit = QSpinBox()
        self.conc_edit.setRange(1, 16)
        self.conc_edit.setValue(3)
        self.conc_edit.setFixedWidth(56)
        add_btn = QPushButton("  Add")
        add_btn.setIcon(themed_icon("plus", "white"))
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add)
        self.name_edit.returnPressed.connect(self._add)
        ah.addWidget(self.name_edit, 1)
        ah.addWidget(self._muted("running"))
        ah.addWidget(self.conc_edit)
        ah.addWidget(self._muted("at a time"))
        ah.addWidget(add_btn)
        v.addWidget(add)

        foot = QHBoxLayout()
        self.total_lbl = self._muted("")
        foot.addWidget(self.total_lbl)
        foot.addStretch()
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        v.addLayout(foot)
        self._update_total()

    # ------------------------------------------------------------- helpers
    def _muted(self, text, size=12):
        l = QLabel(text)
        l.setStyleSheet(f"color:{COLORS['muted']};font-size:{fpx(size)};"
                        f"background:transparent;")
        return l

    def _tasks_in(self, name):
        return [t for t in self.queue.tasks
                if getattr(t, "queue_name", "Main") == name]

    def _update_total(self):
        n = len(self.queue.queues)
        running = sum(getattr(q, "active", 0) for q in self.queue.queues.values())
        self.total_lbl.setText(
            f"{n} queue{'' if n == 1 else 's'} · {running} running")

    # -------------------------------------------------------------- render
    def _rebuild(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for q in self.queue.queues.values():
            self._rows.addWidget(self._row(q))
        self._rows.addStretch()

    def _row(self, q):
        f = QFrame(); f.setObjectName("panel")
        outer = QVBoxLayout(f)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        tasks = self._tasks_in(q.name)
        limit = int(getattr(q, "max_concurrent", 3))
        # Count what is actually running rather than trusting q.active: this
        # dialog is a snapshot, and a stale counter here would misreport the one
        # thing the screen exists to explain.
        running = sum(1 for t in tasks if t.status == T.DOWNLOADING)

        # ---- title row ----
        head = QHBoxLayout(); head.setSpacing(10)
        name = QLabel(q.name)
        name.setStyleSheet(f"font-weight:800;font-size:{fpx(14)};"
                           f"background:transparent;color:{COLORS['text']};")
        head.addWidget(name)
        head.addStretch()
        head.addWidget(self._muted(f"{running} of {limit} running", 11))
        outer.addLayout(head)

        meter = SlotMeter(running, limit)
        meter.setToolTip(f"{running} of {limit} slots in use")
        outer.addWidget(meter)

        # ---- controls ----
        ctl = QHBoxLayout(); ctl.setSpacing(8)
        ctl.addWidget(self._muted("Runs", 12))
        spin = QSpinBox()
        spin.setRange(1, 16)
        spin.setValue(limit)
        spin.setFixedWidth(56)
        spin.setToolTip("How many downloads this queue runs at the same time")
        spin.valueChanged.connect(
            lambda val, n=q.name: self._set_limit(n, val))
        ctl.addWidget(spin)
        ctl.addWidget(self._muted("at a time", 12))
        ctl.addStretch()
        ctl.addWidget(self._summary(tasks))
        if q.name != "Main":
            dele = QPushButton()
            dele.setIcon(themed_icon("trash", "muted"))
            dele.setObjectName("iconbtn")
            dele.setFixedSize(30, 28)
            dele.setToolTip("Delete this queue")
            dele.clicked.connect(lambda _=False, n=q.name: self._del(n))
            ctl.addWidget(dele)
        else:
            # say why it cannot go, instead of just omitting the button and
            # leaving the row looking inconsistent
            lock = self._muted("default", 11)
            lock.setToolTip("Main is the default queue and cannot be deleted")
            ctl.addWidget(lock)
        outer.addLayout(ctl)

        # ---- the downloads themselves ----
        if not tasks:
            empty = self._muted("Nothing in this queue yet.", 11)
            outer.addWidget(empty)
            return f
        for t in tasks:
            outer.addWidget(self._task_line(t))
        return f

    def _summary(self, tasks):
        """"2 downloading · 1 waiting · 1 done" — only the states present."""
        order = [(T.DOWNLOADING, "downloading"), (T.QUEUED, "waiting"),
                 (T.PAUSED, "paused"), (T.ERROR, "failed"),
                 (T.COMPLETED, "done")]
        bits = []
        for state, word in order:
            n = sum(1 for t in tasks if t.status == state)
            if n:
                bits.append(f"{n} {word}")
        return self._muted(" · ".join(bits) or "empty", 11)

    def _task_line(self, t):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(8)
        col = _STATE_COLOR.get(t.status, COLORS["muted"])

        dot = QLabel("●")
        dot.setFixedWidth(10)
        dot.setStyleSheet(f"color:{col};font-size:{fpx(11)};background:transparent;")
        h.addWidget(dot)

        name = QLabel(t.filename or "download")
        name.setToolTip(t.filename or "")
        name.setStyleSheet(f"color:{COLORS['text']};font-size:{fpx(11)};"
                           f"background:transparent;")
        # let long names shrink rather than widening the dialog
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(name, 1)

        if t.status == T.DOWNLOADING and t.total_size:
            detail = f"{t.percent}%  ·  {human_size(t.total_size)}"
        elif t.status == T.COMPLETED:
            detail = human_size(t.total_size or t.downloaded)
        else:
            detail = str(t.status)
        right = QLabel(detail)
        right.setStyleSheet(f"color:{col};font-size:{fpx(11)};font-weight:600;"
                            f"background:transparent;")
        h.addWidget(right)
        return w

    # -------------------------------------------------------------- actions
    def _set_limit(self, name, val):
        self.queue.set_max_concurrent(name, val)
        self._rebuild()          # the meter and "N of M" must follow the change
        self._update_total()

    def _add(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        if not self.queue.add_queue(name, self.conc_edit.value()):
            QMessageBox.information(self, "Queue exists",
                                    f"There is already a queue called “{name}”.")
            return
        self.name_edit.clear()
        self._rebuild()
        self._update_total()

    def _del(self, name):
        n = len(self._tasks_in(name))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Delete queue")
        box.setText(f"Delete the “{name}” queue?")
        # deleting a queue moves downloads rather than removing them; saying so
        # up front is the difference between a safe click and a scary one
        box.setInformativeText(
            f"Its {n} download{'' if n == 1 else 's'} will move to Main."
            if n else "It has no downloads in it.")
        keep = box.addButton("Keep", QMessageBox.RejectRole)
        box.addButton("Delete", QMessageBox.DestructiveRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is keep:
            return
        self.queue.delete_queue(name)
        self._rebuild()
        self._update_total()
