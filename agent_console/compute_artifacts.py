"""Bounded content-addressed transfer store; never interprets archive contents."""
import base64
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat

from .compute_manifest import artifact, digest

CHUNK_BYTES = 256 * 1024
STORE_BUDGET = 20 * 1024 ** 3
DISK_RESERVE = 6 * 1024 ** 3


class ArtifactCapacityUnavailable(RuntimeError):
    """Temporary storage pressure; preserve the immutable upload for retry."""


class ComputeArtifacts:
    def __init__(self, state_dir):
        self.root = Path(state_dir) / 'compute-artifacts'
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('artifact store must not be a symlink')

    def _open(self, name, flags, mode=0o600):
        return os.open(self.root / name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)

    def _size(self, name):
        try:
            info = (self.root / name).lstat()
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('invalid artifact file')
        return info.st_size

    def has(self, description):
        artifact(description)
        return (self.root / description['sha256']).exists() and self._size(description['sha256']) == description['size']

    def offset(self, description):
        artifact(description)
        return description['size'] if self.has(description) else self._size(description['sha256'] + '.part')

    def put(self, artifact_id, *, size, offset, data, final=False):
        artifact({'sha256': artifact_id, 'size': size})
        if type(offset) is not int or offset < 0 or type(final) is not bool:
            raise ValueError('invalid chunk coordinates')
        if not isinstance(data, str) or len(data) > (CHUNK_BYTES + 2) // 3 * 4:
            raise ValueError('chunk exceeds limit')
        try:
            chunk = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise ValueError('invalid chunk encoding') from None
        if len(chunk) > CHUNK_BYTES or offset + len(chunk) > size:
            raise ValueError('chunk exceeds declared size')
        with os.fdopen(self._open('.lock', os.O_CREAT | os.O_RDWR), 'r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.has({'sha256': artifact_id, 'size': size}):
                return {'offset': size, 'complete': True}
            partial = artifact_id + '.part'
            current = self._size(partial)
            if current != offset:
                # Lost acknowledgements are resolved by the authoritative offset;
                # never overwrite a partially uploaded immutable object.
                return {'offset': current, 'complete': False}
            usage = sum(item.stat(follow_symlinks=False).st_size for item in os.scandir(self.root)
                        if item.is_file(follow_symlinks=False))
            if usage + len(chunk) > STORE_BUDGET or shutil.disk_usage(self.root).free - len(chunk) < DISK_RESERVE:
                raise ArtifactCapacityUnavailable('artifact store headroom unavailable')
            with os.fdopen(self._open(partial, os.O_CREAT | os.O_RDWR), 'r+b') as stream:
                stream.seek(offset)
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                current += len(chunk)
                if final:
                    if current != size:
                        raise ValueError('incomplete artifact')
                    stream.seek(0)
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if actual != artifact_id:
                        # Corrupt staging is not a usable immutable artifact.
                        os.unlink(self.root / partial)
                        raise ValueError('artifact digest mismatch')
            if final:
                os.replace(self.root / partial, self.root / artifact_id)
                directory = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return {'offset': current, 'complete': final}

    def get(self, artifact_id, *, offset=0, length=CHUNK_BYTES):
        digest(artifact_id)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= CHUNK_BYTES:
            raise ValueError('invalid fetch coordinates')
        with os.fdopen(self._open(artifact_id, os.O_RDONLY), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or offset > info.st_size:
                raise ValueError('invalid artifact')
            stream.seek(offset)
            chunk = stream.read(length)
            return {'artifact_id': artifact_id, 'offset': offset, 'size': info.st_size,
                    'data': base64.b64encode(chunk).decode('ascii'),
                    'eof': offset + len(chunk) == info.st_size}
