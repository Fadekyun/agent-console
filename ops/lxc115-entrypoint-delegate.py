"""Body embedded in the private sync helper; no fallback to a packaged CLI."""
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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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


def main():
    home = Path(sys.argv[1])
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
    for relative in ('scripts/install-entrypoints.py', 'agent_console/entrypoints.py', 'agent_console/__init__.py'):
        raw = regular_bytes(selected / relative, 262144)
        if hashlib.sha256(raw).hexdigest() != files.get(relative):
            raise ValueError('selected installer manifest mismatch')
    if (current.resolve(strict=True) != selected or identity(current.lstat()) != current_identity
            or identity(selected.stat())[:2] != selected_identity):
        raise ValueError('selected release changed')
    if any(identity(path.lstat()) != expected for path, expected in identities.items()):
        raise ValueError('selected installer file identity changed')
    subprocess.run([sys.executable, '-I', '-B', str(selected / 'scripts/install-entrypoints.py'),
                    '--home', str(home), '--state', str(state), '--releases', str(releases)], check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('selected entrypoint installer unavailable; no legacy fallback', file=sys.stderr)
        raise SystemExit(2)
