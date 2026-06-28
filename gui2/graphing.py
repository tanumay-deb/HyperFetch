"""Shared helpers for the speed graphs (sidebar gauge, details drawer, card
sparkline) so they all render the same calm, smoothed line.

Why: multi-connection and especially HLS downloads report progress in chunky
steps (HLS bumps `downloaded` once per whole .ts segment), so the raw 500ms
samples look spiky. A short moving average + a Catmull-Rom spline turn that into
a readable throughput curve without distorting the overall shape.
"""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath


def moving_avg(values, k=5):
    """Centered moving average; returns a list the same length as `values`."""
    n = len(values)
    if k <= 1 or n < 2:
        return list(values)
    half = k // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = values[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def smooth_path(points):
    """A smooth QPainterPath through `points` (list of QPointF) using a
    Catmull-Rom spline converted to cubic Béziers. Falls back to straight
    segments for < 3 points."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    n = len(points)
    if n < 3:
        for pt in points[1:]:
            path.lineTo(pt)
        return path
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[0]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[n - 1]
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0)
        path.cubicTo(c1, c2, p2)
    return path
