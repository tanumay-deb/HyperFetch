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
from gui.icons import themed_icon
from gui.models import TaskTableModel

class NameDelegate(QStyledItemDelegate):
    """File cell: category icon + filename, with a thin progress bar (active) or
    the category name (idle) on the second line — the ABDM-style two-line cell."""
    ICONS = {
        "Compressed": "archive",
        "Programs": "program",
        "Video": "video",
        "Music": "music",
        "Documents": "document",
        "Other": "folder",
    }

    def sizeHint(self, option, index):
        return QSize(240, 48)

    def paint(self, painter, option, index):
        t = index.data(TaskTableModel.TASK_ROLE)
        if t is None:
            return super().paint(painter, option, index)
        r = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # row background (delegate owns the whole cell, so paint it ourselves)
        if option.state & QStyle.State_Selected:
            painter.fillRect(r, QColor(SEL))
        else:
            painter.fillRect(r, QColor(ALT if index.row() % 2 else SURFACE))

        cat = utils.category_for(t.filename)
        # icon
        icon_rect = QRect(r.left() + 12, r.top() + 14, 20, 20)
        themed_icon(self.ICONS.get(cat, "folder"), "text", 20).paint(
            painter, icon_rect, Qt.AlignCenter)

        tx = r.left() + 44
        tw = max(40, r.right() - tx - 10)

        # line 1 — filename
        name_f = QFont(); name_f.setPointSize(9); name_f.setBold(True)
        painter.setFont(name_f)
        painter.setPen(QColor(TEXT))
        
        import torrent
        display_name = t.filename or ""
        if torrent.is_torrent_task(t.url, t.filename):
            display_name = "[Torrent] " + display_name
            
        name = painter.fontMetrics().elidedText(display_name, Qt.ElideMiddle, tw)
        painter.drawText(QRect(tx, r.top() + 5, tw, 17),
                         int(Qt.AlignVCenter | Qt.AlignLeft), name)

        # line 2 — progress bar (active) or category
        sub_top = r.top() + 25
        if t.status in (T.DOWNLOADING, T.PAUSED) and t.total_size > 0:
            pct = max(0, min(100, t.percent))
            barw = max(20, tw - 48)
            bar = QRect(tx, sub_top + 4, barw, 6)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG))
            painter.drawRoundedRect(bar, 3, 3)
            if pct > 0:
                fill = QRect(bar.left(), bar.top(), int(bar.width() * pct / 100), bar.height())
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(fill, 3, 3)
            painter.setPen(QColor(MUTED))
            small = QFont(); small.setPointSize(8); painter.setFont(small)
            painter.drawText(QRect(bar.right() + 8, sub_top, 44, 16),
                             int(Qt.AlignVCenter | Qt.AlignLeft), f"{pct}%")
        else:
            painter.setPen(QColor(MUTED))
            sub_f = QFont(); sub_f.setPointSize(8); painter.setFont(sub_f)
            painter.drawText(QRect(tx, sub_top, tw, 16),
                             int(Qt.AlignVCenter | Qt.AlignLeft), cat)
        painter.restore()


class SpeedGraphWidget(QWidget):
    def __init__(self, parent=None, max_points=120):
        super().__init__(parent)
        self.max_points = max_points
        self.data = [0.0] * max_points
        self._ema = 0.0      # smoothed sample (the spiky 0.5s reads are noisy)
        self._peak = 1.0     # decaying Y-scale so one spike can't flatten the rest
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_value(self, val):
        # smooth toward sustained throughput rather than plotting each raw burst
        self._ema = val if self._ema <= 0 else 0.3 * val + 0.7 * self._ema
        self.data.append(self._ema)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        # peak decays ~6%/tick: the axis follows real sustained speed, and a lone
        # spike relaxes out instead of permanently squashing the baseline.
        self._peak = max(self._ema, self._peak * 0.94)
        self.update()

    def current(self):
        """Smoothed current speed (B/s) — used for the readout so it doesn't flicker."""
        return self._ema

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        max_val = self._peak * 1.15  # decaying peak + headroom (not the raw max)
        if max_val <= 0:
            max_val = 1

        path = QPainterPath()
        dx = w / (self.max_points - 1)
        
        for i, val in enumerate(self.data):
            x = i * dx
            y = h - (val / max_val) * h
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, 0, 0, h)
        c = QColor(ACCENT)
        gradient.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 100))
        gradient.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        
        painter.fillPath(fill_path, QBrush(gradient))

        pen = QPen(c)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)

# ====================================================================== dialogs
