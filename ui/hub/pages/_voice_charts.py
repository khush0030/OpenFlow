"""Small painted widgets for the Insights page (both tabs).

Same visual language as _charts.py: widget-red marks (S.ACCENT), soft red
for secondary marks, neutral tracks, Geist / JetBrains Mono labels. Every
widget can shrink with its card (no fixed widths) so cards can reflow.
"""
from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QWidget,
                             QWidgetItem)

from ui.hub import style as S
from ui.hub.pages import _charts as C

TRACK = C.BAR_TRACK
SOFT = C.BAR_SOFT
GRID = S.HAIR


# ── tabs ─────────────────────────────────────────────────────────────────
class Tabs(QWidget):
    """Text tabs over a full-width hairline; the active one is Ink with an
    Ink underline. Emits `changed(index)` on click."""

    changed = pyqtSignal(int)

    def __init__(self, labels: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.labels = list(labels)
        self.index = 0
        self.buttons: list[QPushButton] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(26)
        for i, label in enumerate(self.labels):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(S.sans(14.5, 500))
            b.setFixedHeight(36)
            b.setAccessibleName(f"{label} tab")
            b.clicked.connect(lambda _c=False, i=i: self.set_index(i, emit=True))
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch(1)
        self.setFixedHeight(36)
        self._restyle()

    def set_index(self, i: int, emit: bool = False) -> None:
        if not 0 <= i < len(self.labels):
            return
        changed = i != self.index
        self.index = i
        self._restyle()
        if emit and changed:
            self.changed.emit(i)

    def _restyle(self) -> None:
        for i, b in enumerate(self.buttons):
            on = i == self.index
            b.setChecked(on)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;padding:0 0 4px 0;"
                f"border-bottom:2px solid {S.INK if on else 'transparent'};"
                f"color:{S.INK if on else S.MUTED};}}"
                f"QPushButton:hover{{color:{S.INK};}}")

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), QColor(S.HAIR))
        p.end()


# ── labels ───────────────────────────────────────────────────────────────
class ElideLabel(QLabel):
    """Single-line label that elides with … instead of clipping or forcing
    its column wider."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        super().setText(text)
        self.setToolTip(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full) + 2, fm.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(1, self.fontMetrics().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        fm = self.fontMetrics()
        QLabel.setText(self, fm.elidedText(self._full, Qt.TextElideMode.ElideRight,
                                           max(1, self.width())))


# ── flow layout (chips) ──────────────────────────────────────────────────
class FlowLayout(QLayout):
    """Left-to-right items that wrap to the next line."""

    def __init__(self, parent: QWidget | None = None, h: int = 8, v: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = h, v
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def add(self, w: QWidget) -> None:
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return self._do(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, r: QRect) -> None:  # noqa: N802
        super().setGeometry(r)
        self._do(r, dry=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        s = QSize(0, 0)
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, r: QRect, dry: bool) -> int:
        x, y, line = r.x(), r.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            w = min(hint.width(), r.width())
            if x > r.x() and x + w > r.right() + 1:
                x, y, line = r.x(), y + line + self._v, 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), QSize(w, hint.height())))
            x += w + self._h
            line = max(line, hint.height())
        return y + line - r.y()


# ── gauge with the ratio in its hollow ───────────────────────────────────
class RatioGauge(QWidget):
    """Half-ring gauge like C.Gauge, with a Fraunces figure ("3.9×") set
    inside the arc so no text has to sit beside it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fraction = 0.0
        self.center_text = ""
        self.setFixedSize(168, 96)

    def set_value(self, fraction: float, text: str) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self.center_text = text
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self.width() / 120.0
        sw, r = 10 * k, 50 * k
        cx, cy = 60 * k, 60 * k
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        pen = QPen(QColor(C.GAUGE_TRACK), sw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 180 * 16, -180 * 16)
        if self.fraction > 0:
            pen.setColor(QColor(S.ACCENT))
            p.setPen(pen)
            p.drawArc(rect, 180 * 16, int(-180 * 16 * self.fraction))
        if self.center_text:
            p.setFont(S.serif(S.T_H2 + 1))
            p.setPen(QColor(S.INK))
            p.drawText(QRectF(0, cy - 34, self.width(), 34),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                       self.center_text)
        p.end()


