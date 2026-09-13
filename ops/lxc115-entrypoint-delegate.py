"""Body embedded in the private sync helper; no fallback to a packaged CLI."""
import fcntl
import io
import zipfile
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


identities = {}


def identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def regular_bytes(path, limit):
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('invalid selected installer file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if identity(os.fstat(fd)) != identity(info):
            raise ValueError('selected installer changed during read')
        identities[path] = identity(info)
        if len(raw) > limit:
            raise ValueError('oversize selected installer file')
        return raw
    finally:
        os.close(fd)


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate manifest key')
        value[key] = item
    return value


def capture(home):
    state = home / '.local/share/agent-console'
    releases = state / 'releases'
    current = releases / 'current'
    if not current.is_symlink():
        raise ValueError('selected release unavailable')
    current_identity = identity(current.lstat())
    selected = current.resolve(strict=True)
    selected_identity = identity(selected.stat())[:2]
    if selected.parent != releases or not selected.name.startswith('release-'):
        raise ValueError('selected release containment unavailable')
    for path in (releases, selected, selected / 'scripts', selected / 'agent_console'):
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink():
                raise ValueError('selected installer directory is linked')
    manifest = json.loads(regular_bytes(selected / 'manifest.json', 2097152), object_pairs_hook=unique)
    files = manifest['files']
    # Verify every source file the trusted installer imports before executing it.
    captured = {}
    for relative in ('scripts/install-entrypoints.py', 'agent_console/entrypoints.py', 'agent_console/__init__.py'):
        raw = regular_bytes(selected / relative, 262144)
        if hashlib.sha256(raw).hexdigest() != files.get(relative):
            raise ValueError('selected installer manifest mismatch')
        captured[relative] = raw
    if (current.resolve(strict=True) != selected or identity(current.lstat()) != current_identity
            or identity(selected.stat())[:2] != selected_identity):
        raise ValueError('selected release changed')
    if any(identity(path.lstat()) != expected for path, expected in identities.items()):
        raise ValueError('selected installer file identity changed')
    return captured, {'current': current_identity, 'release': selected_identity,
                      'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in captured.items()}}


def execute_captured(home, captured, expected):
    # Read-only descriptors do not prevent in-place writes. Copy exactly the
    # validated bytes into a sealed anonymous archive, including every project
    # import, and execute only this archive. No execution path reopens source.
    bootstrap = """import sys, zipfile
sys._agconsole_pinned = True
with zipfile.ZipFile(sys.argv[0]) as archive:
    import json
    sys._agconsole_selection = json.loads(archive.read('selection.json'))
    source = archive.read('scripts/install-entrypoints.py')
exec(compile(source, '<validated-install-entrypoints>', 'exec'),
     {'__name__': '__main__', '__file__': '<validated-install-entrypoints>'})
"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for relative, raw in captured.items():
            bundle.writestr(relative, raw)
        bundle.writestr('__main__.py', bootstrap)
        bundle.writestr('selection.json', json.dumps(expected))
    fd = os.memfd_create('console-validated-installer', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        data = archive.getvalue()
        with os.fdopen(os.dup(fd), 'wb') as stream:
            stream.write(data)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) != seals:
            raise ValueError('immutable installer boundary unavailable')
        state = home / '.local/share/agent-console'
        subprocess.run([sys.executable, '-I', '-B', '/proc/self/fd/' + str(fd),
                        '--home', str(home), '--state', str(state), '--releases', str(state / 'releases')],
                       pass_fds=(fd,), env={'PATH': '/usr/bin:/bin', 'HOME': str(home)}, check=True, timeout=15)
    finally:
        os.close(fd)


def main():
    home = Path(sys.argv[1])
    captured, expected = capture(home)
    execute_captured(home, captured, expected)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print('selected entrypoint installer unavailable; no legacy fallback', file=sys.stderr)
        raise SystemExit(2)
