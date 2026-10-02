"""The hub main window: sidebar + lazily built pages (spec §3–§4).

Run as its own process (`openflow hub [page]`); rumps and PyQt can't share an
NSApp. Single instance: the first process listens on ~/.openflow/hub.sock and
a later launch hands its page over there and exits. The window owns a Dock
icon only while it is visible, and the process quits after a minute hidden.
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Callable, Iterable

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QEvent, QFileSystemWatcher, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow,
                             QStackedWidget, QVBoxLayout, QWidget)

import config as cfg_mod
from openflow_logger import log_exception
from ui.hub import style
from ui.hub.context import HubContext
from ui.hub.page import FOOTER_PAGES, PAGES, Page

HUB_SOCK = cfg_mod.CONFIG_DIR / "hub.sock"
HUB_GEOMETRY = cfg_mod.CONFIG_DIR / "hub.json"
_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
DOCK_ICON = _ASSETS / "logo" / "icon.png"

DEFAULT_SIZE = (1280, 832)
MIN_SIZE = (980, 640)
SIDEBAR_W = 236
QUIT_AFTER_HIDDEN_MS = 60_000

# ── artwork (mockup common.py: 24-unit stroked glyphs, 26-unit mark) ─────────
_ICON_PATHS = {
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v9h12v-9"/>',
    "insights": '<path d="M5 19V11"/><path d="M10 19V5"/><path d="M15 19v-6"/><path d="M20 19V8"/>',
    "history": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "dictionary": '<path d="M6 4h10a2 2 0 0 1 2 2v14H8a2 2 0 0 1-2-2z"/><path d="M6 18a2 2 0 0 1 2-2h10"/>',
    "tones": '<path d="M5 7h14"/><path d="M5 12h10"/><path d="M5 17h6"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1'
                'M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "help": '<circle cx="12" cy="12" r="8"/><path d="M9.8 9.5a2.3 2.3 0 1 1 3.2 2.1c-.6.3-1 .8-1 1.4v.5"/>'
            '<path d="M12 16.5v.01"/>',
}

def mark_svg() -> str:
    """The OpenFlow mark: Ink arcs (Paper on the Ink theme), accent dot."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">'
        f'<circle cx="13" cy="13" r="11" fill="none" stroke="{style.INK}" stroke-width="1.6" stroke-dasharray="52 17"/>'
        f'<circle cx="13" cy="13" r="6.5" fill="none" stroke="{style.INK}" stroke-width="1.6" stroke-dasharray="30 11"/>'
        f'<circle cx="13" cy="13" r="2.6" fill="{style.ACCENT}"/></svg>'
    )


MARK_SVG = mark_svg()   # Paper; the first-run window uses it


def icon_svg(name: str, color: str | None = None, stroke: float = 1.7) -> str:
    color = color or style.INK
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{_ICON_PATHS.get(name, "")}</svg>')


