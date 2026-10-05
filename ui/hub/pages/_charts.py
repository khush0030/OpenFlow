"""Painting helpers shared by the Home and Insights pages.

Icons are the mockup's 24-unit stroke glyphs rendered from SVG; charts
(gauge, bars, tone split, heatmap) are plain QPainter widgets so they need
nothing beyond PyQt.
"""
from __future__ import annotations

import os
import pwd
from datetime import date, timedelta

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QWidget

from ui.hub import style as S

# ── icons ────────────────────────────────────────────────────────────────
_ICONS = {
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "copy": '<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/>',
    "paste": '<path d="M9 14l-4-4 4-4"/><path d="M5 10h9a5 5 0 0 1 5 5v3"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
}


def icon_pixmap(name: str, size: int = 18, color: str | None = None, sw: float = 1.7,
                scale: float = 2.0) -> QPixmap:
    color = color or S.INK
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" '
           f'stroke-linejoin="round">{_ICONS[name]}</svg>')
    px = int(round(size * scale))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(svg.encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, size: int = 18, color: str | None = None, sw: float = 1.7) -> QIcon:
    return QIcon(icon_pixmap(name, size, color, sw))


# ── small helpers ────────────────────────────────────────────────────────
def num(n: float) -> str:
    """Whole number with thousands separators."""
    return f"{int(round(n)):,}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{num(n)} {one if n == 1 else (many or one + 's')}"


def first_name() -> str | None:
    """First word of the macOS full name ('Khush Mutha' → 'Khush')."""
    full = ""
    try:
        from Foundation import NSFullUserName  # type: ignore
        full = str(NSFullUserName() or "")
    except Exception:
        full = ""
    if not full.strip():
        try:
            full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        except Exception:
            full = ""
    parts = full.split()
    return parts[0] if parts else None


