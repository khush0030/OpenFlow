"""Hub -> daemon control socket (app-hub spec §3).

Newline-delimited JSON over ~/.openflow/control.sock. Unlike the widget
channel this is request/reply and serves many clients at once: every
request {"id": n, "cmd": "...", ...args} gets exactly one reply,
{"id": n, "ok": true, ...fields} or {"id": n, "ok": false, "error": "..."}.
The daemon passes a handler table {cmd: fn(**args) -> dict | None}; each
connection is served on its own thread, so a slow `rerun` (a Sarvam call)
never holds up a `status` poll.
"""
from __future__ import annotations

import errno
import fcntl
import inspect
import itertools
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from openflow_logger import log_exception

SOCKET_PATH = str(Path(os.path.expanduser("~/.openflow")) / "control.sock")

DEFAULT_TIMEOUT = 5.0
# One request line may carry a whole dictation (paste_text / rerun); cap it
# so a runaway client can't grow the buffer without bound.
MAX_LINE = 1 << 20

Handler = Callable[..., Optional[dict]]


class ControlError(Exception):
    """The daemon answered with an error, or the call failed."""


class DaemonNotRunning(ControlError):
    """Nothing is listening on the control socket."""

    def __init__(self, msg: str = "OpenFlow isn't running") -> None:
        super().__init__(msg)


class ControlServer:
    def __init__(self, handlers: Mapping[str, Handler], path: str = SOCKET_PATH) -> None:
        self.path = path
        self._handlers = dict(handlers)
        self._sock: Optional[socket.socket] = None
        self._stopped = threading.Event()
        self._conns: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._ino: Optional[int] = None
        self._lock_fd: Optional[int] = None

    # -- lifecycle (same stale-file / 0600 / single-owner rules as widget_channel)

    def start(self) -> None:
        Path(self.path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._acquire_lock()
        try:
            # Holding the lock means any existing socket file is stale.
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # umask is process-wide: it is held only around bind().
            old_umask = os.umask(0o177)  # socket is created 0600, never wider
            try:
                s.bind(self.path)
            finally:
                os.umask(old_umask)
        except BaseException:
            self._release_lock()
            raise
        self._ino = os.stat(self.path).st_ino
        s.listen(64)   # the hub can fire several calls at once
        self._sock = s
        threading.Thread(target=self._accept_loop, name="control-accept",
                         daemon=True).start()

    def _acquire_lock(self) -> None:
        fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise OSError(errno.EADDRINUSE,
                              f"control server already running at {self.path}") from exc
            raise
        self._lock_fd = fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # closing drops the flock
            self._lock_fd = None

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            conns, self._conns = self._conns, set()
        for c in conns:
            _close(c)
        if self._sock is not None:
            self._sock.close()
        try:
            # Only remove the path if it is still the socket we bound.
            if self._ino is not None and os.stat(self.path).st_ino == self._ino:
                os.unlink(self.path)
        except OSError:
            pass
        self._release_lock()

    # -- serving

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError as exc:
                if self._stopped.is_set():
                    break
                log_exception("control_channel", "accept failed; retrying", exc)
                time.sleep(0.1)
                continue
            with self._lock:
                if self._stopped.is_set():
                    _close(client)
                    break
                self._conns.add(client)
            threading.Thread(target=self._serve, args=(client,),
                             name="control-conn", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while not self._stopped.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        conn.sendall(_encode(self.handle_line(line)))
                if len(buf) > MAX_LINE:
                    conn.sendall(_encode({"id": None, "ok": False,
                                          "error": "request too large"}))
                    break
        except OSError:
            pass  # client went away, or stop() closed us
        except Exception as exc:  # never let a bug kill the thread noisily
            log_exception("control_channel", "connection handler crashed", exc)
        finally:
            with self._lock:
                self._conns.discard(conn)
            _close(conn)

    def handle_line(self, line: bytes) -> dict:
        """One request line -> one reply dict. Never raises."""
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        if not isinstance(req, dict):
            return {"id": None, "ok": False, "error": "malformed request"}
        rid = req.get("id")
        cmd = req.get("cmd")
        fn = self._handlers.get(cmd) if isinstance(cmd, str) else None
        if fn is None:
            return {"id": rid, "ok": False, "error": f"unknown command: {cmd!r}"}
        args = {k: v for k, v in req.items() if k not in ("id", "cmd")}
        try:
            inspect.signature(fn).bind(**args)
        except TypeError as exc:
            return {"id": rid, "ok": False, "error": f"bad arguments for {cmd}: {exc}"}
        except ValueError:
            pass  # no signature to check (builtin); let the call decide
        try:
            result = fn(**args)
        except Exception as exc:
            log_exception("control_channel", f"{cmd} failed", exc)
            return {"id": rid, "ok": False, "error": str(exc) or type(exc).__name__}
        reply: dict[str, Any] = dict(result or {})
        reply.update(id=rid, ok=True)
        try:
            json.dumps(reply, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            log_exception("control_channel", f"{cmd}: unserializable reply", exc)
            return {"id": rid, "ok": False, "error": f"{cmd}: reply not serializable"}
        return reply


class ControlClient:
    """Blocking client: one short-lived connection per call, so it is safe to
    use from any thread (the hub calls it from Qt worker threads)."""

    _ids = itertools.count(1)

    def __init__(self, path: str = SOCKET_PATH) -> None:
        self.path = path

    # A full listen queue also refuses a connection; retry briefly before
    # calling the daemon gone. A missing socket file means gone at once.
    CONNECT_RETRIES = (0.02, 0.05, 0.1)

    def _connect(self, timeout: float) -> socket.socket:
        delays = iter(self.CONNECT_RETRIES)
        while True:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect(self.path)
                return s
            except FileNotFoundError as exc:
                s.close()
                raise DaemonNotRunning() from exc
            except ConnectionRefusedError as exc:
                s.close()
                delay = next(delays, None)
                if delay is None:
                    raise DaemonNotRunning() from exc
                time.sleep(delay)
            except OSError as exc:
                s.close()
                raise DaemonNotRunning() from exc

    def call(self, cmd: str, timeout: float = DEFAULT_TIMEOUT, **args: Any) -> dict:
        """Send one command; return the reply's fields (without id/ok).
        Raises DaemonNotRunning if nothing listens, ControlError otherwise."""
        rid = next(self._ids)
        data = _encode({**args, "id": rid, "cmd": cmd})
        s = self._connect(timeout)
        try:
            s.sendall(data)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    raise DaemonNotRunning()  # daemon quit before replying
                buf += chunk
        except socket.timeout as exc:
            raise ControlError(f"{cmd} timed out after {timeout:g}s") from exc
        except ControlError:
            raise
        except OSError as exc:
            raise DaemonNotRunning() from exc
        finally:
            s.close()
        try:
            reply = json.loads(buf.split(b"\n", 1)[0])
        except ValueError as exc:
            raise ControlError("malformed reply") from exc
        if not isinstance(reply, dict):
            raise ControlError("malformed reply")
        if not reply.get("ok"):
            raise ControlError(str(reply.get("error") or "command failed"))
        return {k: v for k, v in reply.items() if k not in ("id", "ok")}


def _encode(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