def svg_pixmap(svg: str, size: int, dpr: float = 2.0) -> QPixmap:
    """Render SVG markup crisply at `size` logical px."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(QColor(0, 0, 0, 0))
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(2.0, screen.devicePixelRatio() if screen else 2.0)


# ── theme sources ─────────────────────────────────────────────────────────
def configured_appearance() -> str:
    """[widget] appearance from config.toml (read-only: never rewrites it)."""
    return str(cfg_mod.read().get("widget", {}).get("appearance", "paper"))


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_app_theme() -> None:
    """Tooltips, menus, combo popups and natively drawn bits follow the
    theme too (they aren't children of any page)."""
    app = QApplication.instance()
    if app is None:
        return
    try:
        # Re-theming re-polishes every live widget: only when it changes.
        qss = style.app_qss()
        if app.styleSheet() != qss:
            app.setPalette(style.palette())
            app.setStyleSheet(qss)
    except Exception as e:
        log_exception("hub", "could not theme the application", e)


# ── sidebar ───────────────────────────────────────────────────────────────
class NavRow(QFrame):
    """One sidebar row: 18 pt icon, 12 pt gap, Geist 14.5 label. Selected rows
    are ROW_ON and semibold; hovered rows ROW_HOVER; radius 9."""

    clicked = pyqtSignal()

    def __init__(self, key: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("navrow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("on", False)
        self.setProperty("hover", False)
        self.setStyleSheet(
            "QFrame#navrow{background:transparent;border-radius:9px;}"
            f'QFrame#navrow[hover="true"]{{background:{style.ROW_HOVER};}}'
            f'QFrame#navrow[on="true"]{{background:{style.ROW_ON};}}')
        self.setFixedHeight(36)  # 9 + 18 + 9: rows sit on a 40 pt pitch
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        self.icon.setPixmap(svg_pixmap(icon_svg(key, style.INK_SOFT), 18, _dpr()))
        self.icon.setStyleSheet("background:transparent;")
        self.label = QLabel(label)
        self.label.setObjectName("navlabel")
        self.label.setStyleSheet(f"color:{style.INK};background:transparent;")
        self.label.setFont(style.sans(14))
        row.addWidget(self.icon)
        row.addWidget(self.label, 1)
        self.setAccessibleName(label)

    def set_on(self, on: bool) -> None:
        if self.property("on") == on:
            return
        self.setProperty("on", on)
        self.label.setFont(style.sans(14, 600 if on else 400))
        self.icon.setPixmap(svg_pixmap(icon_svg(self.key, style.INK if on else style.INK_SOFT,
                                                1.9 if on else 1.7), 18, _dpr()))
        self._repolish()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.setProperty("hover", True)
        self._repolish()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.setProperty("hover", False)
        self._repolish()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def _brand_row() -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(10, 0, 10, 22)
    row.setSpacing(10)
    mark = QLabel()
    mark.setFixedSize(26, 26)
    mark.setPixmap(svg_pixmap(mark_svg(), 26, _dpr()))
    word = QLabel("OpenFlow")
    f = style.serif(22)
    f.setWeight(QFont.Weight(500))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 99)
    word.setFont(f)
    word.setStyleSheet(f"color:{style.INK};")
    row.addWidget(mark)
    row.addWidget(word)
    row.addStretch(1)
    return w


class PlaceholderPage(Page):
    """Shown for a page whose module is missing or failed to build."""

    def __init__(self, ctx: HubContext, label: str) -> None:
        super().__init__(ctx)
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 40, 40, 40)
        col.addStretch(1)
        title = QLabel(f"{label} is on its way")
        title.setFont(style.serif(28))
        title.setStyleSheet(f"color:{style.INK};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = style.muted("This page is still being built. Everything else keeps working.", 14)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        col.addSpacing(8)
        col.addWidget(sub)
        col.addStretch(1)


# ── window ────────────────────────────────────────────────────────────────
class HubWindow(QMainWindow):
    def __init__(self, ctx: HubContext | None = None, *,
                 pages: Iterable[tuple] = PAGES, footer_pages: Iterable[tuple] = FOOTER_PAGES,
                 geometry_path: Path | None = None, dock=None,
                 appearance: Callable[[], str] | None = None,
                 system_dark: Callable[[], bool] | None = None,
                 watch_config: bool = True) -> None:
        super().__init__()
        self.ctx = ctx or HubContext()
        self.ctx.navigate = self.navigate
        self.ctx.apply_theme = self._sync_theme_later
        self.dock = dock
        self.geometry_path = Path(geometry_path) if geometry_path else HUB_GEOMETRY
        self._page_entries = (tuple(pages), tuple(footer_pages))
        self._registry: dict[str, tuple[str, str, str]] = {}
        self._pages: dict[str, QWidget] = {}
        self.nav_rows: dict[str, NavRow] = {}
        self.current_key: str | None = None
        self._last_kwargs: dict = {}
        # Theme (spec 2026-10-02-dark-hub): follows [widget] appearance —
        # paper, ink, or auto = macOS. Resolved before anything is built.
        self._appearance = appearance or configured_appearance
        self._system_dark = system_dark or system_is_dark
        self.theme = style.apply_theme(self.wanted_theme())
        apply_app_theme()

        self.setWindowTitle("OpenFlow")
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*DEFAULT_SIZE)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build()

        QShortcut(QKeySequence(QKeySequence.StandardKey.Close), self, activated=self.close)
        self._restore_geometry()
        self._watch_theme(watch_config)

    def _build(self) -> None:
        """Sidebar + panel + an empty page stack, in the current theme."""
        pages, footer_pages = self._page_entries
        self._registry.clear()
        self._pages.clear()
        self.nav_rows.clear()
        root = QWidget()
        root.setObjectName("hubroot")
        root.setStyleSheet(f"QWidget#hubroot{{background:{style.DEEP};}}")
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(SIDEBAR_W)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 16, 14, 18)
        side.setSpacing(4)
        side.addSpacing(36)  # traffic lights sit here (full-size content view)
        side.addWidget(_brand_row())
        for entry in pages:
            side.addWidget(self._add_row(entry))
        side.addStretch(1)
        hair_box = QWidget()
        hb = QVBoxLayout(hair_box)
        hb.setContentsMargins(6, 8, 6, 8)
        hair = QFrame()
        hair.setFixedHeight(1)
        hair.setStyleSheet(f"background:{style.HAIR};border:none;")
        hb.addWidget(hair)
        side.addWidget(hair_box)
        for entry in footer_pages:
            side.addWidget(self._add_row(entry))
        body.addWidget(self.sidebar)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 12, 12, 12)
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet(f"QFrame#panel{{background:{style.PAPER};border:1px solid {style.HAIR};"
                                 f"border-radius:14px;}}")
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(1, 1, 1, 1)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget{background:transparent;}")
        pl.addWidget(self.stack)
        cl.addWidget(self.panel)
        body.addWidget(content, 1)
        self.setCentralWidget(root)   # deletes the previous one (and its pages)

    # theme
    def wanted_theme(self) -> str:
        try:
            return style.theme_for(self._appearance(), bool(self._system_dark()))
        except Exception as e:
            log_exception("hub", "could not resolve the appearance", e)
            return style.THEME

    def sync_theme(self) -> bool:
        """Switch to the theme the setting / macOS ask for, rebuilding the
        window and its pages (the current page is shown again). True when
        the theme changed. Safe to call any time; never raises."""
        try:
            want = self.wanted_theme()
            if want == self.theme:
                return False
            key, kwargs = self.current_key, self._last_kwargs
            view: dict = {}
            page = self._pages.get(key) if key else None
            if page is not None and hasattr(page, "view_state"):
                try:
                    view = page.view_state() or {}
                except Exception as e:
                    log_exception("hub", f"page {key!r} view_state() failed", e)
            self.theme = style.apply_theme(want)
            # Drop the old pages first: re-theming the application re-polishes
            # every live widget, and the old ones are about to go anyway.
            # (Always reached from a timer or a direct call, never from a
            # signal of a widget inside the window, so deleting now is safe.)
            old = self.takeCentralWidget()
            self._pages.clear()
            if old is not None:
                old.hide()
                sip.delete(old)
            apply_app_theme()
            self.current_key = None
            self._build()
            if key:
                self.navigate(key, **kwargs)
                page = self._pages.get(key)
                if view and page is not None and hasattr(page, "restore_view"):
                    try:
                        page.restore_view(view)
                    except Exception as e:
                        log_exception("hub", f"page {key!r} restore_view() failed", e)
            return True
        except Exception as e:
            log_exception("hub", "theme switch failed", e)
            return False

    def _sync_theme_later(self, *_a) -> None:
        # Deferred: the signal may come from a widget the rebuild deletes
        # (Settings › Appearance), and file events arrive in bursts.
        self._theme_timer.start()

    def _watch_theme(self, watch_config: bool) -> None:
        self._theme_timer = QTimer(self)
        self._theme_timer.setSingleShot(True)
        self._theme_timer.setInterval(150)
        self._theme_timer.timeout.connect(self.sync_theme)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._sync_theme_later)
        except Exception as e:  # Qt < 6.5
            log_exception("hub", "no colour-scheme signal", e)
        self._watcher = None
        if watch_config:
            # The widget's menu changes [widget] appearance via the daemon;
            # config.toml is replaced atomically, so watch its folder too.
            self._watcher = QFileSystemWatcher(self)
            path = cfg_mod.CONFIG_PATH
            self._watcher.addPaths([str(p) for p in (path, path.parent) if p.exists()])
            self._watcher.fileChanged.connect(self._config_touched)
            self._watcher.directoryChanged.connect(self._config_touched)

    def _config_touched(self, *_a) -> None:
        try:
            path = str(cfg_mod.CONFIG_PATH)
            if self._watcher is not None and path not in self._watcher.files() \
                    and os.path.exists(path):
                self._watcher.addPath(path)
        except Exception as e:
            log_exception("hub", "config watch failed", e)
        self._sync_theme_later()

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        # Belt and braces: re-check when the window comes forward.
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._sync_theme_later()

    def _add_row(self, entry: tuple) -> NavRow:
        key, label, module, cls = entry
        self._registry[key] = (label, module, cls)
        row = NavRow(key, label)
        row.clicked.connect(lambda k=key: self.navigate(k))
        self.nav_rows[key] = row
        return row

    # pages
    def _page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]
        label, module, cls = self._registry[key]
        try:
            page = getattr(importlib.import_module(module), cls)(self.ctx)
        except Exception as e:  # missing module/class, or the page raised
            log_exception("hub", f"page {key!r} ({module}.{cls}) unavailable", e)
            page = PlaceholderPage(self.ctx, label)
        page.key = key
        self._pages[key] = page
        self.stack.addWidget(page)
        return page

    def navigate(self, key: str, **kwargs) -> None:
        """Show page `key` (building it on first use) and call its shown()."""
        if key not in self._registry:
            log_exception("hub", f"unknown page {key!r}")
            return
        page = self._page(key)
        self.stack.setCurrentWidget(page)
        self.current_key = key
        self._last_kwargs = dict(kwargs)
        for k, row in self.nav_rows.items():
            row.set_on(k == key)
        try:
            page.shown(**kwargs)
        except Exception as e:
            log_exception("hub", f"page {key!r} shown() failed", e)

    def present(self, key: str | None = None, **kwargs) -> None:
        """Bring the window forward (Dock icon on) and show a page."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        apply_unified_titlebar(self)
        self.raise_()
        self.activateWindow()
        if self.dock is not None:
            self.dock.shown()
        if key or self.current_key is None:
            self.navigate(key if key in self._registry else "home", **kwargs)

    # geometry
    def _restore_geometry(self) -> None:
        try:
            g = json.loads(self.geometry_path.read_text())
            x, y, w, h = (int(g[k]) for k in ("x", "y", "w", "h"))
        except FileNotFoundError:
            return
        except Exception as e:
            log_exception("hub", f"ignoring bad {self.geometry_path}", e)
            return
        self.resize(max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1]))
        screens = QGuiApplication.screens()
        if not screens or any(s.availableGeometry().contains(x + 40, y + 20) for s in screens):
            self.move(x, y)

    def save_geometry(self) -> None:
        g = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        pos = self.pos()
        data = {"x": pos.x(), "y": pos.y(), "w": g.width(), "h": g.height()}
        try:
            self.geometry_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.geometry_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.geometry_path)
        except Exception as e:
            log_exception("hub", "could not save window geometry", e)

    def closeEvent(self, e) -> None:  # noqa: N802
        """Closing hides: the process lingers a minute so reopening is instant."""
        self.save_geometry()
        e.ignore()
        self.hide()
        if self.dock is not None:
            self.dock.hidden()


# ── Dock presence ─────────────────────────────────────────────────────────
class AppKitBridge:
    """The three AppKit calls DockPresence needs. Tests use a fake."""

    def _app(self):
        from AppKit import NSApplication  # type: ignore
        return NSApplication.sharedApplication()

    def set_regular(self, icon_path) -> None:
        from AppKit import NSApplicationActivationPolicyRegular, NSImage  # type: ignore
        app = self._app()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        # Inside OpenFlow.app the Dock already has the bundle's .icns. A
        # runtime image replaces it and gets tinted by the Dock's tinted icon
        # style, so only set one when running from source (no bundle icon).
        if getattr(sys, "frozen", False):
            return
        img = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if img is not None:
            app.setApplicationIconImage_(img)

    def set_accessory(self) -> None:
        from AppKit import NSApplicationActivationPolicyAccessory  # type: ignore
        self._app().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def activate(self) -> None:
        self._app().activateIgnoringOtherApps_(True)


class DockPresence:
    """Dock icon and ⌘-Tab only while the window is visible (and Settings ›
    Show in Dock is on); quit the process after `quit_after_ms` hidden."""

    def __init__(self, appkit, quit: Callable[[], None], icon_path: Path = DOCK_ICON,
                 quit_after_ms: int = QUIT_AFTER_HIDDEN_MS,
                 wanted: Callable[[], bool] = lambda: True) -> None:
        self.appkit = appkit
        self.icon_path = icon_path
        self.wanted = wanted
        self.visible = False
        self.quit_timer = QTimer()
        self.quit_timer.setSingleShot(True)
        self.quit_timer.setInterval(quit_after_ms)
        self.quit_timer.timeout.connect(quit)

    def shown(self) -> None:
        self.quit_timer.stop()
        self.visible = True
        try:
            self.apply()
            self.appkit.activate()
        except Exception as e:
            log_exception("hub", "could not show the Dock icon", e)

    def apply(self) -> None:
        """Set the activation policy from the current state and setting."""
        try:
            want = self.visible and bool(self.wanted())
        except Exception as e:
            log_exception("hub", "could not read show_in_dock", e)
            want = self.visible
        try:
            if want:
                self.appkit.set_regular(self.icon_path)
            else:
                self.appkit.set_accessory()
        except Exception as e:
            log_exception("hub", "could not update the Dock icon", e)

    def hidden(self) -> None:
        self.visible = False
        try:
            self.appkit.set_accessory()
        except Exception as e:
            log_exception("hub", "could not hide the Dock icon", e)
        self.quit_timer.start()


def apply_unified_titlebar(win: QWidget) -> bool:
    """Traffic lights over the sidebar: transparent, title-less titlebar and
    a full-size content view. macOS only; a no-op elsewhere and offscreen."""
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return False
    try:
        import objc  # type: ignore
        from AppKit import NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden  # type: ignore
        nswin = objc.objc_object(c_void_p=int(win.winId())).window()
        if nswin is None:
            return False
        nswin.setTitlebarAppearsTransparent_(True)
        nswin.setTitleVisibility_(NSWindowTitleHidden)
        nswin.setStyleMask_(nswin.styleMask() | NSWindowStyleMaskFullSizeContentView)
        return True
    except Exception as e:
        log_exception("hub", "unified titlebar failed", e)
        return False


# ── single instance ───────────────────────────────────────────────────────
def send_show(sock_path: Path, page: str, timeout: float = 1.0) -> bool:
    """Ask a running hub to show `page`. False when no hub is listening."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock_path))
            s.sendall((json.dumps({"show": page}) + "\n").encode())
        return True
    except OSError:
        return False


