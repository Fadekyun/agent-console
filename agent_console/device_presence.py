"""Fixed AGC presence contract and private durable ordering (no Console database).

Only the launcher-owned receiver constructs Receiver. HTTP workers are clients;
recreating a web application never establishes a new presence lifetime.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = 'proj-7e8a68b0c8444fa9b97276ef9437a65d'
DEVICE = 'ngF3guMi1g11CNTRL'
POST = '/api/integration/device-presence/agc-laptop'
GET = '/api/device-presence/agc-laptop'
HEADER = 'x-agc-presence-writer'
TTL = 180
LIMIT = 4096


class Unavailable(Exception):
    """Intentionally value-blind failure."""


class Rejected(Exception):
    def __init__(self, status=400):
        self.status = status


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def decode(raw):
    if len(raw) > LIMIT:
        raise Rejected()
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise Rejected()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected()))
    except (ValueError, UnicodeError) as exc:
        raise Rejected() from exc


def utc(value):
    text = datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='microseconds')
    return text.replace('.000000+00:00', 'Z').replace('+00:00', 'Z')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{6})?Z', value):
        raise Rejected()
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        if utc(result) != value:
            raise Rejected()
        return result
    except (ValueError, OverflowError) as exc:
        raise Rejected() from exc


def payload(raw):
    value = decode(raw)
    if (not isinstance(value, dict) or set(value) != {'device_id', 'state', 'observed_at'}
            or value['device_id'] != DEVICE or value['state'] not in ('online', 'offline')):
        raise Rejected()
    observed = timestamp(value['observed_at'])
    return value, observed, hashlib.sha256(encode(value)).hexdigest()


class Clock:
    """Measured intervals, 5ms plus 1000ppm slew allowance; anomalies latch."""
    def __init__(self, wall=time.time, mono=time.monotonic):
        self.wall, self.mono = wall, mono
        self.previous = None
        self.bad = False

    def sample(self):
        before, wall, after = self.mono(), self.wall(), self.mono()
        if not all(math.isfinite(x) for x in (before, wall, after)) or not 0 <= after-before <= .25:
            self.bad = True
        if self.previous:
            pb, pw, pa = self.previous
            allowance = .005 + .001 * max(0, before-pa)
            if (before < pa or wall < pw or wall-after > pw-pb+allowance
                    or wall-before < pw-pa-allowance):
                self.bad = True
        self.previous = before, wall, after
        if self.bad:
            raise Unavailable()
        return before, wall, after


def open_directory(path):
    """Walk without following any symlink; leaf must be private and owned."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Unavailable()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Unavailable()
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular(fd, mode=0o600):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode):
        raise Unavailable()
    return info


class PrivateStore:
    """Owned directory FD; no initialization on read, no following/FIFO reads."""
    def __init__(self, path):
        self.path = Path(path)
        self.fd = open_directory(path)

    def close(self):
        os.close(self.fd)

    def read(self, name, missing=False):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError:
            if missing:
                return None
            raise
        try:
            info = regular(fd)
            if info.st_size > LIMIT:
                raise Unavailable()
            raw = os.read(fd, LIMIT+1)
            if len(raw) > LIMIT:
                raise Unavailable()
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise Unavailable()
            if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
                raise Unavailable()
            return raw
        finally:
            os.close(fd)

    def write(self, name, value):
        # Validate any existing destination before atomic replacement.
        self.read(name, missing=True)
        temporary = '.pending-' + secrets.token_hex(16)
        raw = encode(value)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def verifier(self):
        value = self.read('writer.sha256')
        if value is None or not re.fullmatch(b'[0-9a-f]{64}\n?', value):
            raise Unavailable()
        return value.strip().decode('ascii')


class Receiver:
    """Single service process, serial calls; only ordering survives restart."""
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or Clock()
        _, self.started, _ = self.clock.sample()
        self.current = None
        self.failed = False
        self.order = self.read_order()

    def read_order(self):
        raw = self.store.read('ordering.json', missing=True)
        if raw is None:
            return None
        value = decode(raw)
        if (not isinstance(value, dict) or set(value) != {'observed_at', 'payload_sha256'}
                or not isinstance(value['payload_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', value['payload_sha256'])):
            raise Unavailable()
        timestamp(value['observed_at'])
        return value

    def check(self):
        if self.failed:
            raise Unavailable()
        try:
            if self.read_order() != self.order:
                raise Unavailable()
            return self.clock.sample()
        except Exception as exc:
            self.failed = True
            self.current = None
            raise Unavailable() from exc

    def post(self, raw, token):
        try:
            if (not isinstance(token, str) or not 32 <= len(token) <= 256
                    or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.store.verifier())):
                raise Rejected(403)
            value, observed, digest = payload(raw)
            before, wall, after = self.check()
            if not 0 <= wall-observed < TTL:
                raise Rejected()
            if self.order:
                previous = timestamp(self.order['observed_at'])
                if observed < previous:
                    raise Rejected(409)
                if observed == previous:
                    if (digest != self.order['payload_sha256'] or self.current is None
                            or after >= self.current['mono_end']):
                        raise Rejected(409)
                    return {**self.current['ack'], 'duplicate': True}
            if observed < self.started:
                raise Rejected(409)
            ack = dict(accepted=True, duplicate=False, payload_sha256=digest,
                       state=value['state'], observed_at=value['observed_at'],
                       received_at=utc(wall), expires_at=utc(observed+TTL))
            order = {'observed_at': value['observed_at'], 'payload_sha256': digest}
            self.store.write('ordering.json', order)
            self.order = order
            self.current = {'ack': ack, 'mono_end': before+(observed+TTL-wall)}
            # Persistence time consumes the same deadline. Never ACK a failed write.
            _, finished, end = self.check()
            if finished >= observed+TTL or end >= self.current['mono_end']:
                self.current = None
                raise Rejected(409)
            return ack
        except Rejected:
            raise
        except Exception as exc:
            self.failed = True
            self.current = None
            raise Unavailable() from exc

    def get(self):
        result = {'project_id': PROJECT, 'device_id': DEVICE, 'status': 'Status unavailable',
                  'observed_at': None, 'expires_at': None, 'meaning': 'Tailnet presence, not application health'}
        try:
            _, wall, after = self.check()
            if self.current:
                ack = self.current['ack']
                if (0 <= wall-timestamp(ack['observed_at']) < TTL
                        and after < self.current['mono_end']):
                    result.update(status=ack['state'].title(), observed_at=ack['observed_at'],
                                  expires_at=ack['expires_at'])
        except Exception:
            pass
        return result