def scroll_page(content: QWidget) -> QScrollArea:
    """Vertical-only scroll area for a page body on the Paper panel."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setStyleSheet(f"QScrollArea{{background:{S.PAPER};border:none;}}" + S.scrollbar_qss())
    sa.viewport().setObjectName("pageviewport")
    sa.viewport().setStyleSheet(f"QWidget#pageviewport{{background:{S.PAPER};}}")
    content.setObjectName("pagebody")
    content.setStyleSheet(f"QWidget#pagebody{{background:{S.PAPER};}}")
    content.setMaximumWidth(S.PAGE_MAX_W)
    # Centre the body once the window is wider than PAGE_MAX_W.
    holder = QWidget()
    holder.setObjectName("pageholder")
    holder.setStyleSheet(f"QWidget#pageholder{{background:{S.PAPER};}}")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(content, 1)
    sa.setWidget(holder)
    return sa


def hairline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{S.HAIR};border:none;")
    return f


def _font(family: str, size: float, weight: int = 400) -> QFont:
    f = QFont(family)
    if family == S.MONO:
        f.setFamilies([S.MONO, "Menlo"])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    return f


# ── gauge ────────────────────────────────────────────────────────────────
class Gauge(QWidget):
    """Half-ring gauge (the mockup's 120×68 SVG drawn at 180×104)."""

    def __init__(self, fraction: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fraction = max(0.0, min(1.0, fraction))
        self.setFixedSize(180, 104)

    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self.width() / 120.0
        sw = 11 * k
        r = 50 * k
        cx, cy = 60 * k, 60 * k
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        pen = QPen(QColor(S.GAUGE_TRACK), sw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 180 * 16, -180 * 16)
        if self.fraction > 0:
            pen.setColor(QColor(S.ACCENT))
            p.setPen(pen)
            p.drawArc(rect, 180 * 16, int(-180 * 16 * self.fraction))
        p.end()


# ── horizontal bars ──────────────────────────────────────────────────────
class Bar(QWidget):
    """One rounded bar (22 high): a track with a fill of `pct` percent."""

    def __init__(self, pct: float, strong: bool = False, parent: QWidget | None = None,
                 color: str | None = None) -> None:
        super().__init__(parent)
        self.pct = pct
        self.strong = strong
        self.color = color or (S.ACCENT if strong else S.BAR_SOFT)
        self.setFixedHeight(22)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        p.setBrush(QColor(S.BAR_TRACK))
        p.drawRoundedRect(r, 6, 6)
        w = r.width() * max(self.pct, 3) / 100.0
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(QRectF(0, 0, w, r.height()), 6, 6)
        p.end()


class SplitBar(QWidget):
    """Tone split: the top tone in terracotta with its label, the rest soft."""

    def __init__(self, label: str = "", pct: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label, self.pct = label, pct
        self.setFixedHeight(26)
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
        p.fillRect(r, QColor(S.ACCENT_SOFT if self.label else S.BAR_TRACK))
        if not self.label:
            p.end()
            return
        w = r.width() * self.pct / 100.0
        p.fillRect(QRectF(0, 0, w, r.height()), QColor(S.ACCENT))
        p.setFont(_font(S.SANS, 12.5))
        fm = QFontMetricsF(p.font())
        p.setPen(QColor(S.ON_ACCENT))
        p.drawText(QRectF(10, 0, max(0.0, w - 12), r.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   fm.elidedText(f"{self.label} · {self.pct}%", Qt.TextElideMode.ElideRight, max(0.0, w - 14)))
        if self.pct < 100:
            p.setPen(QColor(S.ACCENT_TEXT))
            p.drawText(QRectF(w, 0, r.width() - w, r.height()), int(Qt.AlignmentFlag.AlignCenter),
                       f"{100 - self.pct}%")
        p.end()


# ── heatmap ──────────────────────────────────────────────────────────────
WEEKS = 22
CELL, GAP, LABEL_W = 13, 4, 34


def heat_level(words: int) -> int:
    """0 = no dictation; 1–4 = <30, <120, <250, ≥250 words that day."""
    if words <= 0:
        return 0
    if words < 30:
        return 1
    if words < 120:
        return 2
    if words < 250:
        return 3
    return 4


def streak_days(per_day: dict[date, int], today: date) -> set[date]:
    """Days in the current streak (same rule as stats.current_streak)."""
    days = {d for d, w in per_day.items()}
    d = today if today in days else today - timedelta(days=1)
    out = set()
    while d in days:
        out.add(d)
        d -= timedelta(days=1)
    return out


def heat_start(today: date, weeks: int = WEEKS) -> date:
    """The Sunday that starts the first column, so today is in the last."""
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return sunday - timedelta(weeks=weeks - 1)


class Heatmap(QWidget):
    """22 weeks × Sun–Sat of words per day, month labels on top, current
    streak outlined in Ink, legend underneath."""

    MONTH_H = 22
    LEGEND_H = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.per_day: dict[date, int] = {}
        self.today = date.today()
        self.streak: set[date] = set()
        h = self.MONTH_H + 7 * CELL + 6 * GAP + self.LEGEND_H
        self.setMinimumSize(LABEL_W + WEEKS * CELL + (WEEKS - 1) * GAP, h)
        self.setFixedHeight(h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, per_day: dict[date, int], today: date) -> None:
        self.per_day = dict(per_day)
        self.today = today
        self.streak = streak_days(self.per_day, today)
        self.update()

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def cell_rect(self, day: date) -> QRectF | None:
        start = heat_start(self.today)
        i = (day - start).days
        if i < 0 or day > self.today:
            return None
        wk, dd = divmod(i, 7)
        return QRectF(LABEL_W + wk * (CELL + GAP), self.MONTH_H + dd * (CELL + GAP), CELL, CELL)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = QColor(S.MUTED)
        start = heat_start(self.today)
        # month labels: at the first column whose Sunday falls in a new month
        p.setFont(_font(S.SANS, 11))
        p.setPen(muted)
        seen = set()
        for wk in range(WEEKS):
            m = (start + timedelta(weeks=wk)).strftime("%b")
            if m not in seen:
                seen.add(m)
                if wk < WEEKS - 1 or len(seen) == 1:
                    p.drawText(QRectF(LABEL_W + wk * (CELL + GAP), 0, 40, 16),
                               int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), m)
        # day labels
        p.setFont(_font(S.SANS, 10.5))
        for dd, name in ((0, "Sun"), (2, "Tue"), (4, "Thu"), (6, "Sat")):
            p.drawText(QRectF(0, self.MONTH_H + dd * (CELL + GAP), LABEL_W - 8, CELL),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
        # cells
        p.setPen(Qt.PenStyle.NoPen)
        ring: list[QRectF] = []
        for i in range(WEEKS * 7):
            day = start + timedelta(days=i)
            r = self.cell_rect(day)
            if r is None:
                continue
            p.setBrush(QColor(S.HEAT[heat_level(self.per_day.get(day, 0))]))
            p.drawRoundedRect(r, 3, 3)
            if day in self.streak:
                ring.append(r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(S.INK), 1.5))
        for r in ring:
            p.drawRoundedRect(r.adjusted(-0.75, -0.75, 0.75, 0.75), 3.5, 3.5)
        # legend
        y = self.MONTH_H + 7 * CELL + 6 * GAP + 12
        p.setFont(_font(S.SANS, 12.5))
        fm = QFontMetricsF(p.font())
        p.setPen(muted)
        x = 0.0
        p.drawText(QRectF(x, y, 60, 14), int(Qt.AlignmentFlag.AlignVCenter), "More")
        x += fm.horizontalAdvance("More") + 6
        p.setPen(Qt.PenStyle.NoPen)
        for c in reversed(S.HEAT[1:]):
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(x, y + 1, 12, 12), 3, 3)
            x += 18
        p.setPen(muted)
        p.drawText(QRectF(x, y, 60, 14), int(Qt.AlignmentFlag.AlignVCenter), "Less")
        label = "Current streak"
        lw = fm.horizontalAdvance(label)
        rx = self.width() - lw
        p.drawText(QRectF(rx, y, lw + 2, 14), int(Qt.AlignmentFlag.AlignVCenter), label)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(S.INK), 1.5))
        p.drawRoundedRect(QRectF(rx - 18, y + 1, 12, 12), 3, 3)
        p.end()