class HubServer:
    """Listens for {"show": page} lines from later launches."""

    def __init__(self, sock_path: Path, on_show: Callable[[str], None],
                 on_quit: Callable[[], None] | None = None,
                 on_raise: Callable[[], None] | None = None) -> None:
        self.on_show = on_show
        self.on_quit = on_quit      # {"quit": true}: OpenFlow quit from the Dock / ⌘Q
        self.on_raise = on_raise    # {"raise": true}: Dock click, keep the current page
        self.path = str(sock_path)
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        self.server = QLocalServer()
        QLocalServer.removeServer(self.path)  # nobody answered: stale file
        self.listening = self.server.listen(self.path)
        if not self.listening:
            log_exception("hub", f"cannot listen on {self.path}: {self.server.errorString()}")
        self.server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        # A request is one short line sent right after connecting, so read it
        # synchronously (bounded wait) instead of wiring per-socket signals.
        while self.server.hasPendingConnections():
            conn: QLocalSocket = self.server.nextPendingConnection()
            buf = bytearray()
            while b"\n" not in buf:
                if not conn.bytesAvailable() and not conn.waitForReadyRead(500):
                    break
                buf.extend(bytes(conn.readAll()))
            conn.disconnectFromServer()
            conn.deleteLater()
            self._handle(bytes(buf))

    def _handle(self, data: bytes) -> None:
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                page = msg.get("show")
            except Exception as e:
                log_exception("hub", f"bad hub.sock message {line[:80]!r}", e)
                continue
            try:
                if msg.get("quit"):
                    if self.on_quit is not None:
                        self.on_quit()
                elif msg.get("raise"):
                    (self.on_raise or (lambda: self.on_show("")))()
                else:
                    self.on_show(page if isinstance(page, str) and page else "home")
            except Exception as e:
                log_exception("hub", "show request failed", e)

    def close(self) -> None:
        self.server.close()


