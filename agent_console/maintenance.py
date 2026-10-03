"""Shared, non-shell configuration and release maintenance for supported hosts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_environment(path: Path) -> dict[str, str]:
    """Read literal assignments; never evaluate command/variable substitutions."""
    result = {}
    if not path.exists():
        return result
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, raw = line.partition('=')
        if not separator or not KEY.fullmatch(key):
            raise ValueError(f'invalid environment assignment at line {number}')
        # Legacy files allow unquoted spaces in values (systemd EnvironmentFile).
        if raw.startswith(('"', "'")):
            words = shlex.split(raw, comments=True)
            if len(words) != 1:
                raise ValueError(f'invalid environment value at line {number}')
            value = words[0]
        else:
            value = raw
        if '\x00' in value or '\n' in value or '\r' in value:
            raise ValueError(f'invalid environment value at line {number}')
        result[key] = value
    return result


def atomic_write(path: Path, content: str | bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content.encode() if isinstance(content, str) else content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def merge_environment(path: Path, defaults: dict[str, str]) -> dict[str, str]:
    """Merge resolved installer settings without dropping unknown keys/comments."""
    existing = read_environment(path)
    for key, value in defaults.items():
        if not KEY.fullmatch(key) or any(c in value for c in '\x00\n\r'):
            raise ValueError('invalid environment setting')
    lines = []
    seen = set()
    for line in path.read_text().splitlines() if path.exists() else []:
        stripped = line.strip().removeprefix('export ')
        key = stripped.partition('=')[0]
        if key in defaults:
            if key in seen:
                continue
            seen.add(key)
            if existing[key] != defaults[key]:
                line = f'{key}={shlex.quote(defaults[key])}'
        lines.append(line)
    for key, value in defaults.items():
        if key not in seen:
            lines.append(f'{key}={shlex.quote(value)}')
    atomic_write(path, '\n'.join(lines) + '\n')
    return dict(existing, **defaults)


def update_bundled_profiles(configured: Path, previous: Path, candidate: Path) -> dict[str, list[str]]:
    """Refresh only exact old defaults; retain custom text with an update copy."""
    report = {'updated': [], 'customized': [], 'unchanged': []}
    configured.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source in sorted(candidate.glob('*.md')):
        if source.is_symlink() or not source.is_file():
            continue
        target = configured / source.name
        old = previous / source.name
        if target.is_symlink():
            report['customized'].append(source.name)
            continue
        new = source.read_bytes()
        if target.is_file() and target.read_bytes() == new:
            report['unchanged'].append(source.name)
        elif not target.exists() or (old.is_file() and not old.is_symlink() and target.read_bytes() == old.read_bytes()):
            # Do not modify release-owned source: selecting candidate updates that
            # source separately; configured external role directories are mutable.
            if configured.resolve() == previous.resolve():
                report['unchanged'].append(source.name)
                continue
            atomic_write(target, new)
            report['updated'].append(source.name)
        else:
            pending = configured / '.available-updates' / candidate.parent.name / source.name
            atomic_write(pending, new)
            report['customized'].append(source.name)
    return report


def runtime_python(release: Path, state: Path) -> Path:
    candidate = release / '.runtime' / 'bin' / 'python'
    if candidate.is_file():
        return candidate
    # Historical releases predate per-release environments. Their original venv
    # is retained, never upgraded by the new maintenance path.
    return state / 'venv' / 'bin' / 'python'


def prepare_runtime(release: Path) -> Path:
    """Install dependencies only into the candidate's private environment."""
    runtime = release / '.runtime'
    inputs = [release / 'web/requirements.txt']
    # Base package dependencies must also be available to CLI/doctor.
    if (release / 'pyproject.toml').exists():
        inputs.append(release / 'pyproject.toml')
    digest = hashlib.sha256(b''.join(p.read_bytes() for p in inputs)).hexdigest()
    marker = runtime / '.requirements-sha256'
    if marker.is_file() and marker.read_text().strip() == digest and (runtime / 'bin/python').is_file():
        return runtime / 'bin/python'
    if runtime.exists():
        raise ValueError('candidate runtime exists but is incomplete; discard candidate and retry')
    try:
        subprocess.run([sys.executable, '-m', 'venv', str(runtime)], check=True)
        python = runtime / 'bin/python'
        requirements = [str(p) for p in inputs[:1]]
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', requirements[0]], check=True, stdout=sys.stderr)
        if len(inputs) > 1:
            import tomllib
            dependencies = tomllib.loads(inputs[1].read_text())['project'].get('dependencies', [])
            if dependencies:
                subprocess.run([str(python), '-m', 'pip', 'install', *dependencies], check=True, stdout=sys.stderr)
        subprocess.run([str(python), '-m', 'pip', 'check'], check=True, stdout=sys.stderr)
        subprocess.run([str(python), '-c', 'import fastapi, httpx, uvicorn, yaml'], check=True)
        atomic_write(marker, digest + '\n')
        return python
    except BaseException:
        shutil.rmtree(runtime, ignore_errors=True)
        raise


