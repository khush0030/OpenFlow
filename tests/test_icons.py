"""Menu bar icon "A · Mark" (app-hub spec §7): bundled PNGs and the loader."""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image

from ui import icons

TERRACOTTA = (184, 73, 44)


@pytest.fixture
def light(monkeypatch):
    monkeypatch.setattr(icons, "_is_dark_menu_bar", lambda: False)


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.setattr(icons, "_is_dark_menu_bar", lambda: True)


def _img(name: str) -> Image.Image:
    return Image.open(icons._TRAY / name).convert("RGBA")


@pytest.mark.parametrize("status", ["idle", "recording", "processing"])
def test_bundled_pngs_at_1x_and_2x(status):
    assert _img(f"tray-{status}.png").size == (20, 20)
    assert _img(f"tray-{status}@2x.png").size == (40, 40)


@pytest.mark.parametrize("status", ["idle", "recording", "processing"])
def test_loader_hands_rumps_the_2x_file(light, status):
    # rumps sizes every status image to 20 × 20 pt and NSImage doesn't pick
    # up @2x siblings, so the 40 px file is the one that stays sharp on Retina.
    assert icons.tray_icon_path(status) == icons._TRAY / f"tray-{status}@2x.png"


def test_recording_has_a_dark_menu_bar_variant(dark):
    assert icons.tray_icon_path("recording") == icons._TRAY / "tray-recording-dark@2x.png"
    # Template states need no dark variant: macOS tints them.
    assert icons.tray_icon_path("idle") == icons._TRAY / "tray-idle@2x.png"


def test_template_flag_per_state(light):
    assert icons.tray_icon_is_template("idle") is True
    assert icons.tray_icon_is_template("processing") is True
    assert icons.tray_icon_is_template("recording") is False  # coloured dot


def test_pil_fallback_is_never_template(monkeypatch, tmp_path, light):
    monkeypatch.setattr(icons, "_TRAY", tmp_path)
    assert icons.tray_icon_path("idle") is None
    assert icons.tray_icon_is_template("idle") is False


@pytest.mark.parametrize("status", ["idle", "processing"])
def test_template_pngs_are_black_on_transparent(status):
    im = _img(f"tray-{status}@2x.png")
    pixels = (im.getpixel((x, y)) for y in range(im.height) for x in range(im.width))
    painted = [px for px in pixels if px[3] > 0]
    assert painted and all(px[:3] == (0, 0, 0) for px in painted)
    assert im.getpixel((0, 0))[3] == 0


def _dot_area(im: Image.Image) -> int:
    # Pixels inside r = 4.5 pt of the centre (inside the ring's 6.25 pt edge).
    c = im.width / 2
    s = im.width / 20
    return sum(1 for y in range(im.height) for x in range(im.width)
               if (x + .5 - c) ** 2 + (y + .5 - c) ** 2 <= (4.5 * s) ** 2
               and im.getpixel((x, y))[3] > 127)


def test_processing_dot_is_smaller():
    assert _dot_area(_img("tray-processing@2x.png")) < _dot_area(_img("tray-idle@2x.png"))


@pytest.mark.parametrize("name,ring", [("tray-recording@2x.png", (0, 0, 0)),
                                       ("tray-recording-dark@2x.png", (255, 255, 255))])
def test_recording_dot_is_terracotta_and_ring_monochrome(name, ring):
    im = _img(name)
    assert im.getpixel((20, 20)) == (*TERRACOTTA, 255)
    # Left edge of the ring (centre 10 pt, r 7 pt) is solid, in the ring colour.
    assert im.getpixel((6, 20)) == (*ring, 255)


def test_ring_gap_sits_upper_right():
    im = _img("tray-idle@2x.png")
    c, r = 20, 14  # 2x: centre 10 pt, r 7 pt

    def at(deg: float) -> int:
        a = math.radians(deg)
        return im.getpixel((round(c + r * math.cos(a)), round(c - r * math.sin(a))))[3]

    assert at(45) == 0                                   # gap, upper right
    assert all(at(d) > 200 for d in (135, 180, 225, 270, 315))
