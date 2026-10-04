"""Ordered, cancellable PTY I/O without blocking the event loop or replaying input."""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import signal
import struct
import termios


class PtyTransport:
    def __init__(self, fd: int, *, size: tuple[int, int] = (24, 80), write_timeout: float = 10):
        self.fd = fd
        self.loop = asyncio.get_running_loop()
        self.closed = False
        self.size = size
        self.write_timeout = write_timeout
        self._waiters = {}
        self._write_lock = asyncio.Lock()
        os.set_blocking(fd, False)

    async def _ready(self, kind: str) -> None:
        if self.closed:
            raise OSError(errno.EBADF, 'PTY transport is closed')
        future = self.loop.create_future()
        if kind in self._waiters:
            raise RuntimeError('PTY readiness already has a waiter')
        self._waiters[kind] = future
        def ready():
            if not future.done():
                future.set_result(None)
        add = self.loop.add_reader if kind == 'read' else self.loop.add_writer
        remove = self.loop.remove_reader if kind == 'read' else self.loop.remove_writer
        try:
            add(self.fd, ready)
            await future
        finally:
            # close() unregisters before closing the FD. A cancelled old waiter
            # must never remove a callback installed later on a reused FD.
            if self._waiters.get(kind) is future:
                self._waiters.pop(kind)
                remove(self.fd)

    async def read(self, size: int = 8192) -> bytes:
        while not self.closed:
            try:
                return os.read(self.fd, size)
            except InterruptedError:
                continue
            except BlockingIOError:
                await self._ready('read')
        raise OSError(errno.EBADF, 'PTY transport is closed')

    async def write(self, data: bytes) -> None:
        try:
            # The websocket receive loop cannot observe a queued disconnect
            # while writing. Bound the entire frame (including lock wait) so a
            # peer that stops draining cannot hold this attachment forever.
            async with asyncio.timeout(self.write_timeout):
                async with self._write_lock:
                    remaining = memoryview(data)
                    while remaining:
                        if self.closed:
                            raise OSError(errno.EBADF, 'PTY transport is closed')
                        try:
                            count = os.write(self.fd, remaining)
                        except InterruptedError:
                            continue
                        except BlockingIOError:
                            await self._ready('write')
                            continue
                        if count <= 0:
                            raise OSError(errno.EIO, 'PTY write made no progress')
                        remaining = remaining[count:]
        except TimeoutError:
            raise RuntimeError('PTY input stalled; attachment closed without replaying input') from None

    def resize(self, rows: int, cols: int, process_id: int) -> bool:
        if self.closed:
            raise OSError(errno.EBADF, 'PTY transport is closed')
        size = (rows, cols)
        if size == self.size:
            return False
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        os.killpg(process_id, signal.SIGWINCH)
        self.size = size
        return True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for kind, future in list(self._waiters.items()):
            remove = self.loop.remove_reader if kind == 'read' else self.loop.remove_writer
            remove(self.fd)
            self._waiters.pop(kind)
            # Cancellation also wakes readers/writers blocked on readiness.
            future.cancel()
        os.close(self.fd)
