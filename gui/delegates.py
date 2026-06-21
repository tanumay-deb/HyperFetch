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


class CardDelegate(QStyledItemDelegate):
    def _get_group(self, status):
        import task as T
        if status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED): return "Active"
        if status == T.PAUSED: return "Paused"
        if status == T.COMPLETED: return "Completed"
        return "Failed"

    def _is_first_in_group(self, index, t):
        if index.row() == 0: return True
        prev_index = index.sibling(index.row() - 1, index.column())
        from gui.models import TaskTableModel
        prev_t = prev_index.data(TaskTableModel.TASK_ROLE)
        if prev_t and self._get_group(prev_t.status) != self._get_group(t.status):
            return True
        return False

    def sizeHint(self, option, index):
        from gui.models import TaskTableModel
        t = index.data(TaskTableModel.TASK_ROLE)
        height = 80
        if t and self._is_first_in_group(index, t):
            height += 40
        return QSize(option.rect.width(), height)

    def paint(self, painter, option, index):
        from gui.models import TaskTableModel
        import task as T
        t = index.data(TaskTableModel.TASK_ROLE)
        if not t:
            return super().paint(painter, option, index)
            
        r = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        is_first = self._is_first_in_group(index, t)
        if is_first:
            group_name = self._get_group(t.status)
            header_rect = QRect(r.left() + 8, r.top() + 10, r.width() - 16, 20)
            painter.setPen(QColor(MUTED))
            header_f = QFont(); header_f.setPixelSize(13); header_f.setBold(True)
            painter.setFont(header_f)
            painter.drawText(header_rect, int(Qt.AlignBottom | Qt.AlignLeft), group_name)
            
            # Offset card rect by 40 pixels
            card_rect = r.adjusted(4, 44, -4, -4)
        else:
            card_rect = r.adjusted(4, 4, -4, -4)
        
        # Card Background
        if option.state & QStyle.State_Selected:
            painter.fillRect(card_rect, QColor(SEL))
        else:
            painter.fillRect(card_rect, QColor(SURFACE))
        
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(card_rect, 8, 8)
        
        # Icon — torrents get a distinct magnet glyph; else category icon
        from gui.icons import themed_icon
        import torrent as _torrent
        cat = utils.category_for(t.filename)
        icon_rect = QRect(card_rect.left() + 16, card_rect.top() + 16, 24, 24)
        ICONS = {"Compressed": "archive", "Programs": "program", "Video": "video", "Music": "music", "Documents": "document", "Other": "folder"}
        icon_name = "magnet" if _torrent.is_torrent_task(t.url, t.filename) else ICONS.get(cat, "folder")
        themed_icon(icon_name, "accent", 24).paint(painter, icon_rect, Qt.AlignCenter)
        
        # Filename
        tx = card_rect.left() + 56
        tw = max(40, card_rect.width() - 150)   # reserve right side for action buttons; never negative
        name_f = QFont(); name_f.setPixelSize(13); name_f.setBold(True)
        painter.setFont(name_f)
        painter.setPen(QColor(TEXT))
        # the magnet icon already marks torrents -> no "[Torrent]" name prefix
        display_name = t.filename or ""
        name = painter.fontMetrics().elidedText(display_name, Qt.ElideMiddle, tw)
        painter.drawText(QRect(tx, card_rect.top() + 14, tw, 20), int(Qt.AlignVCenter | Qt.AlignLeft), name)
        
        # Status text + speed
        sub_f = QFont(); sub_f.setPixelSize(11)
        painter.setFont(sub_f)
        painter.setPen(QColor(MUTED))
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        if is_tor and t.status == T.DOWNLOADING and not t.total_size:
            # magnet metadata phase: only while actually downloading + size unknown
            status_str = f"Fetching metadata… • {t.status}"
        else:
            status_str = f"{human_size(t.downloaded)} / {human_size(t.total_size)} • {t.status}"
            if t.status == T.DOWNLOADING and is_tor:
                # torrents report swarm size, not HTTP segments
                peers = getattr(t, "tor_conns", 0)
                seeds = getattr(t, "tor_seeds", 0)
                status_str += (f" • {peers} peer{'' if peers == 1 else 's'}"
                               f" • {seeds} seed{'' if seeds == 1 else 's'}")
            elif t.status == T.DOWNLOADING and not is_tor:
                # segment count is HTTP-only; show only when there are live ones
                conns = sum(1 for s in t.segments if not s.complete)
                if conns:
                    status_str += f" • {conns} connection{'s' if conns != 1 else ''}"
        painter.drawText(QRect(tx, card_rect.bottom() - 26, tw, 16), int(Qt.AlignVCenter | Qt.AlignLeft), status_str)
        
        # Progress Bar
        bar_y = card_rect.top() + 40
        barw = tw
        bar = QRect(tx, bar_y, barw, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(BG))
        painter.drawRoundedRect(bar, 2, 2)
        # a finished task is 100% even if size was never reported (torrents)
        pct = 100 if t.status == T.COMPLETED else max(0, min(100, t.percent))
        if pct > 0:
            fill = QRect(bar.left(), bar.top(), int(bar.width() * pct / 100), bar.height())
            color = ACCENT if t.status == T.DOWNLOADING else (STATUS_COLORS.get(T.PAUSED) if t.status == T.PAUSED else STATUS_COLORS.get(T.COMPLETED))
            if t.status == T.ERROR: color = STATUS_COLORS.get(T.ERROR)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(fill, 2, 2)
            
        # Action Buttons
        btn_x = card_rect.right() - 44
        btn_f = QFont()
        btn_f.setPixelSize(24)
        btn_f.setBold(True)
        painter.setFont(btn_f)
        painter.setPen(QColor(TEXT))
        
        painter.drawText(QRect(btn_x, card_rect.top(), 36, card_rect.height()), int(Qt.AlignCenter), "⋮")
        
        if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED):
            painter.drawText(QRect(btn_x - 36, card_rect.top(), 36, card_rect.height()), int(Qt.AlignCenter), "⏸")
        elif t.status in (T.PAUSED, T.ERROR):
            painter.drawText(QRect(btn_x - 36, card_rect.top(), 36, card_rect.height()), int(Qt.AlignCenter), "▶")
        
        painter.restore()

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        from gui.models import TaskTableModel
        import task as T
        if event.type() == QEvent.MouseButtonRelease:
            t = index.data(TaskTableModel.TASK_ROLE)
            if not t: return False
            
            r = option.rect
            is_first = self._is_first_in_group(index, t)
            if is_first:
                card_rect = r.adjusted(4, 44, -4, -4)
            else:
                card_rect = r.adjusted(4, 4, -4, -4)
                
            btn_x = card_rect.right() - 44
            
            menu_rect = QRect(btn_x, card_rect.top(), 36, card_rect.height())
            action_rect = QRect(btn_x - 36, card_rect.top(), 36, card_rect.height())
            
            pos = event.pos()
            main_window = self.parent().window() if self.parent() else None
            if not main_window or not hasattr(main_window, "queue"): return False
            
            sm = main_window.list_view.selectionModel()
            from PySide6.QtCore import QItemSelectionModel
            sm.select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            
            if menu_rect.contains(pos):
                main_window._on_context_menu(pos)
                return True
                
            if action_rect.contains(pos):
                if t.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED):
                    main_window.on_pause()
                elif t.status in (T.PAUSED, T.ERROR):
                    main_window.on_resume()
                return True
                
        return super().editorEvent(event, model, option, index)
