from PySide6.QtCore import Qt
from PySide6.QtCore import QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
import sys
from gui2.palette import COLORS
class DeleteDialog(QDialog):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)


    def mouseMoveEvent(self, event):
        if self._drag_active:
            self.move(
                event.globalPosition().toPoint()
                - self._drag_position
            )

        super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)
    def __init__(self, finished=8, downloading=1, parent=None):
        super().__init__(parent)

        self._drag_active = False
        self._drag_position = None
        self.setWindowTitle("Delete Downloads")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.finished = finished
        self.downloading = downloading

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        self.container = QFrame()
        self.container.setObjectName("container")
        outer.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(35)
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        # Header
        self.headerWidget = QFrame()
        header = QHBoxLayout(self.headerWidget)
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Delete Downloads")
        title.setObjectName("title")

        close = QPushButton("✕")
        close.setObjectName("closeBtn")
        close.clicked.connect(self.reject)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close)

        layout.addWidget(self.headerWidget)

        # Icon
        icon = QLabel()
        pm = QPixmap("resources/icons/trash.png")
        if not pm.isNull():
            icon.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon.setText("🗑")
            icon.setObjectName("iconFallback")

        layout.addWidget(icon, alignment=Qt.AlignHCenter)

        # Main
        main = QLabel("Delete selected downloads?")
        main.setObjectName("main")
        layout.addWidget(main)

        total = finished + downloading

        info = QLabel(f"You're about to delete {total} download{'s' if total != 1 else ''}.")
        info.setObjectName("secondary")
        layout.addWidget(info)

        stats = QLabel(
            f"Completed      {finished}\n"
            f"Downloading    {downloading}"
        )
        stats.setObjectName("stats")
        layout.addWidget(stats)

        warning = QLabel("This action cannot be undone.")
        warning.setObjectName("warning")
        layout.addWidget(warning)

        self.deleteDisk = QCheckBox("Also delete downloaded files")
        self.deleteDisk.setChecked(True)
        layout.addWidget(self.deleteDisk)

        sub = QLabel("The downloaded files will be permanently removed.")
        sub.setObjectName("sub")
        layout.addWidget(sub)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.setSpacing(12)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancel")
        cancel.setMinimumWidth(100)
        cancel.setFixedHeight(36)

        delete = QPushButton("Delete")
        delete.setObjectName("delete")
        delete.setMinimumWidth(100)
        delete.setFixedHeight(36)
        delete.setDefault(True)

        cancel.clicked.connect(self.reject)
        delete.clicked.connect(self.accept)

        buttons.addWidget(cancel)
        buttons.addWidget(delete)

        layout.addLayout(buttons)

        self.setStyleSheet(f"""
#container{{
    background:{COLORS['surface2']};
    border:1px solid {COLORS['border']};
    border-radius:16px;
}}

        QLabel{{
            color:{COLORS['text']};
        }}

        #title{{
            font-size:18px;
            font-weight:700;
        }}

        #main{{
            font-size:16px;
            font-weight:600;
        }}

        #secondary{{
            color:{COLORS['muted']};
            font-size:12px;
        }}

        #stats{{
            color:{COLORS['text']};
            font-size:13px;
            font-weight:500;
        }}

        #warning{{
            color:{COLORS['warning']};
            font-size:12px;
        }}

        #sub{{
            color:{COLORS['faint']};
            font-size:11px;
            margin-left:24px;
        }}

        #iconFallback{{
            font-size:34px;
        }}

        QPushButton{{
            border:none;
            border-radius:8px;
            padding:8px 16px;
            color:white;
            font-size:13px;
        }}

        #cancel{{
            background:{COLORS['surface']};
        }}

        #cancel:hover{{
            background:{COLORS['border']};
        }}

        #delete{{
            background:{COLORS['error']};
        }}

        #delete:hover{{
            background:#EA4335;
        }}

        #closeBtn{{
            background:transparent;
            color:{COLORS['muted']};
            min-width:28px;
            max-width:28px;
            min-height:28px;
            max-height:28px;
        }}

        #closeBtn:hover{{
            background:{COLORS['surface']};
            border-radius:6px;
        }}

        QCheckBox{{
            color:white;
            spacing:8px;
        }}

        QCheckBox::indicator{{
            width:18px;
            height:18px;
        }}

        QCheckBox::indicator:unchecked{{
            border:2px solid {COLORS['faint']};
            border-radius:4px;
            background:{COLORS['surface2']};
        }}

        QCheckBox::indicator:checked{{
            border:2px solid {COLORS['error']};
            background:{COLORS['error']};
            border-radius:4px;
        }}
        """)

        self.adjustSize()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = DeleteDialog(finished=8, downloading=1)
    if dlg.exec():
        print("Delete:", dlg.deleteDisk.isChecked())
    sys.exit()
