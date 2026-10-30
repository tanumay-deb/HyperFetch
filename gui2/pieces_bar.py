"""A torrent's pieces as two strips, as uTorrent shows them (asked 2026-10-30):
which pieces are here, and how many connected peers have each - red where
nobody does. Pieces are binned to the strip's width; a bin is as full as the
share of its pieces that are here, and as available as its rarest piece."""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor

from gui2.palette import COLORS, fpx

ROW = 9            # strip height
GAP = 8
PAD = 4            # above and below, for the captions
LABEL = 76         # room for the captions on the left
NUM = 44           # room for the number on the right


def availability(counts):
    """uTorrent's figure: copies of the rarest piece, plus the share of pieces
    held more often than that. 0.0 when nothing is known."""
    if not counts:
        return 0.0
    low = min(counts)
    return low + sum(1 for c in counts if c > low) / len(counts)


def piece_counts(peer_bitfields, count, have_fn):
    """How many of the peers hold each piece; a seed's bitfield is whole."""
    counts = [0] * count
    for bits in peer_bitfields:
        for i, h in enumerate(have_fn(bits, count)[:count]):
            if h:
                counts[i] += 1
    return counts


class PiecesBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._have = []
        self._counts = None
        self.setFixedHeight(ROW + 2 * PAD)

    def set_pieces(self, have, counts=None):
        """`have`: [bool] per piece. `counts`: copies per piece among the
        connected peers, or None to leave the availability strip out."""
        if have == self._have and counts == self._counts:
            return
        self._have, self._counts = list(have), (None if counts is None else list(counts))
        self.setFixedHeight((ROW if counts is None else 2 * ROW + GAP) + 2 * PAD)
        self.setToolTip(self._tip())
        self.update()

    def _tip(self):
        n = len(self._have)
        got = sum(self._have)
        tip = f"{got} of {n} pieces here" if n else "No pieces known yet"
        if self._counts is not None:
            tip += (f"\nAvailability {availability(self._counts):.3f}: copies of the "
                    "rarest piece among the connected peers. Red: nobody connected has it.")
        return tip

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width() - LABEL - NUM
        if w <= 4:
            return
        f = p.font(); f.setPixelSize(int(fpx(11).rstrip("px"))); p.setFont(f)
        rows = [("Downloaded", self._have_bins(w), f"{self._pct():.1f}%")]
        if self._counts is not None:
            rows.append(("Availability", self._avail_bins(w),
                         f"{availability(self._counts):.3f}"))
        for r, (caption, bins, num) in enumerate(rows):
            y = PAD + r * (ROW + GAP)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(0, y - 3, LABEL - 6, ROW + 6), Qt.AlignVCenter | Qt.AlignLeft, caption)
            p.drawText(QRectF(LABEL + w + 4, y - 3, NUM - 4, ROW + 6),
                       Qt.AlignVCenter | Qt.AlignRight, num)
            p.fillRect(QRectF(LABEL, y, w, ROW), QColor(COLORS["surface"]))
            for x, col in bins:
                p.fillRect(QRectF(LABEL + x, y, 1, ROW), col)
            p.setPen(QColor(COLORS["border"]))
            p.drawRect(QRectF(LABEL, y, w - 1, ROW - 1))
        p.end()

    def _pct(self):
        return 100.0 * sum(self._have) / len(self._have) if self._have else 0.0

    def _have_bins(self, w):
        if not self._have:
            return []
        acc = QColor(COLORS["success"])
        out = []
        n = len(self._have)
        for x in range(w):
            lo, hi = x * n // w, max(x * n // w + 1, (x + 1) * n // w)
            share = sum(self._have[lo:hi]) / max(1, hi - lo)
            if share:
                c = QColor(acc); c.setAlphaF(0.35 + 0.65 * share)
                out.append((x, c))
        return out

    def _avail_bins(self, w):
        out, counts = [], self._counts or []
        n = len(counts)
        top = max(counts) if counts else 1
        for x in range(w):
            lo, hi = x * n // w, max(x * n // w + 1, (x + 1) * n // w)
            if lo >= n:
                break
            low = min(counts[lo:hi])
            if low == 0:
                out.append((x, QColor(COLORS["error"])))
            else:
                c = QColor(COLORS["info"]); c.setAlphaF(0.35 + 0.65 * low / max(1, top))
                out.append((x, c))
        return out
