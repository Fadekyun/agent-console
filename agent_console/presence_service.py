"""Private receiver authority. One launcher child owns lifetime for all workers.

No on-demand server creation by HTTP workers. Root/UID/credential provisioning
is deliberately external; this source expects an existing private directory.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import socket
import stat
import struct
import time
from pathlib import Path

from .device_presence import (LIMIT, PrivateStore, Receiver, Rejected, Unavailable,
                              decode, encode, regular)


def frame(connection, end):
    data = bytearray()
    while True:
        remaining = end-time.monotonic()
        if remaining <= 0:
            raise Unavailable()
        connection.settimeout(remaining)
        part = connection.recv(min(2048, LIMIT+1-len(data)))
        if not part:
            return decode(bytes(data))
        data.extend(part)
        if len(data) > LIMIT:
            raise Unavailable()


class Authority:
    def __init__(self, directory, generation, clock=None):
        self.directory = Path(directory)
        self.generation = generation
        self.store = PrivateStore(directory)
        self.lock = None
        self.server = None
        self.socket_identity = None
        try:
            self.lock = os.open('authority.lock', os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW,
                                0o600, dir_fd=self.store.fd)
            self.lock_identity = regular(self.lock)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.receiver = Receiver(self.store, clock)
            # Validate provisioned verifier at service startup, never create it.
            self.store.verifier()
            try:
                previous = os.stat('receiver.sock', dir_fd=self.store.fd, follow_symlinks=False)
                if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid != os.geteuid():
                    raise Unavailable()
                os.unlink('receiver.sock', dir_fd=self.store.fd)
            except FileNotFoundError:
                pass
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.validate_paths()
            self.server.bind(f'/proc/self/fd/{self.store.fd}/receiver.sock')
            os.chmod('receiver.sock', 0o600, dir_fd=self.store.fd, follow_symlinks=False)
            self.socket_identity = os.stat('receiver.sock', dir_fd=self.store.fd, follow_symlinks=False)
            self.server.listen(8)
            self.server.settimeout(.2)
        except BaseException:
            self.close()
            raise

    def validate_paths(self):
        current = os.stat(self.directory, follow_symlinks=False)
        opened = os.fstat(self.store.fd)
        lock = os.stat('authority.lock', dir_fd=self.store.fd, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or (lock.st_dev, lock.st_ino) != (self.lock_identity.st_dev, self.lock_identity.st_ino)
                or not stat.S_ISDIR(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o700):
            raise Unavailable()

    def once(self):
        self.validate_paths()
        # Sample while idle too; rollback does not become a new positive baseline.
        try:
            self.receiver.clock.sample()
        except Unavailable:
            self.receiver.failed = True
            self.receiver.current = None
        try:
            connection, _ = self.server.accept()
        except socket.timeout:
            return
        with connection:
            response = {'status': 503, 'body': {'error': 'presence_unavailable'}}
            try:
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.geteuid():
                    raise Rejected(403)
                value = frame(connection, time.monotonic()+1)
                if (not isinstance(value, dict) or set(value) != {'generation', 'operation', 'body', 'token'}
                        or value['generation'] != self.generation):
                    raise Rejected(503)
                if value['operation'] == 'read' and value['body'] is None and value['token'] is None:
                    body = self.receiver.get()
                elif value['operation'] == 'write' and isinstance(value['body'], str):
                    body = self.receiver.post(value['body'].encode(), value['token'])
                else:
                    raise Rejected()
                response = {'status': 200, 'body': body}
            except Rejected as exc:
                response = {'status': exc.status, 'body': {'error': 'presence_rejected'}}
            except Exception:
                pass
            try:
                connection.settimeout(.1)
                connection.sendall(encode(response))
            except OSError:
                pass

    def close(self):
        if self.server is not None:
            self.server.close()
        if self.socket_identity:
            try:
                current = os.stat('receiver.sock', dir_fd=self.store.fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (self.socket_identity.st_dev, self.socket_identity.st_ino):
                    os.unlink('receiver.sock', dir_fd=self.store.fd)
            except FileNotFoundError:
                pass
        if self.lock is not None:
            os.close(self.lock)
        self.store.close()


def serve(directory, generation, ready):
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    authority = None
    try:
        authority = Authority(directory, generation)
        ready.send(True)
        ready.close()
        while True:
            authority.once()
    except KeyboardInterrupt:
        pass
    except Exception:
        try:
            ready.send(False)
        except (OSError, EOFError):
            pass
    finally:
        ready.close()
        if authority is not None:
            authority.close()


def request(directory, generation, operation, body=None, token=None):
    # No state directory creation, stale socket deletion, or lazy authority start.
    store = PrivateStore(directory)
    try:
        info = os.stat('receiver.sock', dir_fd=store.fd, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise Unavailable()
        value = {'generation': generation, 'operation': operation, 'body': body, 'token': token}
        raw = encode(value)
        if len(raw) > LIMIT:
            raise Rejected()
        end = time.monotonic()+1.5
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(f'/proc/self/fd/{store.fd}/receiver.sock')
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise Unavailable()
            connection.sendall(raw)
            connection.shutdown(socket.SHUT_WR)
            result = frame(connection, end)
        if (not isinstance(result, dict) or set(result) != {'status', 'body'}
                or type(result['status']) is not int or not isinstance(result['body'], dict)):
            raise Unavailable()
        return result
    finally:
        store.close()