def sessions_preserved(before: list[dict], after: list[dict]) -> bool:
    """Compare durable identities; lifecycle/name/running changes are legitimate."""
    rows = {row['id']: row for row in after if row.get('managed', True)}
    return all(row['id'] in rows and rows[row['id']].get('created_at') == row.get('created_at')
               for row in before if row.get('managed', True))


def service_backend() -> str:
    explicit = os.getenv('AGENT_CONSOLE_SERVICE_BACKEND', '')
    if explicit:
        if explicit not in {'systemd', 'launchd', 'foreground'}:
            raise ValueError('unsupported service backend')
        return explicit
    if sys.platform == 'darwin':
        return 'launchd'
    if sys.platform.startswith('linux'):
        return 'systemd'
    raise ValueError('native Windows hosting is unsupported; use Ubuntu 24.04 under WSL2')


def service_action(action: str, *, home: Path | None = None, name: str = 'agent-console-web.service') -> None:
    backend = service_backend()
    home = home or Path.home()
    if backend == 'foreground':
        if action == 'restart':
            raise ValueError('foreground service must be restarted by its container supervisor')
        return
    if backend == 'systemd':
        subprocess.run(['systemctl', '--user', action, *([] if action == 'daemon-reload' else [name])], check=True, timeout=30)
        return
    label = 'com.agent-console.web'
    domain = f'gui/{os.getuid()}'
    plist = home / 'Library/LaunchAgents' / (label + '.plist')
    if action == 'enable':
        # Existing LaunchAgent can stay loaded; kickstart below replaces process.
        probe = subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True, timeout=10)
        if probe.returncode:
            subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True, timeout=30)
    elif action == 'restart':
        subprocess.run(['launchctl', 'kickstart', '-k', domain + '/' + label], check=True, timeout=30)
    elif action != 'daemon-reload':
        raise ValueError('unsupported launchd action')


def write_launch_agent(home: Path, runner: Path, state: Path) -> Path:
    target = home / 'Library/LaunchAgents/com.agent-console.web.plist'
    logs = state / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(target, plistlib.dumps({
        'Label': 'com.agent-console.web', 'ProgramArguments': [str(runner)],
        'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
        'ProcessType': 'Interactive', 'Umask': 0o077,
        'StandardOutPath': str(logs / 'service.stdout.log'),
        'StandardErrorPath': str(logs / 'service.stderr.log'),
        'EnvironmentVariables': {'PATH': os.environ.get('PATH', os.defpath)},
    }))
    return target


def service_pid(name='agent-console-web.service') -> int | None:
    backend = service_backend()
    if backend == 'systemd':
        result = subprocess.run(['systemctl', '--user', 'show', name, '--property=MainPID', '--value'], capture_output=True, text=True, check=True, timeout=5)
        pid = int(result.stdout.strip())
        return pid if pid > 0 else None
    if backend == 'launchd':
        result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/com.agent-console.web'], capture_output=True, text=True, check=True, timeout=5)
        match = re.search(r'\bpid = (\d+)', result.stdout)
        return int(match.group(1)) if match else None
    return None


def process_belongs_to_service(observed: str | None, master: int | None) -> bool:
    """Accept the service master or a live descendant (presence worker model)."""
    try:
        pid = int(observed or '')
        if not master or pid <= 1:
            return False
        for _ in range(8):
            if pid == master:
                return True
            result = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True, check=True, timeout=3)
            parent = int(result.stdout.strip())
            if parent <= 1 or parent == pid:
                return False
            pid = parent
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return False


