"""Tray + app icon loader with light/dark menu bar detection.

Hierarchy:
- Prefer bundled PNG/SVG from assets/tray and assets/logo.
- Fall back to PIL-drawn placeholder so the tray icon is never empty.

Light/dark menu bar handled via `darkdetect`. The tray template-image path
auto-tints regardless, so the dark variant is only needed when the icon
contains color we don't want inverted (e.g. terracotta recording dot).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

Status = Literal["idle", "recording", "processing"]

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_TRAY = _ASSETS / "tray"
_LOGO = _ASSETS / "logo"


def _is_dark_menu_bar() -> bool:
    try:
        import darkdetect  # type: ignore
        return bool(darkdetect.isDark())
    except Exception:
        return False


def tray_icon_path(status: Status) -> Path | None:
    """Return path to bundled tray icon for status, or None if missing.

    Variants tried in order:
      tray-{status}-dark.png  (dark menu bar only)
      tray-{status}.png
    """
    dark = _is_dark_menu_bar()
    candidates = []
    if dark:
        candidates.append(_TRAY / f"tray-{status}-dark.png")
    candidates.append(_TRAY / f"tray-{status}.png")
    for c in candidates:
        if c.exists():
            return c
    return None


# ── PIL FALLBACK ICONS ───────────────────────────────────────
# Used when bundled tray PNGs aren't on disk yet. Brand-aligned colors
# from ui/tokens.py — terracotta outline reads on light AND dark menu
# bars without needing template-image auto-tinting.

_TERRACOTTA       = (184, 73, 44, 255)   # ui/tokens.py Color.TERRACOTTA
_TERRACOTTA_SOFT  = (244, 225, 213, 255) # Color.TERRACOTTA_SOFT — disc fill
_SAGE             = (95, 123, 104, 255)
_AMBER            = (200, 133, 26, 255)
_PAPER            = (250, 247, 242, 255)

_DOT_COLOR = {
    "idle":       _SAGE,
    "recording":  _TERRACOTTA,
    "processing": _AMBER,
}


def render_tray_icon(status: Status, size: int = 22) -> Image.Image:
    """Generate a 22×22 (or @2x 44×44) tray icon.

    Design: solid terracotta ring + soft-paper interior + status dot
    in the center. Terracotta is chromatic enough to read against both
    light and dark menu bars; the interior fill keeps the mark from
    "ghosting out" on dark menus.
    """
    px = size * 2  # render at 2x for retina crispness
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = max(2, px // 8)
    box = (pad, pad, px - pad, px - pad)

    # Soft-paper inner disc (gives the ring a substrate, prevents
    # menu-bar blue/black from showing through on dark mode)
    d.ellipse(box, fill=_TERRACOTTA_SOFT)

    # Heavy terracotta ring — primary brand cue, visible everywhere
    ring_w = max(2, px // 11)
    d.ellipse(box, outline=_TERRACOTTA, width=ring_w)

    # Center status dot — large enough to read at 22px menu-bar size
    dot_r = px // 5
    cx = px // 2
    cy = px // 2
    d.ellipse(
        (cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r),
        fill=_DOT_COLOR[status],
    )

    # Recording: add a thin paper rim around the dot for extra punch
    if status == "recording":
        rim = max(1, px // 22)
        d.ellipse(
            (cx - dot_r - rim, cy - dot_r - rim, cx + dot_r + rim, cy + dot_r + rim),
            outline=_PAPER, width=rim,
        )

    return img


def tray_icon_image(status: Status, size: int = 22) -> Image.Image:
    """Return a PIL.Image for status, preferring a bundled PNG if present."""
    p = tray_icon_path(status)
    if p is not None:
        try:
            return Image.open(p).convert("RGBA")
        except Exception:
            pass
    return render_tray_icon(status, size=size)


def tray_icon_resolved_path(status: Status, size: int = 22) -> Path:
    """Disk path to a tray icon for status. Renders PIL fallback to a
    temp file if no bundled PNG is present. Cached per-process.

    rumps needs a filesystem path for `self.icon = ...`, so we can't hand
    it a PIL.Image directly.
    """
    bundled = tray_icon_path(status)
    if bundled is not None:
        return bundled
    return _render_fallback_to_tmp(status, size)


_tmp_cache: dict[tuple[str, int], Path] = {}


def _render_fallback_to_tmp(status: Status, size: int) -> Path:
    key = (status, size)
    cached = _tmp_cache.get(key)
    if cached is not None and cached.exists():
        return cached

    import tempfile
    tmp_dir = Path(tempfile.gettempdir()) / "openflow-tray-icons"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"tray-{status}-{size}.png"
    img = render_tray_icon(status, size=size)
    img.save(out, format="PNG")
    _tmp_cache[key] = out
    return out


def app_icon_path() -> Path | None:
    """Path to the app's .icns / .png logo, or None if not bundled yet."""
    for name in ("icon.icns", "icon.png", "openflow-mark.svg"):
        p = _LOGO / name
        if p.exists():
            return p
    return None


def wordmark_svg_path() -> Path | None:
    p = _LOGO / "openflow-logo.svg"
    return p if p.exists() else None


def mark_svg_path() -> Path | None:
    p = _LOGO / "openflow-mark.svg"
    return p if p.exists() else None


def window_qicon():
    """QIcon for QWidget.setWindowIcon(). Returns None if Qt or asset missing."""
    try:
        from PyQt6.QtGui import QIcon
    except Exception:
        return None
    # Prefer the high-res PNG over .icns — Qt's .icns reader is fine on mac
    # but the PNG fallback works everywhere.
    for name in ("icon.png", "icon.icns", "openflow-mark.svg"):
        p = _LOGO / name
        if p.exists():
            return QIcon(str(p))
    return None
