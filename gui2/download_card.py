"""DownloadCardWidget — one real QWidget per download (not a paint delegate).

Real child widgets give native buttons, hover/focus, selection and layout, which
removes the whole class of v1 paint-delegate bugs (hand-computed rects, overflow,
collapsed-sidebar mess, fragile hit-testing). Tens of rows, so cost is irrelevant.
"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QProgressBar, QSizePolicy,
    QCheckBox, QWidget
)
from PySide6.QtCore import Qt, Signal, QMimeData, QUrl, QPointF
from PySide6.QtGui import (
    QColor, QPainter, QPen, QPainterPath, QLinearGradient, QDrag
)

import os

import task as T
import utils
import torrent as _torrent
from gui.theme import human_size, human_speed, fmt_eta, humanize_age
from gui.icons import themed_icon
from gui2.palette import COLORS
from gui2.graphing import moving_avg, smooth_path

_CAT_ICON = {
    "Video": ("video", "#FF80AB"), "Music": ("music", "#FF8A80"), "Compressed": ("archive", "#B388FF"),
    "Programs": ("program", "#82B1FF"), "Documents": ("document", "#80D8FF"),
    "Images": ("image", "#4DD0E1"), "Other": ("folder", "#B5B5B5"),
}
# progress-bar chunk colour by state (fast visual scanning)
_BAR_COLOR = {
    T.DOWNLOADING: COLORS["accent"], T.PAUSED: COLORS["warning"],
    T.ERROR: COLORS["error"], T.QUEUED: COLORS["muted"],
    T.SCHEDULED: COLORS["info"], T.COMPLETED: COLORS["success"],
}


def _icon_for(t):
    if _torrent.is_torrent_task(t.url, t.filename):
        return ("magnet", "#B388FF")
    return _CAT_ICON.get(utils.category_for(t.filename), ("folder", "#B5B5B5"))


class ElideLabel(QLabel):
    """A single-line label that elides its text with '…' to the width it's given,
    so a long filename never forces the card wider than the list (which used to
    push the pause/more buttons off-screen). Keeps the full text as a tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._elide()

    def setText(self, text):
        if text != self._full:
            self._full = text
            self.setToolTip(text)
            self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))