class SpeedGraphWidget(QWidget):
    def __init__(self, parent=None, max_points=120):
        super().__init__(parent)
        self.max_points = max_points
        self.data = [0.0] * max_points
        self._ema = 0.0      # smoothed sample (the spiky 0.5s reads are noisy)
        self._peak = 1.0     # decaying Y-scale so one spike can't flatten the rest
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TranslucentBackground)

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

class CircularSpeedGraphWidget(QWidget):
    def __init__(self, parent=None, max_points=60):
        super().__init__(parent)
        self.max_points = max_points
        self.data = [0.0] * max_points
        self._ema = 0.0
        self._peak = 1.0
        self.setFixedSize(90, 90)
        # transparent so the rounded card (SURFACE_2) shows through instead of
        # the global QWidget BG painting a dark square behind the gauge
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TranslucentBackground)

    def add_value(self, val):
        self._ema = val if self._ema <= 0 else 0.3 * val + 0.7 * self._ema
        self.data.append(self._ema)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        self._peak = max(self._ema, self._peak * 0.94)
        self.update()

    def current(self):
        return self._ema

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        
        # Draw Circular Gauge
        rect = QRect(8, 8, w - 16, h - 16)
        
        # startAngle and spanAngle in 1/16th of a degree
        # Start at bottom-left (-225 deg from 3 o'clock positive counter-clockwise) -> actually in Qt, 0 is 3 o'clock, + is CCW.
        # Bottom left is ~225 degrees. Span is -270 degrees (clockwise)
        startAngle = 225 * 16
        spanAngle = -270 * 16
        
        # Background arc
        painter.setPen(QPen(QColor(SURFACE), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, startAngle, spanAngle)
        
        # Foreground arc (speed)
        max_val = self._peak * 1.15
        if max_val <= 0: max_val = 1
        ratio = min(1.0, max(0.0, self._ema / max_val))
        
        painter.setPen(QPen(QColor(ACCENT), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, startAngle, spanAngle * ratio)
        # ring only — the inner line graph overflowed the gauge and was redundant

class SidebarItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        key = index.data(Qt.UserRole)
        if not key:
            collapsed = self.parent().property("collapsed")
            if collapsed: return QSize(option.rect.width(), 0) # hide headers when collapsed
            return QSize(option.rect.width(), 34) # header
        return QSize(option.rect.width(), 36) # item, tighter spacing

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = option.rect
        key = index.data(Qt.UserRole)
        is_selected = option.state & QStyle.State_Selected
        is_hover = option.state & QStyle.State_MouseOver
        collapsed = self.parent().property("collapsed")
        
        if not key:
            if collapsed:
                painter.restore()
                return
            # Render Header
            painter.setPen(QColor(MUTED))
            f = QFont()
            f.setPixelSize(11)
            f.setBold(True)
            painter.setFont(f)
            text = index.data(Qt.DisplayRole)
            painter.drawText(QRect(rect.left() + 10, rect.top() + 10, rect.width() - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.restore()
            return

        # Render Item
        bg_rect = QRect(rect.left(), rect.top(), rect.width(), rect.height() - 2)
        
        # When collapsed, make hover/select look like a small square around the icon
        if collapsed:
            bg_rect = QRect(rect.left() + 8, rect.top(), 32, 32)
            
        if is_selected:
            painter.setBrush(QColor(SURFACE_2))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bg_rect, 6, 6)
            
            # Left accent bar
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRect(bg_rect.left(), bg_rect.top() + 4, 4, bg_rect.height() - 8), 2, 2)
        elif is_hover:
            painter.setBrush(QColor(HOVER))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bg_rect, 6, 6)
            
        # Icon
        icon_path, icon_color, label = index.data(Qt.UserRole + 3)
        count = index.data(Qt.UserRole + 4) or 0
        
        # Draw Icon centered in its 32x32 bounding area
        painter.setPen(QColor(icon_color) if icon_color else QColor(MUTED))
        icon_f = QFont()
        icon_f.setPixelSize(16)
        painter.setFont(icon_f)
        
        if collapsed:
            painter.drawText(bg_rect, Qt.AlignCenter, icon_path)
        else:
            painter.drawText(QRect(bg_rect.left() + 14, bg_rect.top(), 24, bg_rect.height()), Qt.AlignCenter, icon_path)
        
        # If collapsed, don't draw text or counts
        if collapsed:
            painter.restore()
            return
            
        # Draw Label
        painter.setPen(QColor(TEXT) if is_selected else QColor("#D0D0D0"))
        lbl_f = QFont()
        lbl_f.setPixelSize(13)
        lbl_f.setBold(bool(is_selected))
        painter.setFont(lbl_f)
        painter.drawText(QRect(bg_rect.left() + 46, bg_rect.top(), 150, bg_rect.height()), Qt.AlignLeft | Qt.AlignVCenter, label)
        
        # Draw Count
        if count > 0 or key == "All":
            painter.setPen(QColor(MUTED))
            count_str = f"({count})" if key == "All" else f"({count})"
            count_f = QFont()
            count_f.setPixelSize(12)
            painter.setFont(count_f)
            
            # Draw beside label
            fm = painter.fontMetrics()
            lbl_w = fm.horizontalAdvance(label)
            painter.drawText(QRect(bg_rect.left() + 46 + lbl_w + 6, bg_rect.top(), 150, bg_rect.height()), Qt.AlignLeft | Qt.AlignVCenter, count_str)
            
        painter.restore()