def verify_health(host: str, port: int, release: Path) -> bool:
    import urllib.request
    if host in {'0.0.0.0', '::'}:
        host = '127.0.0.1'
    authority = f'[{host}]' if ':' in host else host
    try:
        pid = service_pid()
        if pid is None:
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{authority}:{port}/healthz', timeout=3) as response:
            return (response.status == 200 and response.read(16) == b'ok\n'
                    and process_belongs_to_service(response.headers.get('X-Agent-Console-Pid'), pid)
                    and response.headers.get('X-Agent-Console-Release') == release.resolve().name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


class CanaryOnlyMiddleware:
    """Canary boot validates imports/startup; no sessions or external actions."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] == 'http' and (scope.get('path') != '/healthz' or scope.get('method') != 'GET'):
            await send({'type': 'http.response.start', 'status': 503, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'Canary validation only'})
            return
        await self.app(scope, receive, send)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('environment', 'merge-environment', 'prepare-runtime'):
        command = sub.add_parser(name); command.add_argument('path', type=Path)
    redirect = sub.add_parser('redirect-profile-path'); redirect.add_argument('runtime', type=Path); redirect.add_argument('previous', type=Path); redirect.add_argument('candidate', type=Path)
    sub.add_parser('backend')
    profiles = sub.add_parser('update-profiles'); profiles.add_argument('configured', type=Path); profiles.add_argument('previous', type=Path); profiles.add_argument('candidate', type=Path); profiles.add_argument('--runtime', type=Path)
    lock = sub.add_parser('with-lock'); lock.add_argument('path', type=Path); lock.add_argument('argv', nargs=argparse.REMAINDER)
    inventory = sub.add_parser('snapshot-inventory'); inventory.add_argument('path', type=Path)
    health = sub.add_parser('health'); health.add_argument('host'); health.add_argument('port', type=int); health.add_argument('release', type=Path)
    service = sub.add_parser('service'); service.add_argument('action')
    launch = sub.add_parser('launch-agent'); launch.add_argument('runner', type=Path); launch.add_argument('state', type=Path)
    compare = sub.add_parser('sessions-preserved'); compare.add_argument('before', type=Path); compare.add_argument('after', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'redirect-profile-path':
        value = read_environment(args.runtime).get('AGENT_CONSOLE_PROFILE_DIR')
        if value and Path(value).resolve() == args.previous.resolve():
            merge_environment(args.runtime, {'AGENT_CONSOLE_PROFILE_DIR': str(args.candidate.parent.parent / 'current/agent-profiles')})
    elif args.command == 'update-profiles':
        if args.configured.resolve() == args.previous.resolve():
            if args.runtime is None:
                parser.error('immutable selected-release profiles require --runtime for redirection')
            # Point the setting at the selected release without editing either
            # immutable manifest-bound role directory.
            value = str(args.candidate.parent.parent / 'current/agent-profiles')
            merge_environment(args.runtime, {'AGENT_CONSOLE_PROFILE_DIR': value})
            print(json.dumps({'redirected': value}))
        else:
            print(json.dumps(update_bundled_profiles(args.configured, args.previous, args.candidate)))
    elif args.command == 'backend':
        print(service_backend())
    elif args.command == 'health':
        return 0 if verify_health(args.host, args.port, args.release) else 1
    elif args.command == 'with-lock':
        fd = os.open(args.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return subprocess.run(args.argv, env=dict(os.environ, AGENT_CONSOLE_UPDATE_LOCKED='1')).returncode
        finally:
            os.close(fd)
    elif args.command == 'snapshot-inventory':
        import sqlite3
        from contextlib import closing
        # Maintenance reads only the closed snapshot, never the live database.
        with closing(sqlite3.connect(args.path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as database:
            database.row_factory = sqlite3.Row
            print(json.dumps([dict(row) for row in database.execute('SELECT id,created_at,managed FROM sessions')]))
    elif args.command == 'environment':
        for key, value in read_environment(args.path).items():
            print(f'export {key}={shlex.quote(os.environ.get(key, value))}')
    elif args.command == 'merge-environment':
        merge_environment(args.path, read_environment(Path(sys.stdin.read().strip())))
    elif args.command == 'prepare-runtime':
        print(prepare_runtime(args.path))
    elif args.command == 'service':
        service_action(args.action)
    elif args.command == 'launch-agent':
        write_launch_agent(Path.home(), args.runner, args.state)
    elif args.command == 'sessions-preserved':
        return 0 if sessions_preserved(json.loads(args.before.read_text()), json.loads(args.after.read_text())) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
