"""HyperFetch - IDM-style GUI.

Multi-segment downloads with a live queue, pause/resume/cancel, IDM-style
file-info dialogs, persistent state, and an embedded Flask server so the
browser extension feeds downloads into this same window.
"""
import os
import sys
import time
import threading
import subprocess
from collections import deque

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QDialog,
    QLineEdit, QSpinBox, QFileDialog, QMessageBox, QAbstractItemView,
    QFrame, QButtonGroup, QGridLayout, QSplitter, QSizePolicy, QComboBox, QMenu, QInputDialog,
    QCheckBox, QListWidget, QListWidgetItem, QTableView, QStyledItemDelegate, QStyle
)
from PySide6.QtCore import (
    Qt, QTimer, QModelIndex, QAbstractTableModel, QSortFilterProxyModel, QRect, QSize
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QBrush, QPen, QLinearGradient

import task as T
import utils
import crash_reporter
import updater
from queue_manager import QueueManager
from api_server import run_server, PORT


from gui.main_window import DownloadApp, _self_test
from gui.theme import apply_theme, APP_VERSION

if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"HyperFetch {APP_VERSION}")
        sys.exit(0)
    if "--selftest" in sys.argv:
        _self_test()
        sys.exit(0)

    # install BEFORE QApplication so a Qt construction crash is captured too
    crash_reporter.install(APP_VERSION)

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = DownloadApp()
    win.show()
    sys.exit(app.exec())
