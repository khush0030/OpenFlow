"""Paper / Ink hub themes (spec 2026-10-02-dark-hub.md), offscreen.

The hub follows [widget] appearance (paper / ink / auto = macOS). Never
touches the real ~/.openflow: config paths are monkeypatched to tmp, the
daemon is a fake, the Keychain and permissions probes are stubbed.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from control_channel import DaemonNotRunning
from ui.fonts import load_fonts
from ui.hub import app as hub
from ui.hub import style as S
from ui.hub.context import HubContext
from ui.hub.page import FOOTER_PAGES, PAGES

load_fonts()

HUB_DIR = Path(__file__).resolve().parent.parent / "ui" / "hub"
HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


class FakeControl:
    def call(self, cmd, timeout=5.0, **args):
        raise DaemonNotRunning("not in tests")


@pytest.fixture(autouse=True)
def paper_after():
    yield
    for w in QApplication.topLevelWidgets():     # windows these tests made
        if isinstance(w, hub.HubWindow):
            w.hide()
            sip.delete(w)
    if S.THEME != "paper":
        S.apply_theme("paper")
        hub.apply_app_theme()


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


@pytest.fixture
def stubs(monkeypatch):
    """Keep real pages off the Keychain, permission probes and `open`."""
    from ui.hub.pages import help as help_mod
    from ui.hub.pages import settings as settings_mod
    monkeypatch.setattr(settings_mod, "keychain_read", lambda: "")
    monkeypatch.setattr(settings_mod, "keychain_save", lambda k: None)
    monkeypatch.setattr(settings_mod, "fallback_key_found", lambda name, cfg: False)
    monkeypatch.setattr(settings_mod, "_env_files", lambda: [])
    monkeypatch.setattr(settings_mod, "run_open", lambda args: None)
    monkeypatch.setattr(help_mod, "run_open", lambda args: None)
    monkeypatch.setattr(help_mod, "local_permissions",
                        lambda: {"microphone": None, "accessibility": None, "input_monitoring": None})


def make_window(tmp_path, appearance="paper", dark=False, watch=False):
    state = {"appearance": appearance, "dark": dark}
    ctx = HubContext(history_path=tmp_path / "history.sqlite",
                     dictionary_path=tmp_path / "dictionary.json", control=FakeControl())
    win = hub.HubWindow(ctx, geometry_path=tmp_path / "hub.json",
                        appearance=lambda: state["appearance"],
                        system_dark=lambda: state["dark"], watch_config=watch)
    win.resize(1280, 832)
    return win, state


def spin_until(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# -- palettes ------------------------------------------------------------------------

def _hexes(palette) -> set[str]:
    out = set()
    for v in palette.values():
        for c in (v if isinstance(v, tuple) else (v,)):
            out.add(c.upper())
    return out


def test_palettes_define_the_same_tokens():
    assert set(S.PAPER_PALETTE) == set(S.INK_PALETTE)
    for k, v in S.PAPER_PALETTE.items():
        assert type(v) is type(S.INK_PALETTE[k]), k
        if isinstance(v, tuple):
            assert len(v) == len(S.INK_PALETTE[k]), k


def test_paper_keeps_todays_values():
    assert (S.PAPER_PALETTE["PAPER"], S.PAPER_PALETTE["DEEP"], S.PAPER_PALETTE["CARD"],
            S.PAPER_PALETTE["INK"], S.PAPER_PALETTE["MUTED"], S.PAPER_PALETTE["HAIR"]) == \
        ("#FAF7F2", "#F2EEE5", "#F6F2EB", "#1A1814", "#8A7F73", "#E8E2D9")


def test_one_accent_in_both_themes():
    assert S.PAPER_PALETTE["ACCENT"] == S.INK_PALETTE["ACCENT"] == "#E5402F"


def test_apply_theme_rebinds_tokens_and_unknown_is_paper():
    assert S.apply_theme("ink") == "ink"
    assert (S.THEME, S.PAPER, S.INK, S.HEAT) == \
        ("ink", S.INK_PALETTE["PAPER"], S.INK_PALETTE["INK"], S.INK_PALETTE["HEAT"])
    assert S.apply_theme("sepia") == "paper"
    assert (S.THEME, S.PAPER) == ("paper", "#FAF7F2")


@pytest.mark.parametrize("appearance,dark,theme", [
    ("paper", False, "paper"), ("paper", True, "paper"),
    ("ink", False, "ink"), ("ink", True, "ink"),
    ("auto", False, "paper"), ("auto", True, "ink"),
    ("bogus", True, "paper"),
])
def test_theme_follows_the_widget_rule(appearance, dark, theme):
    assert S.theme_for(appearance, dark) == theme


def test_configured_appearance_reads_config_without_writing(tmp_config):
    assert hub.configured_appearance() == "paper"          # default, no file
    assert not (tmp_config / "config.toml").exists()
    cfg_mod.save_setting("widget", "appearance", "ink")
    assert hub.configured_appearance() == "ink"


# -- contrast (WCAG 2.x) -------------------------------------------------------------

def _lum(h: str) -> float:
    h = h.lstrip("#")[-6:]
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (text, background) pairs the pages actually draw.
TEXT_PAIRS = [
    ("INK", "PAPER"), ("INK", "CARD"), ("INK", "DEEP"), ("INK", "ROW_ON"), ("INK", "ROW_HOVER"),
    ("INK", "SEG_ON"), ("INK_SOFT", "PAPER"), ("INK_SOFT", "CARD"), ("INK_SOFT", "ROW_ON"),
    ("MUTED", "PAPER"), ("MUTED", "CARD"), ("MUTED", "DEEP"), ("MUTED", "ROW_ON"),
    ("MUTED", "SEG_TRACK"),
    ("ACCENT_TEXT", "PAPER"), ("ACCENT_TEXT", "CARD"), ("ACCENT_TEXT", "ACCENT_SOFT"),
    ("ACCENT_TEXT", "SEG_ON"), ("ACCENT_TEXT", "BAR_TRACK"),
    ("SAGE_TEXT", "SAGE_SOFT"), ("SAGE_TEXT", "CARD"), ("SAGE", "PAPER"), ("SAGE", "CARD"),
    ("DANGER", "PAPER"), ("DANGER", "CARD"), ("DANGER", "ACCENT_SOFT"),
    ("PAPER", "INK"), ("BANNER_TEXT", "BANNER"), ("BANNER_BODY", "BANNER"),
    ("BANNER", "BANNER_TEXT"),
]
# Paper is today's look, kept as is (brand book's ink-muted and sage); these
# pairs were below 4.5:1 before Ink existed and stay listed so they can't get
# worse. White on the one accent (#E5402F) is 4.1:1 in both themes: it is
# only used on semibold button labels and chart labels, AA for large text/UI.
PAPER_KNOWN_LOW = {("MUTED", "PAPER"), ("MUTED", "CARD"), ("MUTED", "DEEP"),
                   ("MUTED", "ROW_ON"), ("MUTED", "SEG_TRACK"), ("SAGE", "PAPER"),
                   ("SAGE", "CARD")}


@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_ink_text_meets_wcag_aa(fg, bg):
    p = S.INK_PALETTE
    assert contrast(p[fg], p[bg]) >= 4.5, (fg, bg, round(contrast(p[fg], p[bg]), 2))


@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_paper_text_contrast(fg, bg):
    p = S.PAPER_PALETTE
    floor = 3.0 if (fg, bg) in PAPER_KNOWN_LOW else 4.5
    assert contrast(p[fg], p[bg]) >= floor, (fg, bg, round(contrast(p[fg], p[bg]), 2))


@pytest.mark.parametrize("palette", [S.PAPER_PALETTE, S.INK_PALETTE])
def test_white_on_accent_is_large_text_aa(palette):
    assert contrast(palette["ON_ACCENT"], palette["ACCENT"]) >= 3.0


# -- no stray colours ------------------------------------------------------------------

def _hub_sources():
    for path in sorted(HUB_DIR.rglob("*.py")):
        if path.name != "style.py":
            yield path


def test_no_hex_colours_outside_style():
    stray = []
    for path in _hub_sources():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue   # a comment line
            stray += [f"{path.relative_to(HUB_DIR)}:{n}: {m.group(0)}" for m in HEX.finditer(line)]
    assert not stray, "colours belong in ui/hub/style.py:\n" + "\n".join(stray)


def test_no_literal_rgb_or_named_colours_outside_style():
    pat = re.compile(r"QColor\(\s*\d|GlobalColor\.(white|black)|['\"](white|black)['\"]|rgba?\(")
    stray = [f"{p.relative_to(HUB_DIR)}:{n}: {line.strip()}"
             for p in _hub_sources()
             for n, line in enumerate(p.read_text().splitlines(), 1) if pat.search(line)]
    # The one allowed literal: a fully transparent fill for icon pixmaps.
    stray = [s for s in stray if "QColor(0, 0, 0, 0)" not in s]
    assert not stray, "\n".join(stray)


def test_no_colour_token_is_captured_at_import():
    """`X = S.INK` at module level, a class attribute, or a default argument
    keeps the theme that was current at import; read S.INK when building."""
    tokens = set(S.PAPER_PALETTE)
    bad = []

    def refs(node):
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                    and sub.value.id in ("S", "style") and sub.attr in tokens):
                yield sub

    for path in [*_hub_sources(), HUB_DIR / "style.py"]:
        tree = ast.parse(path.read_text())
        scopes = [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        for body in scopes:
            for stmt in body:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    bad += [f"{path.name}:{r.lineno} {r.attr}" for r in refs(stmt)]
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in [*fn.args.defaults, *[k for k in fn.args.kw_defaults if k]]:
                    bad += [f"{path.name}:{r.lineno} {r.attr} (default)" for r in refs(d)]
                    if path.name == "style.py" and isinstance(d, ast.Name) and d.id in tokens:
                        bad.append(f"style.py:{d.lineno} {d.id} (default)")
    assert not bad, "\n".join(bad)


# -- the window -----------------------------------------------------------------------

def _stylesheets(root: QWidget) -> str:
    parts = [root.styleSheet()]
    for w in root.findChildren(QWidget):
        parts.append(w.styleSheet())
        if isinstance(w, QLabel):
            parts.append(w.text())
    return "\n".join(parts).upper()


def test_window_resolves_the_theme_before_building(tmp_path, stubs, tmp_config):
    win, _ = make_window(tmp_path, "ink")
    assert win.theme == S.THEME == "ink"
    assert S.INK_PALETTE["DEEP"] in win.centralWidget().styleSheet()
    win.deleteLater()


def test_every_page_in_ink_uses_no_paper_only_colour(tmp_path, stubs, tmp_config):
    paper_only = _hexes(S.PAPER_PALETTE) - _hexes(S.INK_PALETTE)
    win, _ = make_window(tmp_path, "ink")
    win.show()
    found = {}
    from ui.hub.pages.settings import SECTIONS
    for key, *_ in PAGES + FOOTER_PAGES:
        win.navigate(key)
        page = win._pages[key]
        if key == "insights":
            for i in range(3):
                page.show_tab(i)
        if key == "settings":
            for section, _l in SECTIONS:
                page.select(section)
        _app.processEvents()
        text = _stylesheets(page)
        hits = sorted(c for c in paper_only if c in text)
        if hits:
            found[key] = hits
    sidebar = _stylesheets(win.centralWidget())
    hits = sorted(c for c in paper_only if c in sidebar)
    if hits:
        found["window"] = hits
    win.hide()
    win.deleteLater()
    assert not found, f"Paper colours left in Ink: {found}"


def test_live_switch_rebuilds_pages_and_keeps_the_place(tmp_path, stubs, tmp_config):
    win, state = make_window(tmp_path, "paper")
    win.navigate("insights")
    old = win._pages["insights"]
    old.show_tab(2)
    assert win.sync_theme() is False                 # nothing changed yet
    state["appearance"] = "ink"
    assert win.sync_theme() is True
    assert S.THEME == win.theme == "ink"
    new = win._pages["insights"]
    assert new is not old and win.current_key == "insights"
    assert new.tab == 2                              # still on Reliability
    assert win.nav_rows["insights"].property("on") is True
    assert S.INK_PALETTE["PAPER"] in win.panel.styleSheet()
    assert QApplication.instance().styleSheet() == S.app_qss()

    win.navigate("settings", section="widget")
    state["appearance"] = "paper"
    assert win.sync_theme() is True
    page = win._pages["settings"]
    assert page.stack.currentWidget() is page.sections["widget"]
    assert S.PAPER_PALETTE["PAPER"] in win.panel.styleSheet()
    win.deleteLater()


def test_auto_follows_macos_dark_mode(tmp_path, stubs, tmp_config):
    win, state = make_window(tmp_path, "auto", dark=False)
    win.navigate("home")
    assert win.theme == "paper"
    state["dark"] = True
    win._sync_theme_later()                          # what colorSchemeChanged does
    assert spin_until(lambda: win.theme == "ink")
    assert win.current_key == "home" and S.THEME == "ink"
    win.deleteLater()


def test_settings_appearance_switches_the_window(tmp_path, stubs, tmp_config):
    ctx = HubContext(history_path=tmp_path / "history.sqlite",
                     dictionary_path=tmp_path / "dictionary.json", control=FakeControl())
    win = hub.HubWindow(ctx, geometry_path=tmp_path / "hub.json",
                        system_dark=lambda: False, watch_config=False)   # real config source
    win.navigate("settings", section="widget")
    page = win._pages["settings"]
    page.appearance.click_value("ink")               # the user's click, deleted by the rebuild
    assert cfg_mod.read()["widget"]["appearance"] == "ink"
    assert spin_until(lambda: win.theme == "ink")
    new = win._pages["settings"]
    assert new is not page
    assert new.stack.currentWidget() is new.sections["widget"]
    assert new.appearance.value() == "ink"
    win.deleteLater()


def test_config_change_from_the_widget_menu_switches_the_window(tmp_path, stubs, tmp_config):
    cfg_mod.save_setting("widget", "appearance", "paper")
    ctx = HubContext(history_path=tmp_path / "history.sqlite",
                     dictionary_path=tmp_path / "dictionary.json", control=FakeControl())
    win = hub.HubWindow(ctx, geometry_path=tmp_path / "hub.json",
                        system_dark=lambda: False, watch_config=True)
    win.navigate("home")
    assert win.theme == "paper"
    cfg_mod.save_setting("widget", "appearance", "ink")   # daemon's set_appearance path
    assert spin_until(lambda: win.theme == "ink", timeout=5.0)
    win.deleteLater()


def test_theme_switch_failure_is_logged_not_raised(tmp_path, stubs, tmp_config, monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "log_exception", lambda *a, **k: calls.append(a))
    win, state = make_window(tmp_path, "paper")

    def boom():
        raise RuntimeError("nope")
    win._appearance = boom
    assert win.sync_theme() is False
    assert calls and win.theme == "paper"
    win.deleteLater()
