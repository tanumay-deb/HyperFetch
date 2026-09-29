"""The question before a Firefox install may send downloads here (pairing.py).

Chrome and Edge pair on their own. Firefox asks, and this is the question. It
shows the code the extension shows, which is how the person tells HyperFetch's
request from any other add-on's. Every way of not saying yes - Don't allow,
closing it, Escape, a stray Enter - is a no, and a no is not asked again for a
while.
"""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from gui2.dialogs.common import DialogHeader
from gui2.palette import COLORS, fpx


class PairDialog(QDialog):
    """Allow or refuse one Firefox install, by its code."""

    def __init__(self, parent=None, code="0000"):
        super().__init__(parent)
        self.setWindowTitle("Pair Firefox with HyperFetch?")
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.setMinimumWidth(440)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(DialogHeader("Pair Firefox with HyperFetch?"))

        body = (f"color:{COLORS['text']};font-size:{fpx(13)};background:transparent;")
        ask = QLabel("HyperFetch in Firefox is asking to send downloads to this app. "
                     "Allow it only if Firefox shows this code:")
        ask.setWordWrap(True)
        ask.setStyleSheet(body)
        v.addWidget(ask)

        shown = QLabel(" ".join(code))
        shown.setStyleSheet(
            f"color:{COLORS['text']};font-size:{fpx(26)};font-weight:700;"
            f"font-family:'Cascadia Mono','Consolas',monospace;letter-spacing:4px;"
            f"background:{COLORS['surface2']};border-radius:8px;padding:10px 18px;")
        v.addWidget(shown)

        note = QLabel("Once allowed, that Firefox sends downloads here without asking "
                      "again. Not expecting this, or the codes differ? Don't allow it.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{COLORS['muted']};font-size:{fpx(12)};"
                           f"background:transparent;")
        v.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        # Enter refuses. Allowing hands over the key to this app, so it takes a
        # click on purpose, never a keypress meant for something else.
        no = QPushButton("Don't allow")
        no.setDefault(True)
        no.clicked.connect(self.reject)
        yes = QPushButton("Allow")
        yes.setObjectName("primary")
        yes.setAutoDefault(False)
        yes.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(yes)
        v.addLayout(row)


def show_next(parent, requests):
    """Ask about the next Firefox install waiting to pair, if any. Returns the
    dialog, shown without blocking so the window's refresh keeps running under
    it, or None when nobody is asking."""
    waiting = requests.next_prompt()
    if waiting is None:
        return None
    origin, code = waiting
    dlg = PairDialog(parent, code)
    dlg.accepted.connect(lambda: requests.decide(origin, True))
    dlg.rejected.connect(lambda: requests.decide(origin, False))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
