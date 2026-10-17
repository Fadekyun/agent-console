"""Bounded, read-only telemetry for the explicitly supported n100/CT115 pool.

No client-supplied host or command is accepted.  This first adapter only produces
a small resource report; it is not a shell runner or a laptop enrollment path.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import os
from pathlib import Path, PurePosixPath
import selectors
import shlex
import shutil
import socket
import stat
import subprocess
import time

from .compute_policy import HOST_GATE_REASONS


GIB = 1024 ** 3
MAX_PROBE_BYTES = 32768
PROBE_TIMEOUT_SECONDS = 12.0
DEFAULT_RESOURCE_SNAPSHOT = '/run/agent-console-host-resources.json'

# Both the volume and its backing pool are checked.  A different storage layout
# is unsupported, rather than silently borrowing another pool's free space.
_HOST_SCRIPT = r'''
import json, subprocess
def read(path):
    with open(path) as stream:
        value = stream.read(16385)
    if len(value) > 16384: raise ValueError('oversized probe')
    return value
def command(args):
    value = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, timeout=3, check=True).stdout
    if len(value) > 16384: raise ValueError('oversized probe')
    return value
def pressure(path, kind):
    row = next(line for line in read(path).splitlines() if line.startswith(kind + ' '))
    return float(dict(field.split('=', 1) for field in row.split()[1:])['avg60'])
memory = {}
for line in read('/proc/meminfo').splitlines():
    key, value = line.split(':', 1)
    if key in ('MemTotal', 'MemAvailable'):
        parts = value.split()
        if len(parts) != 2 or parts[1] != 'kB': raise ValueError('memory unit')
        memory[key] = int(parts[0]) * 1024
config = {}
for line in command(['/usr/bin/sudo', '-n', '/usr/sbin/pct', 'config', '115', '--current', '1']).splitlines():
    if ': ' in line:
        key, value = line.split(': ', 1)
        if key in ('rootfs', 'memory', 'hostname'): config[key] = value
if config.get('rootfs', '').split(',')[0] != 'local-lvm:vm-115-disk-0':
    raise ValueError('unsupported rootfs')
if config.get('hostname') != 'agent-console-staging': raise ValueError('guest identity')
rows = json.loads(command(['/usr/bin/sudo', '-n', '/usr/sbin/lvs', '--reportformat', 'json',
    '-o', 'lv_name,vg_name,pool_lv,segtype,data_percent,metadata_percent',
    'pve/data', 'pve/vm-115-disk-0']))['report'][0]['lv']
rows = [{key: value.strip() if isinstance(value, str) else value for key, value in row.items()}
    for row in rows]
pool = next(row for row in rows if row['vg_name'] == 'pve' and row['lv_name'] == 'data')
volume = next(row for row in rows if row['vg_name'] == 'pve' and row['lv_name'] == 'vm-115-disk-0')
if pool['segtype'] != 'thin-pool' or volume['pool_lv'] != 'data':
    raise ValueError('unsupported backing pool')
print(json.dumps({
    'host_cpu_count': __import__('os').sysconf('SC_NPROCESSORS_ONLN'),
    'host_memory_total_bytes': memory['MemTotal'],
    'host_available_bytes': memory['MemAvailable'],
    'guest_limit_bytes': int(config['memory']) * 1024 * 1024,
    'memory_full_avg60': pressure('/proc/pressure/memory', 'full'),
    'io_some_avg60': pressure('/proc/pressure/io', 'some'),
    'thinpool_data_percent': float(pool['data_percent']),
    'thinpool_metadata_percent': float(pool['metadata_percent']),
}))
'''

HOST_PROBE_COMMAND = (
    '/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=3', '-o', 'ConnectionAttempts=1', '-o', 'LogLevel=ERROR',
    '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
    '-o', 'SendEnv=-*', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
    'n100', '/usr/bin/python3 -c ' + shlex.quote(_HOST_SCRIPT),
)


class ProbeError(ValueError):
    """A probe failed; raw command output must never become a status message."""


def _transport(command: tuple[str, ...], timeout: float) -> str:
    """Capture at most 32 KiB without a shell, stderr disclosure, or temp files."""
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env)
    deadline = time.monotonic() + timeout
    output = bytearray()
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeError('probe_timeout')
                if not selector.select(remaining):
                    raise ProbeError('probe_timeout')
                chunk = os.read(process.stdout.fileno(), min(4096, MAX_PROBE_BYTES + 1 - len(output)))
                if not chunk:
                    selector.unregister(process.stdout)
                    break
                output.extend(chunk)
                if len(output) > MAX_PROBE_BYTES:
                    raise ProbeError('probe_oversized')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ProbeError('probe_unavailable')
        return output.decode('utf-8', errors='strict')
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


def _read_text(path: Path) -> str:
    with path.open() as stream:
        value = stream.read(MAX_PROBE_BYTES + 1)
    if len(value) > MAX_PROBE_BYTES:
        raise ProbeError('probe_oversized')
    return value


def _number(value: object, *, positive: bool = False, maximum: float | None = None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProbeError('invalid_telemetry')
    if (value < 0 or value > 2 ** 63 - 1 or not math.isfinite(value)
            or (positive and value == 0) or (maximum is not None and value > maximum)):
        raise ProbeError('invalid_telemetry')
    return value


def _memory_available(raw: str) -> int:
    rows = [line.split(':', 1)[1].split() for line in raw.splitlines() if line.startswith('MemAvailable:')]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != 'kB':
        raise ProbeError('guest_memory_unavailable')
    return int(_number(int(rows[0][0]))) * 1024


def host_snapshot_gate(path: str | None, now: float) -> str | None:
    """Honor the existing root sampler's veto; never substitute SSH for it.

    The host publishes this numeric snapshot into the guest as UID 0. The path is
    service configuration, never job input. Detailed compute limits still apply
    after this shared launch gate, including the stricter memory/storage reserves.
    """
    if not path:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                    or metadata.st_mode & 0o022 or metadata.st_size > 8192):
                raise ProbeError('untrusted snapshot')
            raw = stream.read(8193)
            if len(raw) > 8192:
                raise ProbeError('oversized snapshot')
            data = json.loads(raw)
        for key in ('sampled_at', 'host_available_bytes', 'host_full_psi_avg10',
                    'ct115_memory_headroom_bytes', 'ct115_disk_free_bytes', 'tmp_free_bytes'):
            _number(data[key])
        if not isinstance(data.get('maintenance'), bool):
            raise ProbeError('missing maintenance state')
        if data['maintenance']:
            return 'host_maintenance'
        if not -5 <= now - data['sampled_at'] <= 60:
            return 'host_snapshot_stale'
        for key, threshold, reason in (
            ('host_available_bytes', 2 * GIB, 'host_snapshot_memory_low'),
            ('ct115_memory_headroom_bytes', GIB // 2, 'host_snapshot_container_memory_low'),
            ('ct115_disk_free_bytes', 4 * GIB, 'host_snapshot_disk_low'),
            ('tmp_free_bytes', 10 * GIB, 'host_snapshot_temporary_disk_low'),
        ):
            if data[key] < threshold:
                return reason
        if data['host_full_psi_avg10'] > 5:
            return 'host_snapshot_memory_pressure'
    except (OSError, ValueError, KeyError, TypeError):
        return 'host_snapshot_unavailable'
    return None


class ComputeSampler:
    """Trusted server-side sampler; restart or a stale sample resets warm-up."""

    def __init__(self, filesystem_path: Path | str = '/', *,
                 transport: Callable[[tuple[str, ...], float], str] = _transport,
                 read_text: Callable[[Path], str] = _read_text,
                 disk_free: Callable[[Path], int] | None = None,
                 device_id: Callable[[Path], int] | None = None,
                 resource_snapshot: str | None = None,
                 hostname: Callable[[], str] = socket.gethostname):
        self.filesystem_path = Path(filesystem_path)
        self._transport = transport
        self._read = read_text
        self._disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self._device_id = device_id or (lambda path: path.stat().st_dev)
        self._hostname = hostname
        self._resource_snapshot = ((os.getenv('AGENT_CONSOLE_RESOURCE_SNAPSHOT') or DEFAULT_RESOURCE_SNAPSHOT)
                                   if resource_snapshot is None else resource_snapshot)
        self._healthy_since: float | None = None
        self._previous_sample: float | None = None

    def _guest_available(self, configured_limit: int) -> int:
        if self._hostname() != 'agent-console-staging':
            raise ProbeError('unsupported_guest')
        raw = self._read(Path('/proc/self/cgroup'))
        rows = [line[3:] for line in raw.splitlines() if line.startswith('0::')]
        if len(rows) != 1:
            raise ProbeError('cgroup_v2_unavailable')
        relative = PurePosixPath(rows[0])
        if not relative.is_absolute() or '..' in relative.parts:
            raise ProbeError('invalid_cgroup_path')
        root = Path('/sys/fs/cgroup')
        current = root.joinpath(*relative.parts[1:])
        headrooms = [_memory_available(self._read(Path('/proc/meminfo')))]
        while True:
            usage = int(_number(int(self._read(current / 'memory.current').strip())))
            limit = self._read(current / 'memory.max').strip()
            if limit != 'max':
                headrooms.append(max(0, int(_number(int(limit), positive=True)) - usage))
            if current == root:
                # The LXC parent is hidden by its cgroup namespace.  Its verified
                # Proxmox configuration supplies the missing outer memory cap.
                headrooms.append(max(0, configured_limit - usage))
                break
            current = current.parent
        return min(headrooms)

    def sample(self, *, enabled: bool = False, operator_hold: bool = True,
               now: float | None = None) -> dict:
        timestamp = time.time() if now is None else now
        _number(timestamp)
        if not isinstance(enabled, bool) or not isinstance(operator_hold, bool):
            raise ValueError('enabled and operator_hold must be booleans')
        result = {'physical_host': 'n100', 'guest_id': 'lxc-115', 'enabled': enabled,
                  'operator_hold': operator_hold, 'online': False, 'supported': False,
                  'sampled_at': timestamp, 'healthy_since': timestamp}
        if not enabled:
            self._healthy_since = self._previous_sample = None
            return {**result, 'error': 'disabled'}
        gate = host_snapshot_gate(self._resource_snapshot, timestamp)
        if gate:
            self._healthy_since = self._previous_sample = None
            return {**result, 'host_gate': gate, 'error': gate}
        try:
            # This adapter knows only CT115's root thin volume. A relocated
            # state/scratch volume needs its own verified storage mapping.
            if self._device_id(self.filesystem_path) != self._device_id(Path('/')):
                raise ProbeError('unsupported_filesystem')
            raw = self._transport(HOST_PROBE_COMMAND, PROBE_TIMEOUT_SECONDS)
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_PROBE_BYTES:
                raise ProbeError('probe_oversized')
            host = json.loads(raw)
            if not isinstance(host, dict):
                raise ProbeError('invalid_telemetry')
            for key in ('host_cpu_count', 'host_memory_total_bytes', 'guest_limit_bytes'):
                _number(host[key], positive=True)
            _number(host['host_available_bytes'], maximum=host['host_memory_total_bytes'])
            _number(host['guest_limit_bytes'], maximum=host['host_memory_total_bytes'])
            if host['host_cpu_count'] != int(host['host_cpu_count']):
                raise ProbeError('invalid_telemetry')
            for key in ('memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent', 'thinpool_metadata_percent'):
                _number(host[key], maximum=100)
            for key in ('host_cpu_count', 'host_memory_total_bytes', 'host_available_bytes',
                        'memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent', 'thinpool_metadata_percent'):
                result[key] = host[key]
            result['guest_available_bytes'] = self._guest_available(int(host['guest_limit_bytes']))
            result['guest_disk_free_bytes'] = int(_number(self._disk_free(self.filesystem_path)))
            healthy = (not operator_hold and host['memory_full_avg60'] < 1 and host['io_some_avg60'] < 10
                       and host['host_available_bytes'] >= 4 * GIB
                       and host['thinpool_data_percent'] < 80 and host['thinpool_metadata_percent'] < 70
                       and result['guest_available_bytes'] >= GIB
                       and result['guest_disk_free_bytes'] >= 6 * GIB)
            if not healthy:
                self._healthy_since = None
            elif (self._healthy_since is None or self._previous_sample is None
                  or not 0 <= timestamp - self._previous_sample <= 30):
                self._healthy_since = timestamp
            self._previous_sample = timestamp
            result.update(online=True, supported=True, error='',
                          healthy_since=self._healthy_since if self._healthy_since is not None else timestamp)
            return result
        except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
            self._healthy_since = self._previous_sample = None
            # Error text and SSH/lvs output can contain host-local data.  Status
            # deliberately records a fixed code only.
            return {**result, 'error': 'telemetry_unavailable'}


def maintenance_report(telemetry: Mapping) -> dict:
    """Return a compact allowlisted report, without reading files or executing."""
    result: dict = {'adapter': 'maintenance.report', 'execution': 'in_process_read_only',
                    'production_writes': False}
    for key in ('physical_host', 'guest_id'):
        expected = {'physical_host': 'n100', 'guest_id': 'lxc-115'}[key]
        if telemetry.get(key) == expected:
            result[key] = expected
    for key in ('enabled', 'operator_hold', 'online', 'supported'):
        value = telemetry.get(key)
        if isinstance(value, bool):
            result[key] = value
    for key in ('sampled_at', 'healthy_since', 'host_cpu_count', 'host_memory_total_bytes',
                'host_available_bytes', 'guest_available_bytes', 'guest_disk_free_bytes',
                'memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent', 'thinpool_metadata_percent'):
        try:
            result[key] = _number(telemetry.get(key))
        except ProbeError:
            continue
    error = telemetry.get('error')
    if isinstance(error, str) and error in {'disabled', 'telemetry_unavailable', '', *HOST_GATE_REASONS}:
        result['error'] = error
    return result


class LaptopSampler:
    """Central observation history, distinct from worker self-reported readiness."""
    def __init__(self, worker):
        self.worker = worker
        self.healthy_since = None
        self.last_sample = None
        self.boot_id = None

    def sample(self, *, enabled, operator_hold, now):
        from .compute_policy import admission, PROFILES, HEALTHY_WINDOW_SECONDS, laptop_schedule_reason
        value = {'physical_host': 'agc-laptop', 'guest_id': 'AGC-Worker',
                 'enabled': enabled, 'operator_hold': operator_hold, 'online': False}
        if not enabled or laptop_schedule_reason(now) == 'schedule_closed':
            value['readiness'] = 'schedule_closed' if enabled else 'worker_disabled'
            self.healthy_since = self.last_sample = None
            return value
        try:
            response = self.worker.call('status')
            raw = response['telemetry']
            if (response.get('worker_id') != 'agc-laptop' or not isinstance(response.get('boot_id'), str)
                    or not response['boot_id'] or not isinstance(raw, dict)
                    or not -5 <= now - _number(raw['sampled_at']) <= 30):
                raise ProbeError('invalid_worker_sample')
            allowed = ('host_cpu_count', 'host_memory_total_bytes', 'host_available_bytes',
                       'guest_available_bytes', 'guest_disk_free_bytes', 'host_disk_total_bytes',
                       'host_disk_free_bytes', 'worker_memory_bytes', 'worker_disk_used_bytes',
                       'host_cpu_percent', 'host_commit_percent', 'on_ac', 'policy_verified')
            value.update({key: raw.get(key) for key in allowed})
            value['worker_ready'] = isinstance(response.get('admission'), dict) and response['admission'].get('ready') is True
            value.update(online=True, sampled_at=now, capabilities=response.get('capabilities'),
                         healthy_since=now - HEALTHY_WINDOW_SECONDS)
            candidates = [profile for profile in PROFILES.values()
                          if profile.physical_host == 'agc-laptop'
                          and isinstance(value['capabilities'], list) and profile.name in value['capabilities']]
            smallest = min(candidates, key=lambda profile: (profile.memory_max_bytes, profile.cpu_cores), default=None)
            reason = admission(smallest, value, (), now) if smallest else 'adapter_unavailable'
            if (reason or self.boot_id != response['boot_id'] or self.last_sample is None
                    or now - self.last_sample > 30 or now < self.last_sample):
                self.healthy_since = None if reason else now
            self.boot_id, self.last_sample = response['boot_id'], now
            value['healthy_since'] = self.healthy_since if self.healthy_since is not None else now
            value['readiness'] = reason or ('ready' if now - value['healthy_since'] >= HEALTHY_WINDOW_SECONDS else 'warming_up')
        except (OSError, ValueError, TypeError, KeyError):
            self.healthy_since = self.last_sample = None
            value['online'] = False
        return value


# Fixed read-only CT102 volume probe, distinct from CT115's local report pool.
# A changed storage mapping fails closed until independently qualified.
_EMERGENCY_HOST_SCRIPT = _HOST_SCRIPT.replace("'115'", "'102'").replace(
    'vm-115-disk-0', 'vm-102-disk-0').replace(
    "if config.get('hostname') != 'agent-console-staging': raise ValueError('guest identity')",
    "if config.get('hostname') != 'bushi-automation': raise ValueError('guest identity')")
from .compute_emergency_probe import BOUNDARY_SCRIPT
_EMERGENCY_HOST_SCRIPT = _EMERGENCY_HOST_SCRIPT.replace('print(json.dumps({', BOUNDARY_SCRIPT + '\nprint(json.dumps({**boundary,').replace(
    "'guest_limit_bytes': int(config['memory']) * 1024 * 1024,", '')
EMERGENCY_HOST_PROBE_COMMAND = (*HOST_PROBE_COMMAND[:-1],
    '/usr/bin/python3 -c ' + shlex.quote(_EMERGENCY_HOST_SCRIPT))


class EmergencySampler:
    """Independent N100 observation plus authenticated, locally measured CT102."""
    def __init__(self, worker, *, transport=_transport, resource_snapshot=None):
        self.worker, self._transport = worker, transport
        self._resource_snapshot = ((os.getenv('AGENT_CONSOLE_RESOURCE_SNAPSHOT') or DEFAULT_RESOURCE_SNAPSHOT)
                                   if resource_snapshot is None else resource_snapshot)
        self.healthy_since = self.last_sample = self.boot_id = None

    def sample(self, *, enabled, operator_hold, now):
        from .compute_manifest import artifact
        from .compute_policy import admission, HEALTHY_WINDOW_SECONDS
        value = {'physical_host': 'n100', 'guest_id': 'lxc-102', 'enabled': enabled,
                 'operator_hold': operator_hold, 'online': False, 'sampled_at': now}
        gate = host_snapshot_gate(self._resource_snapshot, now) if enabled else None
        if not enabled or gate:
            self.healthy_since = self.last_sample = None
            return {**value, 'readiness': gate or 'worker_disabled', **({'host_gate': gate} if gate else {})}
        try:
            raw = self._transport(EMERGENCY_HOST_PROBE_COMMAND, PROBE_TIMEOUT_SECONDS)
            if not isinstance(raw, str) or len(raw.encode()) > MAX_PROBE_BYTES:
                raise ProbeError('invalid_host_sample')
            host = json.loads(raw)
            for key in ('host_cpu_count', 'host_memory_total_bytes', 'guest_limit_bytes'):
                _number(host[key], positive=True)
            _number(host['host_available_bytes'], maximum=host['host_memory_total_bytes'])
            if host['host_cpu_count'] % 1 or not GIB <= host['guest_limit_bytes'] <= 2 * GIB:
                raise ProbeError('invalid_host_sample')
            _number(host['guest_current_bytes'])
            if (host['guest_current_bytes'] % 1 or host['guest_cgroup_path'] != '/lxc/102'
                    or host['guest_namespace_root'] != '/lxc/102/ns'
                    or not -5 <= now-_number(host['boundary_observed_at']) <= 30):
                raise ProbeError('invalid_actual_guest_boundary')
            for key in ('memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent', 'thinpool_metadata_percent'):
                _number(host[key], maximum=100)
            sample = {'observed_at': host['boundary_observed_at'], 'physical_host': 'n100',
                      'memory_total_bytes': host['host_memory_total_bytes'],
                      'memory_available_bytes': host['host_available_bytes'],
                      'cpu_count': host['host_cpu_count'],
                      'ct102_memory_limit_bytes': host['guest_limit_bytes'],
                      'ct102_memory_current_bytes': host['guest_current_bytes'],
                      'ct102_cgroup_path': host['guest_cgroup_path'],
                      'ct102_namespace_root': host['guest_namespace_root'],
                      **{key: host[key] for key in ('memory_full_avg60', 'io_some_avg60',
                         'thinpool_data_percent', 'thinpool_metadata_percent')}}
            self.worker.call('host-sample', sample=sample)
            response = self.worker.call('status')
            telemetry = response['telemetry']
            if (response.get('worker_id') != 'bushi-emergency' or not isinstance(response.get('boot_id'), str)
                    or not response['boot_id'] or not isinstance(telemetry, dict)
                    or not -5 <= now - _number(telemetry['sampled_at']) <= 30
                    or telemetry.get('guest_memory_limit_bytes') != host['guest_limit_bytes']):
                raise ProbeError('invalid_worker_sample')
            for key in ('guest_available_bytes', 'guest_disk_free_bytes'):
                _number(telemetry[key])
            if telemetry['guest_available_bytes'] > host['guest_limit_bytes']:
                raise ProbeError('invalid_guest_headroom')
            if telemetry['guest_available_bytes'] > max(0, host['guest_limit_bytes']-host['guest_current_bytes']):
                raise ProbeError('worker_exceeds_observed_outer_headroom')
            artifact(telemetry['installed_source_artifact'])
            from re import fullmatch
            if not isinstance(telemetry.get('installed_source_sha'), str) or not fullmatch(
                    '[0-9a-f]{40}|[0-9a-f]{64}', telemetry['installed_source_sha']):
                raise ProbeError('invalid_installed_source')
            value.update({key: host[key] for key in ('host_cpu_count', 'host_memory_total_bytes',
                'host_available_bytes', 'memory_full_avg60', 'io_some_avg60',
                'thinpool_data_percent', 'thinpool_metadata_percent')})
            value.update({key: telemetry.get(key) for key in ('guest_available_bytes',
                'guest_memory_limit_bytes', 'guest_disk_free_bytes', 'policy_verified',
                'installed_source_sha', 'installed_source_artifact')})
            value.update(online=True, healthy_since=now-HEALTHY_WINDOW_SECONDS,
                         capabilities=response.get('capabilities'),
                         worker_ready=isinstance(response.get('admission'), dict)
                            and response['admission'].get('ready') is True)
            reason = admission('bushi.emergency', value, (), now)
            if (reason or self.boot_id != response['boot_id'] or self.last_sample is None
                    or not 0 <= now - self.last_sample <= 30):
                self.healthy_since = None if reason else now
            self.last_sample, self.boot_id = now, response['boot_id']
            value['healthy_since'] = self.healthy_since if self.healthy_since is not None else now
            value['readiness'] = reason or ('ready' if now-value['healthy_since'] >= HEALTHY_WINDOW_SECONDS else 'warming_up')
        except (OSError, ValueError, TypeError, KeyError, StopIteration, subprocess.SubprocessError):
            self.healthy_since = self.last_sample = None
            value.update(online=False, readiness='telemetry_unavailable')
        return value
