"""Circular speed gauge with an inner sparkline — a self-contained QWidget.

Reads colours from palette.COLORS at paint time (no stale copies). Feed it
samples with push(bytes_per_sec); it keeps a short rolling history.
"""
from collections import deque

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath

from gui2.palette import COLORS
from gui2.graphing import moving_avg, smooth_path

_SPAN = 270.0          # arc sweep in degrees (open at the bottom)
_START = 225.0         # start angle (Qt: 0=3 o'clock, CCW positive)


class CircularSpeedGauge(QWidget):
    def __init__(self, parent=None, history=48):
        super().__init__(parent)
        self._hist = deque([0.0] * history, maxlen=history)
        self._max = 1.0
        self.setMinimumSize(96, 96)

    def push(self, bps):
        bps = max(0.0, float(bps))
        self._hist.append(bps)
        self._max = max(1.0, max(self._hist))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = COLORS
        rect = self.rect()
        side = min(rect.width(), rect.height()) - 12
        x = rect.center().x() - side / 2
        y = rect.center().y() - side / 2
        arc = QRectF(x, y, side, side)

        # background track
        pen = QPen(QColor(c["border2"]), 7, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(arc, int(-_START * 16), int(-_SPAN * 16))

        # accent arc proportional to current speed vs rolling max
        cur = self._hist[-1] if self._hist else 0.0
        frac = min(1.0, cur / self._max) if self._max else 0.0
        pen.setColor(QColor(c["accent"]))
        p.setPen(pen)
        p.drawArc(arc, int(-_START * 16), int(-_SPAN * frac * 16))

        # inner sparkline of recent history
        n = len(self._hist)
        if n >= 2:
            inset = side * 0.26
            spark = QRectF(x + inset, rect.center().y() - side * 0.10,
                           side - 2 * inset, side * 0.32)
            vals = moving_avg(list(self._hist), 5)
            pts = [QPointF(spark.left() + (i / (n - 1)) * spark.width(),
                           spark.bottom() - (v / self._max) * spark.height())
                   for i, v in enumerate(vals)]
            path = smooth_path(pts)
            sp = QPen(QColor(c["accent2"]), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(sp)
            p.drawPath(path)
        p.end()


class SpeedGraph(QWidget):
    """A flat line graph of recent speed (replaces the round gauge in the
    sidebar). push(bytes_per_sec) each tick; keeps a rolling history."""
    def __init__(self, parent=None, history=64):
        super().__init__(parent)
        self._hist = deque([0.0] * history, maxlen=history)
        self._max = 1.0
        self.setMinimumHeight(46)

    def push(self, bps):
        self._hist.append(max(0.0, float(bps)))
        self._max = max(1.0, max(self._hist))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = COLORS
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(c["border"]), 1))
        for i in range(1, 3):                       # faint gridlines
            y = h * i / 3
            p.drawLine(0, int(y), w, int(y))
        n = len(self._hist)
        if n >= 2:
            vals = moving_avg(list(self._hist), 5)
            pts = [QPointF(w * i / (n - 1), h - (v / self._max) * (h - 6) - 3)
                   for i, v in enumerate(vals)]
            line = smooth_path(pts)
            fill = QPainterPath(line)
            fill.lineTo(pts[-1].x(), h); fill.lineTo(pts[0].x(), h); fill.closeSubpath()
            fc = QColor(c["accent"])
            fc.setAlpha(40)
            p.fillPath(fill, fc)
            p.setPen(QPen(QColor(c["accent"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(line)
        p.end()