# ── split bar ────────────────────────────────────────────────────────────
class SplitBar(QWidget):
    """Top tone in red with its label; the remainder in soft red. The
    remainder's % is drawn only when it fits (C.SplitBar clipped it)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label, self.pct = "", 0
        self.setFixedHeight(26)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_split(self, label: str, pct: int) -> None:
        self.label, self.pct = label, pct
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 7, 7)
        p.setClipPath(clip)
        p.fillRect(r, QColor(S.ACCENT_SOFT if self.label else TRACK))
        if self.label:
            w = r.width() * self.pct / 100.0
            p.fillRect(QRectF(0, 0, w, r.height()), QColor(S.ACCENT))
            p.setFont(S.sans(S.T_SMALL, 500))
            fm = QFontMetricsF(p.font())
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(10, 0, max(0.0, w - 14), r.height()),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       fm.elidedText(f"{self.label} · {self.pct}%", Qt.TextElideMode.ElideRight,
                                     max(0.0, w - 16)))
            rest = f"{100 - self.pct}%"
            if self.pct < 100 and r.width() - w >= fm.horizontalAdvance(rest) + 12:
                p.setPen(QColor(S.ACCENT_TEXT))
                p.drawText(QRectF(w, 0, r.width() - w, r.height()),
                           int(Qt.AlignmentFlag.AlignCenter), rest)
        p.end()


# ── line chart ───────────────────────────────────────────────────────────
class LineChart(QWidget):
    """A weekly series: red line and dots over faint gridlines, the last
    value labelled, x labels under the first, middle and last points.
    Points are evenly spaced (they are the weeks that have data)."""

    def __init__(self, height: int = 150, fmt=lambda v: f"{v:.0f}",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.points: list[tuple[str, float]] = []
        self.fmt = fmt
        self.setFixedHeight(height)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_points(self, points: Sequence[tuple[str, float]]) -> None:
        self.points = list(points)
        self.update()

    def paintEvent(self, _e) -> None:
        if not self.points:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = S.sans(11.5)
        fm = QFontMetricsF(small)
        label_h = fm.height() + 8
        top, left, right = 22.0, 8.0, 8.0
        bottom = self.height() - label_h
        vals = [v for _l, v in self.points]
        lo, hi = min(vals), max(vals)
        span = hi - lo or max(1.0, abs(hi) * 0.2)
        lo, hi = lo - span * 0.25, hi + span * 0.25
        if min(vals) >= 0:
            lo = max(0.0, lo)
        w = self.width() - left - right
        n = len(self.points)

        def xy(i: int, v: float) -> QPointF:
            x = left + (w * i / (n - 1) if n > 1 else w / 2)
            return QPointF(x, bottom - (v - lo) / (hi - lo) * (bottom - top))

        p.setPen(QPen(QColor(GRID), 1))
        for j in range(3):
            y = top + (bottom - top) * j / 2
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        pts = [xy(i, v) for i, (_l, v) in enumerate(self.points)]
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        p.setPen(QPen(QColor(S.ACCENT), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for i, q in enumerate(pts):
            last = i == n - 1
            p.setBrush(QColor(S.PAPER))
            p.drawEllipse(q, 5.5 if last else 4.2, 5.5 if last else 4.2)
            p.setBrush(QColor(S.ACCENT))
            p.drawEllipse(q, 3.6 if last else 2.6, 3.6 if last else 2.6)
        # last value
        p.setFont(S.sans(S.T_SMALL, 600))
        tfm = QFontMetricsF(p.font())
        t = self.fmt(vals[-1])
        tw = tfm.horizontalAdvance(t)
        q = pts[-1]
        tx = min(max(0.0, q.x() - tw / 2), self.width() - tw)
        p.setPen(QColor(S.INK))
        p.drawText(QRectF(tx, q.y() - 24, tw + 2, 18),
                   int(Qt.AlignmentFlag.AlignCenter), t)
        # x labels: first, middle, last (skipping any that would collide)
        p.setFont(small)
        p.setPen(QColor(S.MUTED))
        idx = sorted({0, n // 2, n - 1}) if n > 2 else list(range(n))
        placed: list[tuple[float, float]] = []
        for i in idx:
            lab = self.points[i][0]
            lw = fm.horizontalAdvance(lab)
            x = min(max(0.0, pts[i].x() - lw / 2), self.width() - lw)
            if any(x < b + 10 and a < x + lw + 10 for a, b in placed):
                continue
            placed.append((x, x + lw))
            p.drawText(QRectF(x, bottom + 8, lw + 2, label_h - 8),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), lab)
        p.end()


# ── hour-of-day bars ─────────────────────────────────────────────────────
class HourBars(QWidget):
    """24 vertical bars (dictations per hour); the busiest hour in red,
    the rest soft red, empty hours a short neutral stub."""

    def __init__(self, height: int = 132, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.counts = [0] * 24
        self.setFixedHeight(height)
        self.setMinimumWidth(24 * 5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_counts(self, counts: Sequence[int]) -> None:
        self.counts = list(counts)[:24] + [0] * max(0, 24 - len(counts))
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = S.sans(11.5)
        fm = QFontMetricsF(small)
        label_h = fm.height() + 8
        bottom = self.height() - label_h
        top = 4.0
        slot = self.width() / 24
        bw = max(3.0, min(slot * 0.62, 18.0))
        peak = max(self.counts) if any(self.counts) else 0
        p.setPen(Qt.PenStyle.NoPen)
        for h, c in enumerate(self.counts):
            x = h * slot + (slot - bw) / 2
            if c <= 0:
                p.setBrush(QColor(TRACK))
                p.drawRoundedRect(QRectF(x, bottom - 3, bw, 3), 1.5, 1.5)
                continue
            bh = max(4.0, (bottom - top) * c / peak)
            p.setBrush(QColor(S.ACCENT if c == peak else SOFT))
            p.drawRoundedRect(QRectF(x, bottom - bh, bw, bh), min(3.0, bw / 2), min(3.0, bw / 2))
        p.setFont(small)
        p.setPen(QColor(S.MUTED))
        for h, lab in ((0, "12a"), (6, "6a"), (12, "12p"), (18, "6p")):
            x = h * slot + (slot - bw) / 2
            p.drawText(QRectF(x, bottom + 8, 40, label_h - 8),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), lab)
        p.end()
