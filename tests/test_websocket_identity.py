"""Open terminal sockets stay bound to the Console session they attached to."""
from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import AsyncMock, patch

from starlette.websockets import WebSocketDisconnect
from test_web import WebTests as _WebFixture


class WebSocketIdentityTests(unittest.TestCase):
    setUp = _WebFixture.setUp
    tearDown = _WebFixture.tearDown

    def drain_closed(self, websocket):
        with self.assertRaises(WebSocketDisconnect) as raised:
            while True:
                websocket.receive_bytes()
        return raised.exception.code

    def test_open_socket_scroll_follows_id_after_rename_and_old_name_reuse(self):
        original = self.manager.create(tool="shell", profile="general", name="socket-original")
        called = threading.Event()
        with self.client.websocket_connect(
            f'/ws/sessions/socket-original?session_id={original["id"]}', headers=self.headers
        ) as ws:
            renamed = self.manager.rename("socket-original", "socket-renamed")
            replacement = self.manager.create(tool="shell", profile="general", name="socket-original")
            self.assertEqual(original["id"], renamed["id"])
            self.assertNotEqual(original["id"], replacement["id"])
            with patch.object(self.manager.tmux, "scroll_history", side_effect=lambda *args: called.set()) as scroll:
                ws.send_text(json.dumps({"type": "scroll", "lines": -8}))
                self.assertTrue(called.wait(5), "scroll control was not processed")
                scroll.assert_called_once_with("socket-renamed", -8)
            ws.send_text(json.dumps({"type": "detach"}))
            self.assertEqual(self.drain_closed(ws), 4000)

    def test_expected_identity_rejects_reused_name_before_attachment(self):
        original = self.manager.create(tool="shell", profile="general", name="guard-original")
        self.manager.rename("guard-original", "guard-renamed")
        self.manager.create(tool="shell", profile="general", name="guard-original")
        with patch("agent_console.web.pty.openpty") as openpty:
            with self.assertRaises(WebSocketDisconnect) as raised:
                with self.client.websocket_connect(
                    f'/ws/sessions/guard-original?session_id={original["id"]}', headers=self.headers
                ):
                    self.fail("stale identity should not attach")
            self.assertEqual(raised.exception.code, 4409)
            openpty.assert_not_called()

    def test_client_limit_remains_bound_to_id_through_rename(self):
        original = self.manager.create(tool="shell", profile="general", name="limit-original")
        with patch("agent_console.web.MAX_PTY_CLIENTS", 1):
            with self.client.websocket_connect('/ws/sessions/limit-original', headers=self.headers) as first:
                self.manager.rename("limit-original", "limit-renamed")
                with self.assertRaises(WebSocketDisconnect) as raised:
                    with self.client.websocket_connect('/ws/sessions/limit-renamed', headers=self.headers):
                        self.fail("rename must not reset the attached-client count")
                self.assertEqual(raised.exception.code, 4429)
                replacement = self.manager.create(tool="shell", profile="general", name="limit-original")
                self.assertNotEqual(original["id"], replacement["id"])
                with self.client.websocket_connect('/ws/sessions/limit-original', headers=self.headers) as other:
                    other.send_text(json.dumps({"type": "detach"}))
                    self.assertEqual(self.drain_closed(other), 4000)
                first.send_text(json.dumps({"type": "detach"}))
                self.assertEqual(self.drain_closed(first), 4000)
            with self.client.websocket_connect('/ws/sessions/limit-renamed', headers=self.headers) as again:
                again.send_text(json.dumps({"type": "detach"}))
                self.assertEqual(self.drain_closed(again), 4000)

    def test_scroll_failure_closes_socket_instead_of_leaving_it_half_open(self):
        self.manager.create(tool="shell", profile="general", name="failed-scroll")
        with patch.object(self.manager.tmux, "scroll_history", side_effect=RuntimeError("target disappeared")):
            with self.client.websocket_connect('/ws/sessions/failed-scroll', headers=self.headers) as ws:
                ws.send_text(json.dumps({"type": "scroll", "lines": -1}))
                self.assertEqual(self.drain_closed(ws), 4001)
    def test_attachment_uses_tmux_runtime_id_not_reusable_name(self):
        self.manager.create(tool="shell", profile="general", name="runtime-identity")
        with patch.object(self.manager.tmux, "command", wraps=self.manager.tmux.command) as command:
            with self.client.websocket_connect('/ws/sessions/runtime-identity', headers=self.headers) as ws:
                ws.send_text(json.dumps({"type": "detach"}))
                self.assertEqual(self.drain_closed(ws), 4000)
            attaches = [call.args for call in command.call_args_list if call.args[0] == "attach-session"]
            self.assertEqual(len(attaches), 1)
            self.assertEqual(attaches[0][:2], ("attach-session", "-t"))
            self.assertRegex(attaches[0][2], r"^\$[0-9]+$")

    def test_binary_frames_are_forwarded_once_without_collapsing_legitimate_repeats(self):
        self.manager.create(tool="shell", profile="general", name="ordered-input")
        received, done = [], threading.Event()
        async def record(data):
            received.append(data)
            if len(received) == 2: done.set()
        with patch("agent_console.web.PtyTransport.write", new=AsyncMock(side_effect=record)):
            with self.client.websocket_connect('/ws/sessions/ordered-input', headers=self.headers) as ws:
                ws.send_bytes(b'a'); ws.send_bytes(b'a')
                self.assertTrue(done.wait(5), "binary frames were not forwarded")
                ws.send_text(json.dumps({"type":"detach"}))
                self.assertEqual(self.drain_closed(ws), 4000)
        self.assertEqual(received, [b'a', b'a'])
        self.assertTrue(self.manager.tmux.exists('ordered-input'))

    def test_repeated_same_size_resize_signals_attach_client_only_once(self):
        import os
        self.manager.create(tool="shell", profile="general", name="stable-resize")
        with patch('agent_console.pty_transport.os.killpg', wraps=os.killpg) as signal_client:
            with self.client.websocket_connect('/ws/sessions/stable-resize', headers=self.headers) as ws:
                for _ in range(3): ws.send_text(json.dumps({"type":"resize", "rows":30, "cols":100}))
                ws.send_text(json.dumps({"type":"detach"}))
                self.assertEqual(self.drain_closed(ws), 4000)
            self.assertEqual(signal_client.call_count, 1)
        self.assertTrue(self.manager.tmux.exists('stable-resize'))

    def test_disconnect_queued_behind_backpressure_releases_attachment_and_keeps_session(self):
        import os
        from agent_console.pty_transport import PtyTransport
        class StalledInput(PtyTransport):
            def __init__(self, fd):
                super().__init__(fd)
                self.input_read, input_write = os.pipe()
                self.blocked_input = PtyTransport(input_write, write_timeout=.05)
                while True:
                    try: os.write(input_write, b'x'*4096)
                    except BlockingIOError: break
            async def write(self, data):
                await self.blocked_input.write(data)
            def close(self):
                if self.closed: return
                self.blocked_input.close(); os.close(self.input_read); super().close()
        self.manager.create(tool="shell", profile="general", name="stalled-disconnect")
        with patch('agent_console.web.PtyTransport', StalledInput):
            with self.client.websocket_connect('/ws/sessions/stalled-disconnect', headers=self.headers) as ws:
                ws.send_bytes(b'pending-input')
                ws.close()  # Queued behind the stalled write in the server receive loop.
                self.assertEqual(self.drain_closed(ws), 4001)
        self.assertTrue(self.manager.tmux.exists('stalled-disconnect'))
        # The timed-out attachment released its per-session client reservation.
        with self.client.websocket_connect('/ws/sessions/stalled-disconnect', headers=self.headers) as ws:
            ws.send_text(json.dumps({"type":"detach"}))
            self.assertEqual(self.drain_closed(ws), 4000)

    def test_reader_failure_closes_attachment_without_killing_session(self):
        self.manager.create(tool="shell", profile="general", name="reader-failure")
        with patch('agent_console.web.PtyTransport.read', new=AsyncMock(side_effect=OSError('reader failed'))):
            with self.client.websocket_connect('/ws/sessions/reader-failure', headers=self.headers) as ws:
                self.assertEqual(self.drain_closed(ws), 4001)
        self.assertTrue(self.manager.tmux.exists('reader-failure'))

    def test_brief_and_review_reject_stale_identity_before_reading(self):
        original = self.manager.create(tool="shell", profile="general", name="read-original")
        self.manager.rename("read-original", "read-renamed")
        self.manager.create(tool="shell", profile="general", name="read-original")
        for operation, method in (("brief", "session_brief"), ("review", "review_session")):
            with self.subTest(operation=operation), patch.object(self.manager, method) as read:
                response = self.client.get(
                    f'/api/sessions/read-original/{operation}?session_id={original["id"]}', headers=self.headers,
                )
                self.assertEqual(response.status_code, 409)
                read.assert_not_called()
            response = self.client.get(
                f'/api/sessions/read-renamed/{operation}?session_id={original["id"]}', headers=self.headers,
            )
            self.assertEqual(response.status_code, 200, response.text)

    def test_guarded_read_discards_response_if_identity_changes_during_read(self):
        original = self.manager.create(tool="shell", profile="general", name="read-race")
        def raced_read(name):
            self.manager.rename(name, "read-race-renamed")
            self.manager.create(tool="shell", profile="general", name=name)
            return {"brief": "This value must not be disclosed"}
        with patch.object(self.manager, "session_brief", side_effect=raced_read):
            response = self.client.get(
                f'/api/sessions/read-race/brief?session_id={original["id"]}', headers=self.headers,
            )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("This value", response.text)

    def test_invalid_tmux_identity_releases_client_reservation(self):
        from subprocess import CompletedProcess
        self.manager.create(tool="shell", profile="general", name="invalid-runtime-id")
        real_run = self.manager.tmux.run
        def invalid_id(*args, **kwargs):
            if args[-1] == "#{session_id}":
                return CompletedProcess(args, 0, stdout="not-a-runtime-id\n", stderr="")
            return real_run(*args, **kwargs)
        with patch("agent_console.web.MAX_PTY_CLIENTS", 1):
            with patch.object(self.manager.tmux, "run", side_effect=invalid_id):
                with self.client.websocket_connect('/ws/sessions/invalid-runtime-id', headers=self.headers) as ws:
                    self.assertEqual(self.drain_closed(ws), 4001)
            with self.client.websocket_connect('/ws/sessions/invalid-runtime-id', headers=self.headers) as ws:
                ws.send_text(json.dumps({"type": "detach"}))
                self.assertEqual(self.drain_closed(ws), 4000)


# Reuse setup/teardown without collecting the entire imported WebTests suite.
del _WebFixture
