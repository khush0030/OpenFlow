"""Paper / Ink colour tokens for the flow widget (spec §2). Pure: no Qt.

Colours are (r, g, b, a) tuples with a in 0–255.
"""
from __future__ import annotations

from dataclasses import dataclass

FONT_UI = "Geist"
FONT_SERIF = "Fraunces"
ACCENT = (229, 64, 47, 255)          # #E5402F bright red (widget only; brand terracotta stays in ui/tokens.py)
APPEARANCES = ("paper", "ink", "auto")

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Theme:
    name: str
    surface: RGBA
    text: RGBA
    muted: RGBA
    hairline: RGBA
    button_bg: RGBA
    button_text: RGBA
    button_hover: RGBA
    secondary_bg: RGBA
    secondary_text: RGBA
    shadow: RGBA
    accent: RGBA = ACCENT


PAPER = Theme(
    name="paper",
    surface=(250, 247, 242, 255),       # #FAF7F2
    text=(26, 24, 20, 255),             # #1A1814
    muted=(138, 127, 115, 255),         # #8A7F73
    hairline=(232, 226, 217, 255),      # #E8E2D9
    button_bg=(26, 24, 20, 255),
    button_text=(250, 247, 242, 255),
    button_hover=(61, 56, 50, 255),
    secondary_bg=(239, 234, 225, 255),  # #EFEAE1
    secondary_text=(26, 24, 20, 255),
    shadow=(60, 40, 25, 46),
)

INK = Theme(
    name="ink",
    surface=(26, 24, 20, 255),
    text=(250, 247, 242, 255),
    muted=(163, 154, 142, 255),         # #A39A8E
    hairline=(250, 247, 242, 26),
    button_bg=(250, 247, 242, 255),
    button_text=(26, 24, 20, 255),
    button_hover=(232, 226, 217, 255),
    secondary_bg=(61, 56, 50, 255),     # #3D3832
    secondary_text=(250, 247, 242, 255),
    shadow=(60, 30, 15, 89),
)


def resolve(appearance: str, system_dark: bool) -> Theme:
    if appearance == "ink":
        return INK
    if appearance == "auto":
        return INK if system_dark else PAPER
    return PAPER
