"""Daemon <-> flow widget channel (spec section 6).

Newline-delimited JSON over a Unix domain socket. The daemon runs a
WidgetServer; the widget process connects with a WidgetClient. A live
connection is the liveness signal for both sides (no polling, no pgrep).
"""
from __future__ import annotations

import errno
import json
import os
import select
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from openflow_logger import log_exception

SOCKET_PATH = str(Path(os.path.expanduser("~/.openflow")) / "widget.sock")

# send() may run under the FlowController lock, so a hung peer must never
# stall it for long. The timeout is per-socket; the reader treats a recv
# timeout as "idle" and keeps waiting.
SEND_TIMEOUT = 1.0

Handler = Callable[[dict], None]


class _Conn:
    """One socket: a reader thread that parses lines, and a locked writer."""

    def __init__(self, sock: socket.socket, on_message: Handler,
                 on_close: Callable[["_Conn"], None]) -> None:
        sock.settimeout(SEND_TIMEOUT)
        self.sock = sock
        self.alive = True
        self._on_message = on_message
        self._on_close = on_close
        self._lock = threading.Lock()
        threading.Thread(target=self._read, name="widget-channel-read", daemon=True).start()

    def send(self, msg: dict) -> bool:
        """Never raises. On any socket error (incl. timeout) the connection is closed."""
        if not self.alive:
            return False
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            # Nothing was written, so the connection is still healthy.
            log_exception("widget_channel", "unserializable message dropped", exc)
            return False
        try:
            with self._lock:
                self.sock.sendall(data)
            return True
        except OSError as exc:
            # A timed-out sendall may have written a partial line, so the
            # stream is unusable: close rather than keep it.
            if self.alive:
                log_exception("widget_channel", "send failed; closing connection", exc)
            self.close()
            return False

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def _read(self) -> None:
        buf = b""
        try:
            while self.alive:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    continue  # idle; no data is lost on a recv timeout
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        try:
                            self._on_message(msg)
                        except Exception as exc:  # a handler bug must not drop the link
                            log_exception("widget_channel", "message handler raised", exc)
        except OSError:
            pass
        finally:
            self.close()
            self._on_close(self)


class WidgetServer:
    def __init__(self, path: str = SOCKET_PATH, on_message: Optional[Handler] = None,
                 on_connect: Optional[Callable[[], None]] = None) -> None:
        self.path = path
        self._on_message = on_message
        self._on_connect = on_connect
        self._conn: Optional[_Conn] = None
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._stopped = threading.Event()
        self._ino: Optional[int] = None

    def start(self) -> None:
        Path(self.path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._clear_stale_path()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # socket is created 0600, never wider
        try:
            s.bind(self.path)
        finally:
            os.umask(old_umask)
        self._ino = os.stat(self.path).st_ino
        s.listen(2)
        self._sock = s
        threading.Thread(target=self._accept_loop, name="widget-channel-accept",
                         daemon=True).start()

    def _clear_stale_path(self) -> None:
        """Refuse to steal a live server's path; unlink it only if stale."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(self.path)
        except FileNotFoundError:
            return
        except ConnectionRefusedError:
            os.unlink(self.path)  # nobody listening: stale
            return
        finally:
            probe.close()
        # The probe connects and closes without sending; _accept_loop ignores
        # such connections, so it does not evict the live server's widget.
        raise OSError(errno.EADDRINUSE, f"widget server already running at {self.path}")

    @staticmethod
    def _is_probe(client: socket.socket) -> bool:
        """True if the peer closed right after connecting (a liveness probe).

        A real widget never closes without talking; it stays connected, so
        the short wait times out and it is treated as real.
        """
        try:
            readable, _, _ = select.select([client], [], [], 0.05)
            return bool(readable) and client.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError as exc:
                if self._stopped.is_set():
                    break
                log_exception("widget_channel", "accept failed; retrying", exc)
                time.sleep(0.1)
                continue
            if self._is_probe(client):
                client.close()
                continue
            conn = _Conn(client, self._dispatch, self._closed)
            with self._lock:
                old, self._conn = self._conn, conn
            if old is not None:
                old.send({"type": "exit"})
                old.close()
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception as exc:
                    log_exception("widget_channel", "on_connect raised", exc)

    def _dispatch(self, msg: dict) -> None:
        if self._on_message is not None:
            self._on_message(msg)

    def _closed(self, conn: _Conn) -> None:
        with self._lock:
            if self._conn is conn:
                self._conn = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._conn is not None and self._conn.alive

    def send(self, msg: dict) -> bool:
        with self._lock:
            conn = self._conn
        return conn.send(msg) if conn is not None else False

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
        if self._sock is not None:
            self._sock.close()
        try:
            # Only remove the path if it is still the socket we bound.
            if self._ino is not None and os.stat(self.path).st_ino == self._ino:
                os.unlink(self.path)
        except OSError:
            pass


class WidgetClient:
    def __init__(self, path: str = SOCKET_PATH, on_message: Optional[Handler] = None,
                 on_disconnect: Optional[Callable[[], None]] = None) -> None:
        self.path = path
        self._on_message = on_message or (lambda _msg: None)
        self._on_disconnect = on_disconnect
        self._conn: Optional[_Conn] = None

    def connect(self) -> bool:
        if self.connected:
            return True
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return False
        self._conn = _Conn(s, self._on_message, self._closed)
        return True

    def _closed(self, conn: _Conn) -> None:
        if self._conn is conn:
            self._conn = None
            if self._on_disconnect is not None:
                self._on_disconnect()

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.alive

    def send(self, msg: dict) -> bool:
        conn = self._conn
        return conn.send(msg) if conn is not None else False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
