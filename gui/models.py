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


from gui.theme import *

class TaskTableModel(QAbstractTableModel):
    """Model over the (already filtered) visible task list. Sorting + the
    two-line name rendering are handled by the proxy + NameDelegate; live
    progress/speed updates come through refresh_dynamic()."""
    COLS = ["File", "Size", "Status", "Speed", "Time Left", "Added"]
    SORT_ROLE = Qt.UserRole + 1
    TASK_ROLE = Qt.UserRole + 2

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tasks = []

    def set_tasks(self, tasks):
        tasks = list(tasks)
        if tasks != self.tasks:           # membership/order changed -> full reset
            self.beginResetModel()
            self.tasks = tasks
            self.endResetModel()

    def refresh_dynamic(self):
        """Repaint volatile columns (progress/speed/time-left/status) in place."""
        if self.tasks:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.tasks) - 1, len(self.COLS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tasks)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLS[section]
        return None

    def _eta_secs(self, t, bps):
        if t.status == T.DOWNLOADING and bps > 0 and t.total_size > 0:
            return max(0, (t.total_size - t.downloaded) / bps)
        return 0

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self.tasks[index.row()]
        col = index.column()
        if role == self.TASK_ROLE:
            return t
        bps = self.app._bps_for(t)
        if role == Qt.DisplayRole:
            if col == 0:
                return t.filename                       # painted by NameDelegate
            if col == 1:
                return human_size(t.total_size) if t.total_size else "—"
            if col == 2:
                return t.status
            if col == 3:
                return human_speed(bps) if t.status == T.DOWNLOADING else ""
            if col == 4:
                return fmt_eta(self._eta_secs(t, bps))
            if col == 5:
                return humanize_age(t.added)
        elif role == Qt.ForegroundRole and col == 2:
            return QColor(STATUS_COLORS.get(t.status, TEXT))
        elif role == Qt.TextAlignmentRole and col in (1, 3, 4):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == self.SORT_ROLE:
            if col == 0:
                return (t.filename or "").lower()
            if col == 1:
                return t.total_size or t.downloaded
            if col == 2:
                return t.status
            if col == 3:
                return bps
            if col == 4:
                return self._eta_secs(t, bps)
            if col == 5:
                return t.added or 0
        return None


