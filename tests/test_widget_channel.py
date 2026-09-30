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
