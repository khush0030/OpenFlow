"""control_channel: request/reply JSON lines over a Unix socket (spec §3)."""
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import control_channel as cc
from control_channel import ControlClient, ControlError, ControlServer, DaemonNotRunning


def sock_path() -> str:
    # Unix socket paths are limited to ~104 bytes on macOS: keep it short.
    return os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofc"), "c.sock")


@pytest.fixture
def server():
    started: list[ControlServer] = []

    def make(handlers):
        srv = ControlServer(handlers, path=sock_path())
        srv.start()
        started.append(srv)
        return srv
    yield make
    for srv in started:
        srv.stop()


def raw_exchange(path: str, payload: bytes, n_lines: int = 1) -> list[dict]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(path)
    s.sendall(payload)
    buf = b""
    while buf.count(b"\n") < n_lines:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return [json.loads(line) for line in buf.splitlines() if line.strip()]


def test_round_trip_passes_args_and_returns_fields(server):
    seen = []

    def echo(value):
        seen.append(value)
        return {"echo": value}
    srv = server({"echo": echo})
    reply = ControlClient(srv.path).call("echo", value="hi")
    assert reply == {"echo": "hi"}
    assert seen == ["hi"]


def test_handler_returning_none_is_an_empty_ok_reply(server):
    srv = server({"noop": lambda: None})
    assert ControlClient(srv.path).call("noop") == {}


def test_reply_carries_the_request_id(server):
    srv = server({"ping": lambda: {"pong": True}})
    out = raw_exchange(srv.path, b'{"id": 7, "cmd": "ping"}\n')
    assert out == [{"id": 7, "ok": True, "pong": True}]


def test_several_requests_on_one_connection_each_get_a_reply(server):
    srv = server({"n": lambda x: {"x": x}})
    out = raw_exchange(srv.path, b'{"id": 1, "cmd": "n", "x": 1}\n'
                                 b'{"id": 2, "cmd": "n", "x": 2}\n', n_lines=2)
    assert [(r["id"], r["x"]) for r in out] == [(1, 1), (2, 2)]


def test_unknown_command_is_an_error_reply(server):
    srv = server({})
    out = raw_exchange(srv.path, b'{"id": 3, "cmd": "nope"}\n')
    assert out[0]["id"] == 3 and out[0]["ok"] is False
    assert "unknown command" in out[0]["error"]
    with pytest.raises(ControlError, match="unknown command"):
        ControlClient(srv.path).call("nope")


def test_malformed_json_gets_an_error_reply_and_the_link_survives(server):
    srv = server({"ping": lambda: {"pong": True}})
    out = raw_exchange(srv.path, b'{not json\n[1, 2]\n{"id": 4, "cmd": "ping"}\n', n_lines=3)
    assert out[0] == {"id": None, "ok": False, "error": "malformed request"}
    assert out[1] == {"id": None, "ok": False, "error": "malformed request"}
    assert out[2] == {"id": 4, "ok": True, "pong": True}


def test_bad_arguments_are_an_error_reply(server):
    srv = server({"needs_x": lambda x: {"x": x}})
    with pytest.raises(ControlError, match="bad arguments"):
        ControlClient(srv.path).call("needs_x", y=1)


def test_handler_exception_becomes_an_error_reply(server):
    def boom():
        raise RuntimeError("sarvam down")
    srv = server({"boom": boom, "ping": lambda: {"pong": True}})
    cli = ControlClient(srv.path)
    with pytest.raises(ControlError, match="sarvam down"):
        cli.call("boom")
    assert cli.call("ping") == {"pong": True}  # server still serving


def test_unserializable_result_becomes_an_error_reply(server):
    srv = server({"bad": lambda: {"obj": object()}})
    with pytest.raises(ControlError):
        ControlClient(srv.path).call("bad")


def test_concurrent_clients_are_served_in_parallel(server):
    # Two slow requests must overlap: a client waiting on a long rerun
    # can't block another asking for status.
    gate = threading.Barrier(2, timeout=2.0)

    def slow(n):
        gate.wait()
        return {"n": n}
    srv = server({"slow": slow})
    results, errors = {}, []

    def worker(n):
        try:
            results[n] = ControlClient(srv.path).call("slow", n=n, timeout=3.0)["n"]
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert not errors
    assert results == {1: 1, 2: 2}


def test_many_clients(server):
    srv = server({"sq": lambda x: {"y": x * x}})
    out, errors = {}, []

    def worker(n):
        try:
            out[n] = ControlClient(srv.path).call("sq", x=n)["y"]
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert not errors
    assert out == {n: n * n for n in range(12)}


def test_client_raises_daemon_not_running_without_server():
    path = sock_path()
    with pytest.raises(DaemonNotRunning):
        ControlClient(path).call("status")
    # Stale socket file, nobody listening.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.close()
    with pytest.raises(DaemonNotRunning):
        ControlClient(path).call("status")


def test_daemon_not_running_is_a_control_error():
    # The hub can catch ControlError for everything and still tell the two apart.
    assert issubclass(DaemonNotRunning, ControlError)
    assert "isn't running" in str(DaemonNotRunning())


def test_client_times_out(server):
    release = threading.Event()
    srv = server({"hang": lambda: release.wait(5.0) and {}})
    try:
        with pytest.raises(ControlError, match="timed out"):
            ControlClient(srv.path).call("hang", timeout=0.2)
    finally:
        release.set()


def test_server_stopping_mid_call_is_daemon_not_running(server):
    entered, release = threading.Event(), threading.Event()

    def hang():
        entered.set()
        release.wait(5.0)
        return {}
    srv = server({"hang": hang})
    errs = []

    def call():
        try:
            ControlClient(srv.path).call("hang", timeout=3.0)
        except Exception as exc:
            errs.append(exc)
    t = threading.Thread(target=call)
    t.start()
    assert entered.wait(2.0)
    srv.stop()
    t.join(4.0)
    release.set()
    assert len(errs) == 1 and isinstance(errs[0], DaemonNotRunning)


def test_stale_socket_file_is_replaced_and_mode_is_0600():
    path = sock_path()
    with open(path, "w") as f:
        f.write("stale")
    srv = ControlServer({"ping": lambda: {"pong": True}}, path=path)
    srv.start()
    try:
        assert stat.S_ISSOCK(os.stat(path).st_mode)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert ControlClient(path).call("ping") == {"pong": True}
    finally:
        srv.stop()
    assert not os.path.exists(path)


def test_second_server_on_same_path_is_refused():
    path = sock_path()
    a = ControlServer({}, path=path)
    a.start()
    try:
        with pytest.raises(OSError):
            ControlServer({}, path=path).start()
    finally:
        a.stop()


def test_default_path_is_under_dot_openflow():
    assert cc.SOCKET_PATH.endswith(os.path.join(".openflow", "control.sock"))


def test_refused_connect_is_retried_before_not_running(server, monkeypatch):
    # A full listen queue refuses like a dead daemon; one refusal mustn't
    # make the hub say "OpenFlow isn't running".
    srv = server({"ping": lambda: {"pong": True}})
    real = socket.socket.connect
    attempts = {"n": 0}

    def flaky(self, addr):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionRefusedError()
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", flaky)
    assert ControlClient(srv.path).call("ping")["pong"] is True
    assert attempts["n"] == 2
