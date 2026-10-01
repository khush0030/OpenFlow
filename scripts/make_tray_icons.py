"""Render the menu bar icon "A · Mark" into assets/tray/*.png.

Run once after changing the mark: .venv/bin/python scripts/make_tray_icons.py
Chosen by the user 2026-10-01 (app-hub spec §7): an open ring with its gap at
the upper right plus a centre dot, on an 18 pt grid. Idle and processing are
template images (black on transparent, macOS tints them); recording keeps a
terracotta dot, so it is non-template and needs a white-ring dark variant.

The grid sits centred on a 20 pt canvas because rumps forces every status
image to 20 × 20 pt; a 20 pt file is drawn 1:1 instead of being stretched.
Each shape is drawn as a mask at 8× and box-filtered down for clean edges.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "tray"
CANVAS = 20          # pt; rumps sets the NSImage to 20 × 20
GRID = 18            # pt; the mark's design grid
SUPER = 8            # supersampling factor
RING_R = 7           # centre of the stroke
RING_W = 1.5
DASH, GAP = 33, 11   # SVG dasharray: a ¾ ring, gap of a quarter turn
GAP_AT = 45          # gap centred at 45° (upper right), counter-clockwise from 3 o'clock
DOT_R = {"idle": 3.2, "recording": 3.2, "processing": 2.2}

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
TERRACOTTA = (184, 73, 44)  # ui/tokens.py Color.TERRACOTTA


def _ring_mask(px: int) -> Image.Image:
    k = px / CANVAS * SUPER
    c = CANVAS / 2 * k
    outer = (RING_R + RING_W / 2) * k
    gap_deg = 360 * GAP / (DASH + GAP)
    # PIL angles run clockwise from 3 o'clock (y points down), so the
    # counter-clockwise gap centre at +45° is -45° here.
    start = -GAP_AT + gap_deg / 2
    m = Image.new("L", (px * SUPER, px * SUPER), 0)
    ImageDraw.Draw(m).arc((c - outer, c - outer, c + outer, c + outer),
                          start, start + 360 - gap_deg, fill=255, width=round(RING_W * k))
    return m.resize((px, px), Image.Resampling.BOX)


def _dot_mask(px: int, r: float) -> Image.Image:
    k = px / CANVAS * SUPER
    c = CANVAS / 2 * k
    m = Image.new("L", (px * SUPER, px * SUPER), 0)
    ImageDraw.Draw(m).ellipse((c - r * k, c - r * k, c + r * k, c + r * k), fill=255)
    return m.resize((px, px), Image.Resampling.BOX)


def render(status: str, px: int, ring=BLACK, dot=BLACK) -> Image.Image:
    """The mark at px × px. Ring and dot don't overlap, so each pixel takes
    the colour of whichever shape covers it, unpremultiplied."""
    ring_m, dot_m = _ring_mask(px), _dot_mask(px, DOT_R[status])
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    for y in range(px):
        for x in range(px):
            a_ring, a_dot = ring_m.getpixel((x, y)), dot_m.getpixel((x, y))
            if a_dot:
                img.putpixel((x, y), (*dot, a_dot))
            elif a_ring:
                img.putpixel((x, y), (*ring, a_ring))
    return img


def main() -> None:
    assert math.isclose(2 * math.pi * RING_R, DASH + GAP, rel_tol=0.01)
    OUT.mkdir(parents=True, exist_ok=True)
    variants = [
        ("tray-idle", "idle", BLACK, BLACK),
        ("tray-processing", "processing", BLACK, BLACK),
        ("tray-recording", "recording", BLACK, TERRACOTTA),
        ("tray-recording-dark", "recording", WHITE, TERRACOTTA),
    ]
    for name, status, ring, dot in variants:
        for scale, suffix in ((1, ""), (2, "@2x")):
            path = OUT / f"{name}{suffix}.png"
            render(status, CANVAS * scale, ring, dot).save(path, format="PNG", optimize=True)
            print(f"wrote {path.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
