"""PTY transport regressions; private FDs only, no operator sessions."""
import asyncio
import errno
import os
import signal
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent_console.pty_transport import PtyTransport


class PtyTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.master, self.slave = os.openpty()
        self.transport = PtyTransport(self.master)

    async def asyncTearDown(self):
        self.transport.close()
        if self.slave is not None: os.close(self.slave)

    async def test_short_writes_interruption_and_backpressure_preserve_every_byte(self):
        accepted = bytearray()
        results = iter([2, InterruptedError(), BlockingIOError(), 1, 99])
        def write(fd, data):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            count = min(result, len(data)); accepted.extend(data[:count]); return count
        with patch('agent_console.pty_transport.os.write', side_effect=write), patch.object(self.transport, '_ready', new=AsyncMock()) as ready:
            await self.transport.write(b'abcdef')
        self.assertEqual(accepted, b'abcdef')
        ready.assert_awaited_once_with('write')

    async def test_concurrent_writes_are_ordered_while_backpressure_yields(self):
        blocked, resume = asyncio.Event(), asyncio.Event()
        accepted = bytearray(); calls = 0
        def write(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                accepted.extend(data[:1]); return 1
            if calls == 2:
                raise BlockingIOError()
            accepted.extend(data); return len(data)
        async def ready(kind):
            blocked.set(); await resume.wait()
        with patch('agent_console.pty_transport.os.write', side_effect=write), patch.object(self.transport, '_ready', side_effect=ready):
            first = asyncio.create_task(self.transport.write(b'first'))
            await asyncio.wait_for(blocked.wait(), 2)
            second = asyncio.create_task(self.transport.write(b'second'))
            await asyncio.sleep(0)  # Event loop continues; second waits for the first frame.
            self.assertEqual(accepted, b'f')
            resume.set(); await asyncio.gather(first, second)
        self.assertEqual(accepted, b'firstsecond')

    async def test_cancelled_partial_write_does_not_replay_remaining_input(self):
        blocked = asyncio.Event(); accepted = bytearray(); calls = 0
        def write(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                accepted.extend(data[:1]); return 1
            if calls == 2:
                raise BlockingIOError()
            accepted.extend(data); return len(data)
        async def ready(kind):
            blocked.set(); await asyncio.Future()
        with patch('agent_console.pty_transport.os.write', side_effect=write), patch.object(self.transport, '_ready', side_effect=ready):
            task = asyncio.create_task(self.transport.write(b'old-input'))
            await asyncio.wait_for(blocked.wait(), 2); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            await self.transport.write(b'new-input')
        self.assertEqual(accepted, b'onew-input')

    async def test_real_read_waits_without_using_executor_threads(self):
        self.assertFalse(os.get_blocking(self.master))
        with patch.object(self.transport.loop, 'run_in_executor', side_effect=AssertionError('PTY read must not occupy a worker')):
            task = asyncio.create_task(self.transport.read())
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            os.write(self.slave, b'output-once')
            self.assertEqual(await asyncio.wait_for(task, 2), b'output-once')
        self.assertFalse(self.transport._waiters)

    async def test_real_pty_multichunk_binary_input_is_exact_and_ordered(self):
        import tty
        tty.setraw(self.slave)
        peer = PtyTransport(self.slave)
        chunks = [bytes(range(256))*128, 'é中'.encode()*4096, b'final\x00\r\n'*2048]
        expected = b''.join(chunks)
        async def consume():
            result = bytearray()
            while len(result) < len(expected):
                result.extend(await peer.read(97))
            return bytes(result)
        async def produce():
            for chunk in chunks: await self.transport.write(chunk)
        try:
            received, _ = await asyncio.wait_for(asyncio.gather(consume(), produce()), 10)
            self.assertEqual(received, expected)
        finally:
            peer.close(); self.slave = None

    async def test_real_pty_backpressure_has_bounded_timeout_and_cleans_waiter(self):
        import tty
        tty.setraw(self.slave)
        self.transport.write_timeout = .05
        # No reader drains the slave. This must fill the PTY queue and stop
        # without replay, rather than holding the websocket receive loop forever.
        with self.assertRaisesRegex(RuntimeError, 'stalled.*without replaying'):
            await asyncio.wait_for(self.transport.write(b'x' * 1048576), 2)
        self.assertFalse(self.transport._waiters)
        self.assertFalse(self.transport._write_lock.locked())
        self.transport.close()
        self.assertTrue(self.transport.closed)

    async def test_close_unregisters_before_fd_reuse_and_old_waiter_cannot_remove_new_callback(self):
        callbacks = {}
        def add(fd, callback): callbacks[fd] = callback
        def remove(fd): return callbacks.pop(fd, None) is not None
        with patch.object(self.transport.loop, 'add_reader', side_effect=add), patch.object(self.transport.loop, 'remove_reader', side_effect=remove) as remove_reader:
            task = asyncio.create_task(self.transport._ready('read'))
            await asyncio.sleep(0)
            self.assertIn(self.master, callbacks)
            self.transport.close()
            self.assertNotIn(self.master, callbacks)
            os.dup2(self.slave, self.master)  # Reuse the exact descriptor before cancellation unwinds.
            replacement = Mock(); add(self.master, replacement)
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertIs(callbacks[self.master], replacement)
            remove_reader.assert_called_once_with(self.master)
            os.close(self.master)
        self.assertFalse(self.transport._waiters)

    async def test_read_and_write_waiters_cancel_without_leaving_callbacks(self):
        for kind in ('read', 'write'):
            add_name, remove_name = ('add_reader', 'remove_reader') if kind == 'read' else ('add_writer', 'remove_writer')
            with patch.object(self.transport.loop, add_name) as add, patch.object(self.transport.loop, remove_name) as remove:
                task = asyncio.create_task(self.transport._ready(kind)); await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                add.assert_called_once(); remove.assert_called_once_with(self.master)
                self.assertNotIn(kind, self.transport._waiters)

    async def test_resize_signals_only_when_dimensions_change_and_closed_fd_is_never_used(self):
        with patch('agent_console.pty_transport.fcntl.ioctl') as ioctl, patch('agent_console.pty_transport.os.killpg') as killpg:
            self.assertFalse(self.transport.resize(24, 80, 123))
            self.assertTrue(self.transport.resize(30, 100, 123))
            self.assertFalse(self.transport.resize(30, 100, 123))
            self.assertTrue(self.transport.resize(40, 100, 123))
            self.assertEqual(ioctl.call_count, 2)
            self.assertEqual(killpg.call_count, 2)
            killpg.assert_called_with(123, signal.SIGWINCH)
            self.transport.close()
            with self.assertRaises(OSError): self.transport.resize(20, 60, 123)
            self.assertEqual(ioctl.call_count, 2)

    async def test_zero_progress_write_fails_instead_of_spinning(self):
        with patch('agent_console.pty_transport.os.write', return_value=0):
            with self.assertRaises(OSError) as raised: await self.transport.write(b'input')
        self.assertEqual(raised.exception.errno, errno.EIO)
