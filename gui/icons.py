"""Small SVG icon loader with theme-aware tinting."""
import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from gui import theme

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover - PySide6 normally ships QtSvg.
    QSvgRenderer = None


_CACHE = {}
_MISSING = set()          # names already warned about, so the log stays readable


def icon_path(name):
    return theme.resource_path("assets", "icons", f"{name}.svg")


def _resolve_color(color):
    if color == "text":
        return theme.TEXT
    if color == "muted":
        return theme.MUTED
    if color == "accent":
        return theme.ACCENT
    return color


def themed_icon(name, color="text", size=18):
    """Return a tinted QIcon for one bundled SVG."""
    resolved = _resolve_color(color)
    key = (name, resolved.lower(), int(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    path = icon_path(name)
    if not os.path.exists(path):
        # An empty QIcon renders as nothing at all, so a typo'd or never-added
        # name is invisible rather than obviously broken — three buttons shipped
        # iconless that way. Say so once per name; tests assert the set is empty.
        if name not in _MISSING:
            _MISSING.add(name)
            logging.getLogger("hyperfetch.gui").warning(
                "no icon named %r in assets/icons — rendering blank", name)
        return QIcon()

    if QSvgRenderer is None:
        icon = QIcon(path)
        _CACHE[key] = icon
        return icon

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer = QSvgRenderer(path)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pix.rect(), QColor(resolved))
    painter.end()

    icon = QIcon(pix)
    _CACHE[key] = icon
    return icon
