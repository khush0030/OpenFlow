"""Design tokens for OpenFlow. Single source of truth.

Every color, font, size, motion, and shadow constant lives here.
Do not hardcode these values anywhere else in the UI code.

Source: DESIGN_INTEGRATION.md §A.1, cross-checked against brand book.
"""
from __future__ import annotations


# ── COLORS ───────────────────────────────────────────────────
class Color:
    # Surfaces
    PAPER         = "#FAF7F2"
    PAPER_DEEP    = "#F2EEE5"
    PAPER_DEEPER  = "#E8E2D9"   # rules and dividers

    # Ink
    INK           = "#1A1814"
    INK_SOFT      = "#3D3832"
    INK_MUTED     = "#8A7F73"

    # Brand
    TERRACOTTA      = "#B8492C"
    TERRACOTTA_DEEP = "#8A3520"
    TERRACOTTA_SOFT = "#F4E1D5"

    # Semantic
    SAGE          = "#5F7B68"   # idle / success
    AMBER         = "#C8851A"   # processing / warning
    DANGER        = "#B8492C"   # same as terracotta intentionally

    # Dark-mode reserves (used only by recording pill + edit overlay)
    PAPER_DARK         = "#1A1814"
    PAPER_DEEP_DARK    = "#252220"
    INK_DARK           = "#FAF7F2"
    INK_MUTED_DARK     = "#8A7F73"


# ── TYPOGRAPHY ───────────────────────────────────────────────
class Font:
    DISPLAY = "Fraunces"
    BODY    = "Geist"
    MONO    = "JetBrains Mono"

    # System fallbacks (used when bundled fonts not yet on disk)
    DISPLAY_FALLBACK = "Georgia"
    BODY_FALLBACK    = "-apple-system"
    MONO_FALLBACK    = "Menlo"

    # Sizes (px)
    SIZE_DISPLAY   = 36
    SIZE_H1        = 26
    SIZE_H2        = 20
    SIZE_H3        = 16
    SIZE_BODY      = 13
    SIZE_BODY_SM   = 12
    SIZE_LABEL     = 11
    SIZE_MONO_KBD  = 11
    SIZE_EYEBROW   = 10   # uppercase mono labels

    # Weights
    WEIGHT_LIGHT    = 300
    WEIGHT_REGULAR  = 400
    WEIGHT_MEDIUM   = 500
    WEIGHT_SEMIBOLD = 600


# ── SPACING ──────────────────────────────────────────────────
class Space:
    XS   = 4
    SM   = 8
    MD   = 12
    LG   = 16
    XL   = 24
    XXL  = 32
    XXXL = 48


# ── RADIUS ───────────────────────────────────────────────────
class Radius:
    SM   = 4
    MD   = 6
    LG   = 8
    XL   = 12
    PILL = 100


# ── MOTION ───────────────────────────────────────────────────
class Motion:
    FAST  = 120   # ms — hover transitions
    BASE  = 200   # ms — most state changes
    SLOW  = 320   # ms — page transitions
    PULSE = 1400  # ms — recording dot pulse cycle
    WAVE  = 1200  # ms — waveform bar cycle

    EASE_OUT    = "cubic-bezier(0.16, 1, 0.3, 1)"
    EASE_IN_OUT = "cubic-bezier(0.65, 0, 0.35, 1)"


# ── SHADOWS ──────────────────────────────────────────────────
# (blur_radius, x_offset, y_offset, alpha) — feed to QGraphicsDropShadowEffect.
class Shadow:
    SUBTLE = (8,  0,  2,  0.06)
    CARD   = (24, 0,  8,  0.10)
    MODAL  = (60, 0, 30,  0.25)
    PILL   = (40, 0, 20,  0.30)
