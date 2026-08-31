"""Settings -> Users Site: accounts, the invite code, and what they are using.

Read and written straight through ``site_auth``. This dialog is the only place
an account can be created without an invite code, which is safe because whoever
is looking at it is already sitting at the machine — the code exists to stop
strangers who found the URL, and has nothing to add here.
"""
import time

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QWidget, QScrollArea, QMessageBox, QComboBox, QSizePolicy
)
from PySide6.QtCore import Qt

import site_auth
import site_limits
from gui2.palette import COLORS, fpx
from gui.icons import themed_icon


def _human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


QUOTA_CHOICES = [
    ("500 MB", 500 * 1024 ** 2),
    ("2 GB", 2 * 1024 ** 3),
    ("5 GB", 5 * 1024 ** 3),
    ("10 GB", 10 * 1024 ** 3),
    ("50 GB", 50 * 1024 ** 3),
]


class NewUserDialog(QDialog):
    """Create an account. Validation is site_auth's, shown as it complains.

    The rules live in one place on purpose: a second copy here would drift, and
    the copy that matters is the one the public signup form also runs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New account")
        self.setMinimumWidth(360)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        def field(label, widget):
            cap = QLabel(label)
            cap.setStyleSheet(f"color:{COLORS['muted']};font-size:{fpx(11)};"
                              "font-weight:700;background:transparent;")
            v.addWidget(cap)
            v.addWidget(widget)
            return widget

        self.username = field("Username", QLineEdit())
        self.username.setPlaceholderText("letters, numbers, . _ -")
        self.email = field("Email (optional)", QLineEdit())
        self.password = field("Password", QLineEdit())
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(
            "at least %d characters" % site_auth.MIN_PASSWORD)

        self.quota = QComboBox()
        for label, _ in QUOTA_CHOICES:
            self.quota.addItem(label)
        self.quota.setCurrentIndex(1)          # 2 GB, the default
        field("Space", self.quota)

        self.err = QLabel("")
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color:{COLORS['error']};background:transparent;")
        self.err.setVisible(False)
        v.addWidget(self.err)

        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create")
        create.setObjectName("primary")
        create.clicked.connect(self._create)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(create)
        v.addLayout(row)

        self.created = None

    def _create(self):
        try:
            self.created = site_auth.create_user_as_admin(
                self.username.text(), self.email.text(), self.password.text(),
                QUOTA_CHOICES[self.quota.currentIndex()][1])
        except ValueError as e:
            self.err.setText(str(e))
            self.err.setVisible(True)
            return
        self.accept()


class SiteUsersPanel(QWidget):
    """The list of accounts, rebuilt from disk whenever anything changes.

    Rebuilding rather than patching rows: the list is short, and a panel that
    can disagree with the store is worse than one that redraws.
    """

    def __init__(self, save_dir, parent=None):
        super().__init__(parent)
        self.save_dir = save_dir
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(9)

        head = QHBoxLayout()
        self.count = QLabel("")
        self.count.setStyleSheet("font-weight:700;background:transparent;")
        add = QPushButton("  New account")
        add.setIcon(themed_icon("plus", "text"))
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._new_user)
        head.addWidget(self.count)
        head.addStretch()
        head.addWidget(add)
        v.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMinimumHeight(180)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(self.scroll, 1)

        self.reload()

    # ------------------------------------------------------------------ rows
    def reload(self):
        users = site_auth.list_users()
        self.count.setText("%d account%s" % (len(users), "" if len(users) == 1 else "s"))

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)

        if not users:
            empty = QLabel("No accounts yet. Anyone signing up needs the invite "
                           "code below.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
            lay.addWidget(empty)
        for u in users:
            lay.addWidget(self._row(u))
        lay.addStretch()
        self.scroll.setWidget(inner)

    def _row(self, u):
        f = QFrame()
        f.setObjectName("panel")
        f.setStyleSheet(f"#panel{{background:{COLORS['surface2']};"
                        f"border:1px solid {COLORS['border']};border-radius:9px;}}")
        g = QVBoxLayout(f)
        g.setContentsMargins(12, 9, 12, 9)
        g.setSpacing(6)

        top = QHBoxLayout()
        name = QLabel(u["username"])
        name.setStyleSheet(f"font-weight:800;background:transparent;color:{COLORS['text']};")
        top.addWidget(name)

        disabled = u["status"] != site_auth.STATUS_ACTIVE
        pill = QLabel("Disabled" if disabled else "Active")
        tone = COLORS["error"] if disabled else COLORS["success"]
        pill.setStyleSheet(
            f"color:{tone};background:transparent;font-size:{fpx(10)};"
            "font-weight:800;letter-spacing:.06em;")
        top.addWidget(pill)
        top.addStretch()

        used = site_limits.usage_bytes(self.save_dir, u["username"])
        usage = QLabel("%s of %s" % (_human(used), _human(u["quota"])))
        usage.setStyleSheet(f"color:{COLORS['muted']};background:transparent;"
                            f"font-size:{fpx(11)};")
        top.addWidget(usage)
        g.addLayout(top)

        if u["email"]:
            em = QLabel(u["email"])
            em.setStyleSheet(f"color:{COLORS['faint']};background:transparent;"
                             f"font-size:{fpx(11)};")
            g.addWidget(em)

        acts = QHBoxLayout()
        acts.setSpacing(6)
        for label, slot in (
            ("Password", lambda _=False, x=u: self._reset(x)),
            ("Space", lambda _=False, x=u: self._quota(x)),
            ("Enable" if disabled else "Disable", lambda _=False, x=u: self._toggle(x)),
            ("Delete", lambda _=False, x=u: self._delete(x)),
        ):
            b = QPushButton(label)
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            if label == "Delete":
                b.setStyleSheet(f"color:{COLORS['error']};")
            acts.addWidget(b)
        acts.addStretch()
        g.addLayout(acts)
        return f

    # --------------------------------------------------------------- actions
    def _new_user(self):
        d = NewUserDialog(self.window())
        if d.exec() and d.created:
            self.reload()

    def _reset(self, u):
        from PySide6.QtWidgets import QInputDialog
        pw, ok = QInputDialog.getText(
            self, "New password for %s" % u["username"],
            "At least %d characters. This signs them out everywhere."
            % site_auth.MIN_PASSWORD,
            QLineEdit.Password)
        if not ok:
            return
        try:
            site_auth.set_password(u["id"], pw)
        except ValueError as e:
            QMessageBox.warning(self, "Not changed", str(e))
            return
        QMessageBox.information(
            self, "Password changed",
            "%s has been signed out on every device." % u["username"])

    def _quota(self, u):
        from PySide6.QtWidgets import QInputDialog
        labels = [c[0] for c in QUOTA_CHOICES]
        current = next((i for i, c in enumerate(QUOTA_CHOICES)
                        if c[1] == u["quota"]), 1)
        label, ok = QInputDialog.getItem(
            self, "Space for %s" % u["username"],
            "How much of the disk this account may use:",
            labels, current, False)
        if not ok:
            return
        site_auth.set_quota(u["id"], dict(QUOTA_CHOICES)[label])
        self.reload()

    def _toggle(self, u):
        disabled = u["status"] != site_auth.STATUS_ACTIVE
        site_auth.set_status(
            u["id"],
            site_auth.STATUS_ACTIVE if disabled else site_auth.STATUS_DISABLED)
        self.reload()

    def _delete(self, u):
        used = site_limits.usage_bytes(self.save_dir, u["username"])
        # Files are deliberately kept: removing a login should not delete
        # gigabytes. Say so plainly, because the opposite is what people expect.
        answer = QMessageBox.question(
            self, "Delete %s?" % u["username"],
            "The account is removed and they can no longer sign in.\n\n"
            "Their %s of downloads stay on disk in the folder named after "
            "them, for you to deal with." % _human(used),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        site_auth.delete_user(u["id"])
        self.reload()
