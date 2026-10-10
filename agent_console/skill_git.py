"""Fetch a selected Git tree as inert files, never as a checked-out worktree."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from .skill_registry import ID, MAX_FILE, MAX_PACKAGE, SECRET, frontmatter

MAX_FETCH = 128 * 1024 * 1024
MAX_SECONDS = 60


def validate_source(source: str, revision: str, subdirectory: str) -> None:
    parsed = urlsplit(source)
    if (len(source) > 2000 or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in source) or SECRET.search(source)):
        raise ValueError('Git import requires an HTTPS URL without credentials, queries or fragments')
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', revision)
            or '..' in revision or '//' in revision or '@{' in revision):
        raise ValueError('Git revision must be HEAD, a ref name or a full commit SHA')
    path = PurePosixPath(subdirectory)
    if (len(subdirectory) > 500 or path.is_absolute() or '..' in path.parts
            or '\\' in subdirectory or ':' in subdirectory
            or any(ord(c) < 32 for c in subdirectory)):
        raise ValueError('Git package directory must be a relative path without traversal')


class GitReader:
    def __init__(self, root: Path):
        self.root = root
        self.repo = root / 'repository.git'
        self.deadline = time.monotonic() + MAX_SECONDS
        self.binary = shutil.which('git')
        if not self.binary:
            raise ValueError('Git is not installed')
        # No operator credentials, URL rewrites, hooks, templates, external
        # protocols or task-supplied Git environment are inherited.
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8',
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/bin/false',
                    'GIT_ALLOW_PROTOCOL': 'https'}

    def run(self, *args: str, limit: int = MAX_FILE) -> bytes:
        argv = [self.binary, '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=',
                '-c', 'http.followRedirects=false', '-c', 'http.lowSpeedLimit=1024',
                '-c', 'http.lowSpeedTime=10', '-c', 'protocol.allow=never',
                '-c', 'protocol.https.allow=always', *args]
        with tempfile.TemporaryFile(dir=self.root) as out, tempfile.TemporaryFile(dir=self.root) as err:
            process = subprocess.Popen(argv, cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            try:
                while True:
                    size = 0
                    for path in self.root.rglob('*'):
                        try:
                            if path.is_file():
                                size += path.stat().st_size
                        except FileNotFoundError:
                            # Git atomically renames incoming pack/index files.
                            continue
                    if (time.monotonic() > self.deadline or size > MAX_FETCH
                            or os.fstat(out.fileno()).st_size > limit
                            or os.fstat(err.fileno()).st_size > MAX_FILE):
                        raise ValueError('Git import exceeded its time or size budget')
                    if process.poll() is not None:
                        break
                    time.sleep(.05)
                if process.returncode:
                    # Native errors may include remote-supplied text or URLs.
                    raise ValueError('Git import failed; check the public URL, revision and package directory')
                out.seek(0)
                data = out.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('Git output exceeds the package limit')
                return data
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    def fetch(self, source: str, revision: str) -> str:
        self.run('init', '--bare', '--template=', str(self.repo))
        self.run('--git-dir=' + str(self.repo), 'fetch', '--depth=1', '--no-tags',
                 '--no-recurse-submodules', source, revision)
        sha = self.run('--git-dir=' + str(self.repo), 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode().strip()
        if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', sha):
            raise ValueError('Git did not resolve an exact commit')
        return sha

    def extract(self, sha: str, subdirectory: str) -> Path:
        tree = sha + (':' + subdirectory if subdirectory else '')
        records = self.run('--git-dir=' + str(self.repo), 'ls-tree', '-r', '-z', '-l', tree, limit=256 * 1024)
        entries = []
        total = 0
        for raw in records.split(b'\0'):
            if not raw:
                continue
            header, raw_path = raw.split(b'\t', 1)
            mode, kind, oid, size = header.split()
            try:
                rel = raw_path.decode('utf-8')
            except UnicodeError:
                raise ValueError('Git package paths must use UTF-8') from None
            path = PurePosixPath(rel)
            if (mode not in {b'100644', b'100755'} or kind != b'blob'
                    or path.is_absolute() or '..' in path.parts or '\\' in rel
                    or any(ord(c) < 32 for c in rel) or len(rel) > 1000 or SECRET.search(rel)):
                raise ValueError('Git skill tree contains an unsafe path, symlink or submodule')
            total += int(size)
            if int(size) > MAX_FILE or total > MAX_PACKAGE or len(entries) >= 512:
                raise ValueError('Git skill package exceeds file/count/size limits')
            entries.append((rel, oid.decode(), int(size), mode == b'100755'))
        package = self.root / 'package'
        package.mkdir(mode=0o700)
        for rel, oid, size, executable in entries:
            content = self.run('--git-dir=' + str(self.repo), 'cat-file', 'blob', oid)
            if len(content) != size:
                raise ValueError('Git blob size changed during import')
            target = package / rel
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o755 if executable else 0o644)
        try:
            name = frontmatter(package / 'SKILL.md').get('name')
            if not isinstance(name, str) or not ID.fullmatch(name) or SECRET.search(name):
                raise ValueError()
        except (OSError, UnicodeError, ValueError):
            raise ValueError('Git package requires SKILL.md with a valid portable name') from None
        selected = self.root / 'selected' / name
        selected.parent.mkdir()
        package.rename(selected)
        return selected


@contextmanager
def git_package(source: str, revision: str = 'HEAD', subdirectory: str = ''):
    validate_source(source, revision, subdirectory)
    subdirectory = '' if subdirectory in {'', '.'} else PurePosixPath(subdirectory).as_posix()
    with tempfile.TemporaryDirectory(prefix='console-skill-git-') as temp:
        reader = GitReader(Path(temp))
        sha = reader.fetch(source, revision)
        package = reader.extract(sha, subdirectory)
        yield package, {'kind': 'git', 'source': source, 'requested_revision': revision,
                        'revision': sha, 'subdirectory': subdirectory}