class Sparkline(QWidget):
    """Tiny live speed graph drawn on a downloading card — a filled area + line
    of the last ~40 speed samples, scaled to its own peak. Makes a download feel
    alive at a glance (steady = filled, stalling = collapsing)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(66, 22)
        self._data = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # never eat card clicks/drag

    def set_data(self, data):
        self._data = [float(x) for x in data][-40:]
        self.update()

    def paintEvent(self, _e):
        if len(self._data) < 2:
            return
        w, h = self.width(), self.height()
        vals = moving_avg(self._data, 5)
        peak = max(vals) or 1.0
        n = len(vals)
        step = w / (n - 1)
        pts = [QPointF(i * step, h - 2 - (v / peak) * (h - 4)) for i, v in enumerate(vals)]

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        line = smooth_path(pts)
        area = QPainterPath(line)
        area.lineTo(pts[-1].x(), h); area.lineTo(0, h); area.closeSubpath()

        accent = QColor(COLORS["accent"])
        grad = QLinearGradient(0, 0, 0, h)
        c0 = QColor(accent); c0.setAlpha(90); c1 = QColor(accent); c1.setAlpha(0)
        grad.setColorAt(0, c0); grad.setColorAt(1, c1)
        p.fillPath(area, grad)
        p.setPen(QPen(accent, 1.6)); p.drawPath(line)
        p.end()


class DownloadCardWidget(QFrame):
    action = Signal(str, str)            # (action, task_id): pause/resume/open/folder/more/details
    selectRequested = Signal(str, str)   # (task_id, mode): single/toggle/range

    def __init__(self, task, sl_no=0, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.task_id = task.id
        self._selected = False
        self._press_pos = None
        self._drag_url = task.url or ""
        self._drag_file = ""
        self.setFocusPolicy(Qt.StrongFocus)      # so list-scoped shortcuts work
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 9, 12, 9)        # tighter vertical padding -> more rows on screen
        root.setSpacing(13)

        # checkbox
        self.chk = QCheckBox()
        self.chk.setStyleSheet(
            f"QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; "
            f"border: 1px solid {COLORS['border']}; }}")
        root.addWidget(self.chk, 0, Qt.AlignVCenter)

        # serial number
        self.sl_lbl = QLabel(f"#{sl_no}")
        self.sl_lbl.setFixedWidth(30)
        self.sl_lbl.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px; font-weight: 700; background: transparent;")
        root.addWidget(self.sl_lbl, 0, Qt.AlignVCenter)

        ic_name, ic_color = _icon_for(task)
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setPixmap(themed_icon(ic_name, "white").pixmap(22, 22))
        self.icon.setStyleSheet(f"background: {ic_color}; border-radius: 8px;")
        root.addWidget(self.icon, 0, Qt.AlignVCenter)

        mid = QVBoxLayout(); mid.setSpacing(4)
        namerow = QHBoxLayout(); namerow.setSpacing(8)
        self.name = ElideLabel(task.filename or "download")
        self.name.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {COLORS['text']}; background: transparent;")
        self.pct = QLabel("")
        self.pct.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLORS['muted']}; background: transparent;")
        # queue badge — only shown when the task isn't in the default "Main" queue,
        # so multi-queue membership is visible at a glance
        self.qbadge = QLabel("")
        self.qbadge.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {COLORS['accent2']}; "
            f"background: {COLORS['surface2']}; border-radius: 6px; padding: 1px 7px;")
        self.qbadge.setVisible(False)
        self.spark = Sparkline()
        self.spark.setVisible(False)
        namerow.addWidget(self.name, 1)
        namerow.addWidget(self.qbadge, 0)
        namerow.addWidget(self.spark, 0, Qt.AlignVCenter)
        namerow.addWidget(self.pct, 0, Qt.AlignRight)
        mid.addLayout(namerow)

        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setRange(0, 100)
        mid.addWidget(self.bar)

        self.sub = ElideLabel("")
        self.sub.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        mid.addWidget(self.sub)
        root.addLayout(mid, 1)

        actions = QHBoxLayout(); actions.setSpacing(4)
        self.btn_primary = self._iconbtn("pause", "pause")
        self.btn_more = self._iconbtn("more", "more")
        actions.addWidget(self.btn_primary); actions.addWidget(self.btn_more)
        root.addLayout(actions)

        self.update_task(task, 0.0)

    def _iconbtn(self, icon_name, action):
        b = QPushButton(); b.setObjectName("iconbtn"); b.setFixedSize(36, 26)
        b.setIcon(themed_icon(icon_name, "text"))
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"background: {COLORS['surface2']}; border: 1px solid {COLORS['border']}; border-radius: 13px;")
        is_primary = action == "pause"
        b.clicked.connect(
            lambda: self.action.emit(self._primary_action if is_primary else action, self.task_id))
        return b

    # ---- selection / mouse ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setFocus()
            self._press_pos = e.position().toPoint()
            mods = e.modifiers()
            mode = ("toggle" if mods & Qt.ControlModifier
                    else "range" if mods & Qt.ShiftModifier else "single")
            self.selectRequested.emit(self.task_id, mode)
            e.accept()            # consume so a card click isn't seen as a blank-space click
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # past a small threshold with the button held -> drag the download OUT:
        # a finished file drops into Explorer/apps; any card drops its URL as text.
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return super().mouseMoveEvent(e)
        if (e.position().toPoint() - self._press_pos).manhattanLength() < 12:
            return
        mime = QMimeData()
        if self._drag_url:
            mime.setText(self._drag_url)
        urls = []
        if self._drag_file:
            urls.append(QUrl.fromLocalFile(self._drag_file))
        if urls:
            mime.setUrls(urls)
        if not mime.hasText() and not mime.hasUrls():
            return
        drag = QDrag(self); drag.setMimeData(mime)
        drag.setPixmap(self.grab().scaledToWidth(220, Qt.SmoothTransformation))
        self._press_pos = None
        drag.exec(Qt.CopyAction)

    def mouseDoubleClickEvent(self, _e):
        self.action.emit("details", self.task_id)

    def contextMenuEvent(self, _e):
        self.action.emit("more", self.task_id)

    def fade_in(self):
        """Soft entrance when the card is first added to the list."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(220); a.setStartValue(0.0); a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.finished.connect(lambda: self.setGraphicsEffect(None))   # drop effect after (no lingering repaint cost)
        a.start()
        self._fade_anim = a                                        # keep a ref alive

    def set_selected(self, on):
        if on == self._selected:
            return
        self._selected = on
        self.setStyleSheet(
            f"#card {{ border: 1px solid {COLORS['accent']}; background: {COLORS['card_hover']}; }}"
            if on else "")

    def update_task(self, t, bps, history=None):
        self.name.setText(t.filename or "download")
        self._drag_url = t.url or ""
        done0 = t.status == T.COMPLETED
        sp = getattr(t, "save_path", "") or ""
        self._drag_file = sp if (done0 and sp and os.path.exists(sp)) else ""
        qn = getattr(t, "queue_name", "Main") or "Main"
        self.qbadge.setText(qn)
        self.qbadge.setVisible(qn != "Main")
        ic_name, ic_color = _icon_for(t)
        self.icon.setPixmap(themed_icon(ic_name, "white").pixmap(24, 24))
        self.icon.setStyleSheet(f"background: {ic_color}; border-radius: 8px;")
        is_tor = _torrent.is_torrent_task(t.url, t.filename)
        done = t.status == T.COMPLETED

        pct = 100 if done else max(0, min(100, t.percent))
        self.bar.setValue(pct)
        self.bar.setVisible(not done)
        self.pct.setText("" if done else f"{pct}%")
        col = _BAR_COLOR.get(t.status, COLORS["accent"])
        self.bar.setStyleSheet(
            f"QProgressBar{{background:{COLORS['surface2']};border:none;border-radius:4px;max-height:6px;}}"
            f"QProgressBar::chunk{{background:{col};border-radius:4px;}}")

        if done:
            age = humanize_age(getattr(t, "added", 0))
            self.sub.setText(f"Completed  •  {human_size(t.total_size or t.downloaded)}"
                             + (f"  •  {age}" if age else ""))
        elif is_tor and t.status == T.DOWNLOADING and not t.total_size:
            self.sub.setText("Fetching metadata…")
        elif t.status == T.ERROR:
            self.sub.setText(t.error[:70] if t.error else "Failed")
        else:
            parts = [f"{human_size(t.downloaded)} / {human_size(t.total_size)}"]
            if t.status == T.DOWNLOADING:
                spd = human_speed(bps)
                if spd:
                    parts.append(spd)
                if is_tor:
                    parts.append(f"{getattr(t,'tor_conns',0)} peers · {getattr(t,'tor_seeds',0)} seeds")
                else:
                    eta = fmt_eta((t.total_size - t.downloaded) / bps) if bps > 0 and t.total_size else ""
                    if eta:
                        parts.append(f"ETA {eta}")
            else:
                parts.append(str(t.status))
            self.sub.setText("  -  ".join(parts))

        # live speed sparkline — only while actively downloading
        if t.status == T.DOWNLOADING and history and len(history) >= 2:
            self.spark.set_data(history); self.spark.setVisible(True)
        else:
            self.spark.setVisible(False)

        if t.status == T.DOWNLOADING:
            self._primary_action = "pause"
            self.btn_primary.setIcon(themed_icon("pause", "text")); self.btn_primary.setToolTip("Pause"); self.btn_primary.show()
        elif t.status in (T.PAUSED, T.ERROR, T.QUEUED, T.SCHEDULED):
            self._primary_action = "resume"
            self.btn_primary.setIcon(themed_icon("play", "text")); self.btn_primary.setToolTip("Resume"); self.btn_primary.show()
        elif done:
            self._primary_action = "open"
            self.btn_primary.setIcon(themed_icon("open", "text")); self.btn_primary.setToolTip("Open file"); self.btn_primary.show()
        else:
            self.btn_primary.hide()