# ── entry point ───────────────────────────────────────────────────────────
def _daemon_running() -> bool:
    """True when the menu bar app holds its single-instance lock."""
    import cli
    fd = cli.acquire_daemon_lock(cli.DAEMON_LOCK)
    if fd is None:
        return True
    cli.release_daemon_lock(fd)
    return False


def _show_in_dock(daemon_running: Callable[[], bool] = _daemon_running) -> bool:
    """Should this window process have its own Dock icon? With Show in Dock
    on, the running menu bar app already owns the OpenFlow Dock icon (and
    quitting it closes this window too), so the window doesn't add a second
    one. Only when OpenFlow itself isn't running does the window show it."""
    import config as cfg_mod
    if not bool(cfg_mod.load().get("hub", {}).get("show_in_dock", True)):
        return False
    return not daemon_running()


def main(page: str = "home", *, sock_path: Path | None = None,
         geometry_path: Path | None = None) -> int:
    sock_path = Path(sock_path) if sock_path else HUB_SOCK
    if send_show(sock_path, page):
        return 0  # a hub is already open; it shows the page

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from ui.fonts import load_fonts
    load_fonts()
    app.setFont(style.sans(13))

    win = HubWindow(geometry_path=geometry_path)
    win.dock = DockPresence(AppKitBridge(), quit=app.quit, wanted=_show_in_dock)
    win.ctx.apply_dock = win.dock.apply
    server = HubServer(sock_path, lambda p: win.present(p), on_quit=app.quit,
                       on_raise=lambda: win.present(None))
    app.aboutToQuit.connect(win.save_geometry)
    win.present(page)
    try:
        return app.exec()
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "home"))
