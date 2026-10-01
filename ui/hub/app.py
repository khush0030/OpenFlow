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

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer, pyqtSignal
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

MARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">'
    f'<circle cx="13" cy="13" r="11" fill="none" stroke="{style.INK}" stroke-width="1.6" stroke-dasharray="52 17"/>'
    f'<circle cx="13" cy="13" r="6.5" fill="none" stroke="{style.INK}" stroke-width="1.6" stroke-dasharray="30 11"/>'
    f'<circle cx="13" cy="13" r="2.6" fill="{style.ACCENT}"/></svg>'
)


def icon_svg(name: str, color: str = style.INK, stroke: float = 1.7) -> str:
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
        self.icon.setPixmap(svg_pixmap(icon_svg(key), 18, _dpr()))
        self.icon.setStyleSheet("background:transparent;")
        self.label = QLabel(label)
        self.label.setObjectName("navlabel")
        self.label.setStyleSheet(f"color:{style.INK};background:transparent;")
        self.label.setFont(style.sans(14.5))
        row.addWidget(self.icon)
        row.addWidget(self.label, 1)
        self.setAccessibleName(label)

    def set_on(self, on: bool) -> None:
        if self.property("on") == on:
            return
        self.setProperty("on", on)
        self.label.setFont(style.sans(14.5, 600 if on else 400))
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
    mark.setPixmap(svg_pixmap(MARK_SVG, 26, _dpr()))
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
                 geometry_path: Path | None = None, dock=None) -> None:
        super().__init__()
        self.ctx = ctx or HubContext()
        self.ctx.navigate = self.navigate
        self.dock = dock
        self.geometry_path = Path(geometry_path) if geometry_path else HUB_GEOMETRY
        self._registry: dict[str, tuple[str, str, str]] = {}
        self._pages: dict[str, QWidget] = {}
        self.nav_rows: dict[str, NavRow] = {}
        self.current_key: str | None = None

        self.setWindowTitle("OpenFlow")
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*DEFAULT_SIZE)
        self.setUnifiedTitleAndToolBarOnMac(True)

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
        self.setCentralWidget(root)

        QShortcut(QKeySequence(QKeySequence.StandardKey.Close), self, activated=self.close)
        self._restore_geometry()

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
        img = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if img is not None:
            app.setApplicationIconImage_(img)

    def set_accessory(self) -> None:
        from AppKit import NSApplicationActivationPolicyAccessory  # type: ignore
        self._app().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def activate(self) -> None:
        self._app().activateIgnoringOtherApps_(True)


class DockPresence:
    """Dock icon and ⌘-Tab only while the window is visible; quit the
    process after `quit_after_ms` hidden."""

    def __init__(self, appkit, quit: Callable[[], None], icon_path: Path = DOCK_ICON,
                 quit_after_ms: int = QUIT_AFTER_HIDDEN_MS) -> None:
        self.appkit = appkit
        self.icon_path = icon_path
        self.quit_timer = QTimer()
        self.quit_timer.setSingleShot(True)
        self.quit_timer.setInterval(quit_after_ms)
        self.quit_timer.timeout.connect(quit)

    def shown(self) -> None:
        self.quit_timer.stop()
        try:
            self.appkit.set_regular(self.icon_path)
            self.appkit.activate()
        except Exception as e:
            log_exception("hub", "could not show the Dock icon", e)

    def hidden(self) -> None:
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

    def __init__(self, sock_path: Path, on_show: Callable[[str], None]) -> None:
        self.on_show = on_show
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
                page = json.loads(line).get("show")
            except Exception as e:
                log_exception("hub", f"bad hub.sock message {line[:80]!r}", e)
                continue
            try:
                self.on_show(page if isinstance(page, str) and page else "home")
            except Exception as e:
                log_exception("hub", "show request failed", e)

    def close(self) -> None:
        self.server.close()


# ── entry point ───────────────────────────────────────────────────────────
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
    win.dock = DockPresence(AppKitBridge(), quit=app.quit)
    server = HubServer(sock_path, lambda p: win.present(p))
    app.aboutToQuit.connect(win.save_geometry)
    win.present(page)
    try:
        return app.exec()
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "home"))
