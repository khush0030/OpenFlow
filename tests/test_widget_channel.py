"""widget_channel: JSON lines over a Unix socket."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from widget_channel import WidgetClient, WidgetServer


def wait_for(pred, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def sock_path() -> str:
    # Unix socket paths are limited to ~104 bytes on macOS: keep it short.
    return os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofw"), "w.sock")


def test_round_trip_both_directions():
    to_server, to_client, connects = [], [], []
    srv = WidgetServer(sock_path(), on_message=to_server.append,
                       on_connect=lambda: connects.append(1))
    srv.start()
    cli = WidgetClient(srv.path, on_message=to_client.append)
    assert cli.connect()
    assert wait_for(lambda: srv.connected and connects)
    assert cli.send({"action": "start"})
    assert srv.send({"type": "state", "state": "recording"})
    assert wait_for(lambda: to_server and to_client)
    assert to_server[0] == {"action": "start"}
    assert to_client[0] == {"type": "state", "state": "recording"}
    cli.close()
    srv.stop()


def test_server_notices_client_disconnect():
    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    cli.close()
    assert wait_for(lambda: not srv.connected)
    srv.stop()


def test_client_notices_server_stop():
    lost = []
    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path, on_disconnect=lambda: lost.append(1))
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    srv.stop()
    assert wait_for(lambda: lost and not cli.connected)


def test_newer_client_replaces_older_which_is_told_to_exit():
    old_msgs, new_msgs = [], []
    srv = WidgetServer(sock_path())
    srv.start()
    old = WidgetClient(srv.path, on_message=old_msgs.append)
    assert old.connect()
    assert wait_for(lambda: srv.connected)
    new = WidgetClient(srv.path, on_message=new_msgs.append)
    assert new.connect()
    assert wait_for(lambda: {"type": "exit"} in old_msgs)
    assert wait_for(lambda: not old.connected)
    assert srv.send({"type": "ping"})
    assert wait_for(lambda: {"type": "ping"} in new_msgs)
    assert {"type": "ping"} not in old_msgs
    new.close()
    srv.stop()


def test_connect_fails_cleanly_without_server():
    cli = WidgetClient(sock_path())
    assert cli.connect() is False
    assert cli.send({"action": "start"}) is False


def test_send_to_unresponsive_peer_returns_false_quickly():
    srv = WidgetServer(sock_path())
    srv.start()
    # Raw peer that connects and never reads.
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.connect(srv.path)
    assert wait_for(lambda: srv.connected)
    payload = {"type": "blob", "data": "x" * 65536}
    start = time.monotonic()
    results = []
    # Fill the kernel buffers; eventually a send must time out, not hang.
    for _ in range(200):
        results.append(srv.send(payload))
        if results[-1] is False:
            break
        assert time.monotonic() - start < 10
    assert results[-1] is False
    assert time.monotonic() - start < 10
    assert wait_for(lambda: not srv.connected)
    assert srv.send({"type": "ping"}) is False
    peer.close()
    srv.stop()


def test_reader_survives_idle_longer_than_send_timeout():
    to_server = []
    srv = WidgetServer(sock_path(), on_message=to_server.append)
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    time.sleep(1.5)  # longer than the 1s socket timeout
    assert srv.connected and cli.connected
    assert cli.send({"action": "late"})
    assert wait_for(lambda: to_server == [{"action": "late"}])
    cli.close()
    srv.stop()


def test_non_dict_json_lines_are_dropped():
    got = []
    srv = WidgetServer(sock_path(), on_message=got.append)
    srv.start()
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.connect(srv.path)
    assert wait_for(lambda: srv.connected)
    peer.sendall(b'[1, 2]\n"hi"\n42\nnull\nnot json\n{"ok": 1}\n')
    assert wait_for(lambda: got)
    assert got == [{"ok": 1}]
    peer.close()
    srv.stop()


# --- review fixes -----------------------------------------------------------

import errno
import stat

import pytest

import widget_channel


@pytest.fixture(autouse=True)
def logged(monkeypatch):
    calls = []
    monkeypatch.setattr(widget_channel, "log_exception",
                        lambda *a, **k: calls.append(a))
    return calls


def test_accept_loop_survives_raising_on_connect(logged):
    def boom():
        raise RuntimeError("on_connect bug")

    srv = WidgetServer(sock_path(), on_connect=boom)
    srv.start()
    first = WidgetClient(srv.path)
    assert first.connect()
    assert wait_for(lambda: srv.connected and logged)
    got = []
    second = WidgetClient(srv.path, on_message=got.append)
    assert second.connect()
    assert wait_for(lambda: len(logged) >= 2)
    assert srv.send({"type": "ping"})
    assert wait_for(lambda: got == [{"type": "ping"}])
    first.close()
    second.close()
    srv.stop()


def test_accept_loop_survives_transient_accept_error(logged):
    class Flaky:
        def __init__(self, real):
            self.real, self.failed = real, False

        def accept(self):
            if not self.failed:
                self.failed = True
                raise OSError(errno.ECONNABORTED, "aborted")
            return self.real.accept()

        def close(self):
            self.real.close()

    srv = WidgetServer(sock_path())
    srv._on_connect = lambda: setattr(srv, "_sock", Flaky(srv._sock))
    srv.start()
    first = WidgetClient(srv.path)
    assert first.connect()
    assert wait_for(lambda: srv.connected and isinstance(srv._sock, Flaky))
    got = []
    second = WidgetClient(srv.path, on_message=got.append)
    assert second.connect()
    assert wait_for(lambda: logged)  # the transient error was logged
    assert wait_for(lambda: srv.send({"type": "ping"}) and got)
    first.close()
    second.close()
    srv.stop()


def test_handler_exception_does_not_close_connection(logged):
    seen = []

    def handler(msg):
        seen.append(msg)
        if msg.get("n") == 1:
            raise RuntimeError("handler bug")

    srv = WidgetServer(sock_path(), on_message=handler)
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    assert cli.send({"n": 1}) and cli.send({"n": 2})
    assert wait_for(lambda: len(seen) == 2)
    assert logged and srv.connected and cli.connected
    cli.close()
    srv.stop()


def test_client_handler_exception_does_not_close_connection(logged):
    seen = []

    def handler(msg):
        seen.append(msg)
        if msg.get("n") == 1:
            raise RuntimeError("handler bug")

    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path, on_message=handler)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    assert srv.send({"n": 1}) and srv.send({"n": 2})
    assert wait_for(lambda: len(seen) == 2)
    assert logged and cli.connected and srv.connected
    cli.close()
    srv.stop()


def test_unserializable_message_keeps_connection_open(logged):
    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    assert srv.send({"bad": object()}) is False
    assert logged
    assert srv.connected
    assert srv.send({"type": "ping"}) is True
    cli.close()
    srv.stop()


def test_timed_out_connection_is_logged_once(logged):
    srv = WidgetServer(sock_path())
    srv.start()
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.connect(srv.path)
    assert wait_for(lambda: srv.connected)
    payload = {"type": "blob", "data": "x" * 65536}
    for _ in range(200):
        if srv.send(payload) is False:
            break
    assert srv.send(payload) is False
    assert srv.send(payload) is False
    assert len(logged) == 1
    peer.close()
    srv.stop()


def test_second_server_on_live_path_is_refused_and_does_not_evict():
    path = sock_path()
    a = WidgetServer(path)
    a.start()
    got = []
    cli = WidgetClient(path, on_message=got.append)
    assert cli.connect()
    assert wait_for(lambda: a.connected)
    b = WidgetServer(path)
    with pytest.raises(OSError) as ei:
        b.start()
    assert ei.value.errno == errno.EADDRINUSE
    time.sleep(0.3)  # the probe must not have evicted the real widget
    assert a.connected and cli.connected
    assert a.send({"type": "ping"})
    assert wait_for(lambda: got == [{"type": "ping"}])
    cli.close()
    a.stop()


def test_stale_socket_file_is_replaced():
    path = sock_path()
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(path)
    dead.close()  # file remains, nobody listening
    assert os.path.exists(path)
    srv = WidgetServer(path)
    srv.start()
    cli = WidgetClient(path)
    assert cli.connect()
    cli.close()
    srv.stop()


def test_stop_does_not_unlink_a_path_it_no_longer_owns():
    path = sock_path()
    srv = WidgetServer(path)
    srv.start()
    os.unlink(path)
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(path)  # someone else's live socket now owns the path
    srv.stop()
    assert os.path.exists(path)
    other.close()


def test_socket_is_0600_and_umask_restored():
    before = os.umask(0o022)
    try:
        srv = WidgetServer(sock_path())
        srv.start()
        assert stat.S_IMODE(os.stat(srv.path).st_mode) == 0o600
        assert os.umask(0o022) == 0o022
        srv.stop()
    finally:
        os.umask(before)


def test_new_dir_is_0700_and_existing_dir_untouched():
    root = tempfile.mkdtemp(dir="/tmp", prefix="ofw")
    new_dir = os.path.join(root, "d")
    srv = WidgetServer(os.path.join(new_dir, "w.sock"))
    srv.start()
    assert stat.S_IMODE(os.stat(new_dir).st_mode) == 0o700
    srv.stop()
    os.chmod(root, 0o755)
    srv = WidgetServer(os.path.join(root, "w.sock"))
    srv.start()
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o755
    srv.stop()


def test_lock_not_socket_file_decides_ownership():
    path = sock_path()
    a = WidgetServer(path)
    a.start()
    os.unlink(path)  # even with the socket file gone, A still holds the lock
    b = WidgetServer(path)
    with pytest.raises(OSError) as ei:
        b.start()
    assert ei.value.errno == errno.EADDRINUSE
    a.stop()


def test_new_server_can_start_after_first_stops():
    path = sock_path()
    a = WidgetServer(path)
    a.start()
    a.stop()
    b = WidgetServer(path)
    b.start()
    cli = WidgetClient(path)
    assert cli.connect()
    assert wait_for(lambda: b.connected)
    cli.close()
    b.stop()


# -- on_disconnect: the live peer going away, and only that ----------------

def test_server_on_disconnect_fires_when_the_peer_closes():
    gone = []
    srv = WidgetServer(sock_path(), on_disconnect=lambda: gone.append(1))
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    assert gone == []
    cli.close()
    assert wait_for(lambda: gone == [1])
    srv.stop()


def test_server_on_disconnect_ignores_a_replaced_client():
    gone = []
    srv = WidgetServer(sock_path(), on_disconnect=lambda: gone.append(1))
    srv.start()
    old = WidgetClient(srv.path)
    assert old.connect()
    assert wait_for(lambda: srv.connected)
    new = WidgetClient(srv.path)
    assert new.connect()
    assert wait_for(lambda: not old.connected)
    time.sleep(0.1)
    assert gone == [] and srv.connected
    new.close()
    assert wait_for(lambda: gone == [1])
    srv.stop()


def test_server_on_disconnect_not_fired_by_stop():
    gone = []
    srv = WidgetServer(sock_path(), on_disconnect=lambda: gone.append(1))
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    srv.stop()
    assert wait_for(lambda: not cli.connected)
    time.sleep(0.1)
    assert gone == []


def test_message_sent_right_before_close_still_arrives():
    # A peer may say its last word and hang up at once; the line must not
    # be lost to the shutdown.
    got, gone = [], []
    srv = WidgetServer(sock_path(), on_message=got.append,
                       on_disconnect=lambda: gone.append(1))
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    assert cli.send({"action": "bye"})
    cli.close()
    assert wait_for(lambda: gone)
    assert got == [{"action": "bye"}]
    srv.stop()


def test_edit_overlay_socket_is_its_own_path_under_openflow():
    from widget_channel import EDIT_OVERLAY_SOCKET_PATH, SOCKET_PATH
    assert EDIT_OVERLAY_SOCKET_PATH != SOCKET_PATH
    assert os.path.dirname(EDIT_OVERLAY_SOCKET_PATH) == os.path.dirname(SOCKET_PATH)
    assert not EDIT_OVERLAY_SOCKET_PATH.startswith("/tmp")
