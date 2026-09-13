"""Maintenance-only installation of the eight selected-release CLI aliases.

Trusted release owners must use the shared lock when selecting current. This is
not a sandbox against a privileged process replacing files after installation.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import threading
import uuid

_held = threading.local()

NAMES = ('agentctl', 'agent-selector', 'agent-console-status', 'agent-console-logs')


class EntrypointError(ValueError):
    pass


def _identity(fd: int) -> tuple:
    s = os.fstat(fd)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _directory(path: Path) -> int:
    """Open every absolute path component without following directory links."""
    path = path.absolute()
    if '..' in path.parts:
        raise EntrypointError('path traversal is unsupported')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(parent: int, name: str, limit: int) -> tuple[int, bytes]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
            raise EntrypointError('release file is not a bounded private regular file')
        identity = _identity(fd)
        pieces = []
        remaining = limit + 1
        while remaining:
            block = os.read(fd, min(65536, remaining))
            if not block:
                break
            pieces.append(block)
            remaining -= len(block)
        data = b''.join(pieces)
        if len(data) > limit or _identity(fd) != identity:
            raise EntrypointError('release file changed during validation')
        return fd, data
    except BaseException:
        os.close(fd)
        raise


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EntrypointError('duplicate manifest key')
        result[key] = value
    return result


@contextmanager
def entrypoint_lock(state: Path, timeout: float = 5):
    directory = _directory(state)
    key = _identity(directory)[:2]
    held = getattr(_held, 'locks', {})
    _held.locks = held
    if key in held:
        os.close(directory)
        yield
        return
    fd = None
    try:
        fd = os.open('entrypoints.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or s.st_mode & 0o077:
            raise EntrypointError('entrypoint lock is unavailable')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise EntrypointError('entrypoint writer is busy') from None
                time.sleep(0.02)
        named = os.stat('entrypoints.lock', dir_fd=directory, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (s.st_dev, s.st_ino):
            raise EntrypointError('entrypoint lock changed')
        held[key] = fd
        try:
            yield
        finally:
            held.pop(key, None)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


class SelectedRelease:
    def __init__(self, releases: Path, stack: ExitStack):
        self.releases = releases.absolute()
        self.root_fd = _directory(self.releases)
        stack.callback(os.close, self.root_fd)
        self.current = os.stat('current', dir_fd=self.root_fd, follow_symlinks=False)
        if not stat.S_ISLNK(self.current.st_mode):
            raise EntrypointError('selected release is not a symlink')
        target = os.readlink('current', dir_fd=self.root_fd)
        if Path(target).is_absolute():
            if Path(target).parent != self.releases:
                raise EntrypointError('selected release escapes its root')
            target = Path(target).name
        if not re.fullmatch(r'release-[A-Za-z0-9._-]+', target):
            raise EntrypointError('selected release name is unsupported')
        self.name = target
        self.path = self.releases / target
        self.fd = _directory(self.path)
        stack.callback(os.close, self.fd)
        self.files = []
        manifest_fd, raw = _regular(self.fd, 'manifest.json', 2 * 1024 * 1024)
        stack.callback(os.close, manifest_fd)
        self.files.append(('manifest.json', manifest_fd, _identity(manifest_fd)))
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        files = manifest.get('files') if isinstance(manifest, dict) else None
        if not isinstance(files, dict) or not 1 <= len(files) <= 2048:
            raise EntrypointError('release manifest is invalid')
        if type(manifest.get('file_count')) is not int or manifest['file_count'] != len(files):
            raise EntrypointError('release manifest count is invalid')
        self.hashes = files
        for name in NAMES:
            if f'scripts/{name}' not in files:
                raise EntrypointError('release is missing a normal entrypoint')
        for relative, expected in files.items():
            if not isinstance(relative, str) or not relative or '\\' in relative:
                raise EntrypointError('manifest path is invalid')
            parts = relative.split('/')
            if any(part in {'', '.', '..'} for part in parts):
                raise EntrypointError('manifest path escapes release')
            if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                raise EntrypointError('manifest hash is invalid')
            parent = _directory(self.path.joinpath(*parts[:-1]))
            try:
                fd, data = _regular(parent, parts[-1], 64 * 1024 * 1024)
            finally:
                os.close(parent)
            stack.callback(os.close, fd)
            if hashlib.sha256(data).hexdigest() != expected:
                raise EntrypointError('release manifest hash mismatch')
            if relative in {f'scripts/{name}' for name in NAMES} and not os.fstat(fd).st_mode & 0o111:
                raise EntrypointError('release entrypoint is not executable')
            self.files.append((relative, fd, _identity(fd)))
        self.check()

    def check(self):
        current = os.stat('current', dir_fd=self.root_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mtime_ns) != (
                self.current.st_dev, self.current.st_ino, self.current.st_mtime_ns):
            raise EntrypointError('selected release changed')
        for path, expected_fd in ((self.releases, self.root_fd), (self.path, self.fd)):
            fd = _directory(path)
            try:
                if _identity(fd)[:2] != _identity(expected_fd)[:2]:
                    raise EntrypointError('release directory changed')
            finally:
                os.close(fd)
        for relative, expected_fd, expected in self.files:
            parent = _directory((self.path / relative).parent)
            try:
                value = os.stat(Path(relative).name, dir_fd=parent, follow_symlinks=False)
            finally:
                os.close(parent)
            observed = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if observed != expected or _identity(expected_fd) != expected:
                raise EntrypointError('validated release file changed')


def install_entrypoints(home: Path, state: Path, releases: Path, *, expected_selection: dict | None = None) -> dict:
    home, state, releases = home.absolute(), state.absolute(), releases.absolute()
    # No manager, database, provider or selected-source execution in this operation.
    with entrypoint_lock(state), ExitStack() as stack:
        home_fd = _directory(home)
        stack.callback(os.close, home_fd)
        selected = SelectedRelease(releases, stack)
        if expected_selection is not None:
            current = selected.current
            observed = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            if (observed != tuple(expected_selection['current'])
                    or _identity(selected.fd)[:2] != tuple(expected_selection['release'])
                    or any(selected.hashes.get(name) != digest for name, digest in expected_selection['files'].items())):
                raise EntrypointError('selected source changed since immutable capture')
        directories = (home / 'bin', home / '.local' / 'bin')
        old = {}
        # Validate every existing ancestor and destination before creating anything.
        for directory in (home / '.local', *directories):
            try:
                fd = _directory(directory)
            except FileNotFoundError:
                continue
            stack.callback(os.close, fd)
        for directory in directories:
            for name in NAMES:
                path = directory / name
                try:
                    value = path.lstat()
                except FileNotFoundError:
                    old[str(path)] = None
                else:
                    if not stat.S_ISLNK(value.st_mode):
                        raise EntrypointError('refusing to replace a manual entrypoint')
                    old[str(path)] = os.readlink(path)
        selected.check()
        # Create only the three allowlisted home directories through pinned parent FDs.
        for component in ('bin', '.local'):
            try:
                os.mkdir(component, 0o755, dir_fd=home_fd)
            except FileExistsError:
                pass
        local_fd = _directory(home / '.local')
        stack.callback(os.close, local_fd)
        try:
            os.mkdir('bin', 0o755, dir_fd=local_fd)
        except FileExistsError:
            pass
        target_dirs = []
        for directory in directories:
            fd = _directory(directory)
            stack.callback(os.close, fd)
            target_dirs.append((directory, fd))
        receipt = {'release': selected.name, 'previous_links': old, 'installed': []}
        receipt_name = 'entrypoints-' + uuid.uuid4().hex + '.json'
        state_fd = _directory(state)
        stack.callback(os.close, state_fd)
        receipts = 'entrypoint-receipts'
        try:
            os.mkdir(receipts, 0o700, dir_fd=state_fd)
        except FileExistsError:
            pass
        receipt_dir = _directory(state / receipts)
        stack.callback(os.close, receipt_dir)
        if os.fstat(receipt_dir).st_mode & 0o077:
            raise EntrypointError('receipt directory is not private')
        receipt_fd = os.open(receipt_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=receipt_dir)
        with os.fdopen(receipt_fd, 'w') as stream:
            json.dump(receipt, stream)
            stream.flush()
            os.fsync(stream.fileno())
        for directory, directory_fd in target_dirs:
            check = _directory(directory)
            try:
                if _identity(check)[:2] != _identity(directory_fd)[:2]:
                    raise EntrypointError('alias directory changed')
            finally:
                os.close(check)
            for name in NAMES:
                selected.check()
                try:
                    value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not stat.S_ISLNK(value.st_mode) or os.readlink(name, dir_fd=directory_fd) != old[str(directory / name)]:
                        raise EntrypointError('alias changed since prevalidation')
                except FileNotFoundError:
                    if old[str(directory / name)] is not None:
                        raise EntrypointError('alias disappeared since prevalidation') from None
                temporary = '.' + name + '-' + uuid.uuid4().hex
                try:
                    os.symlink(str(releases / 'current' / 'scripts' / name), temporary, dir_fd=directory_fd)
                    os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
                receipt['installed'].append(str(directory / name))
        selected.check()
        # The receipt preserves old metadata for diagnosis, never an unsafe auto-restore.
        return {'ok': True, 'release': selected.name, 'aliases': receipt['installed'],
                'receipt': str(state / receipts / receipt_name)}


def bootstrap_entrypoints(source: Path, home: Path, state: Path, releases: Path,
                          database: Path, config: Path) -> dict:
    """Explicit first install only; never recover an existing DB via old source."""
    for directory in (home, state):
        fd = _directory(directory)
        os.close(fd)
    from .admission import admission_lock
    from .schema_compatibility import prepare_database_for_release, release_schema_version
    with admission_lock(state), entrypoint_lock(state):
        current = releases / 'current'
        if not current.exists() and not current.is_symlink():
            if database.exists() or database.is_symlink():
                raise EntrypointError('existing database requires an explicit selected release')
            # Installer-owned initialization, separate from normal alias sync.
            from .database import Database
            from .deployer import Deployer
            deployer = Deployer(releases, object(), state_dir=state,
                                source_tracker='git' if (source / '.git').exists() else None)
            release = deployer.create_release(source)
            Database(database).migrate()
            prepare_database_for_release(database, release_schema_version(Path(release['release_path'])), config)
            # Admission and entrypoint locks are already held; use the same guarded
            # compatibility check above without recursively taking admission.lock.
            deployer.select_release(release['release_name'])
        return install_entrypoints(home, state, releases)
