"""Bounded, read-only guest keeper over one already-established WSL connection.

Never reconnect, launch work, remove a hold, stop a unit, or terminate a distro.
The independent Windows task supplies this reviewed file through stdin once.
"""
import hashlib
import json
import socket
import sys
import time
from pathlib import Path

BOOT = Path('/proc/sys/kernel/random/boot_id')
HOLD = Path('/etc/agc-worker/hold')
WORKER = Path('/usr/local/lib/agc-worker/worker.py')
CGROUP = Path('/sys/fs/cgroup')
SOCKET = '/run/agc-worker/rpc.sock'
ALLOWED_WORKERS = {
    'c4e193682e3cdcbce5ce90ede8cb00687550b67908db29b40ef908b9d6782e02',
    'c6dacde95e76069672f4ab1a1524e0f997de83388c3700d57af0982ae23018d8',
}


def status():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(SOCKET)
            client.sendall(b'{"protocol_version":1,"action":"status"}\n')
            data = b''
            until = time.monotonic() + 1
            while b'\n' not in data:
                client.settimeout(max(0.001, until - time.monotonic()))
                chunk = client.recv(4096)
                if not chunk or len(data) + len(chunk) > 65536:
                    raise ValueError('status_frame')
                data += chunk
            reply = json.loads(data.split(b'\n', 1)[0])
        return {
            'ok': reply.get('ok') is True,
            'idle': 'active' in reply and reply['active'] is None,
            'held': 'operator_hold' in reply.get('admission', {}).get('holds', []),
            'boot_id': reply.get('boot_id'),
            'host_sampled_at': reply.get('host', {}).get('observed_at'),
            'new_watchdog_sample': 'qualified_worker_sha256' in reply.get('host', {}),
        }
    except (OSError, ValueError, TypeError):
        # A held supervisor restart is expected. No synthetic success.
        return None


def sample(expected_boot):
    boot = BOOT.read_text().strip()
    if boot != expected_boot or not HOLD.is_file() or HOLD.is_symlink():
        raise ValueError('boot_or_hold_changed')
    digest = hashlib.sha256(WORKER.read_bytes()).hexdigest()
    if digest not in ALLOWED_WORKERS:
        raise ValueError('unexpected_worker')
    populated = 0
    for group in CGROUP.rglob('agc-job-*.service'):
        events = dict(line.split() for line in (group / 'cgroup.events').read_text().splitlines())
        if events.get('populated') != '0':
            populated += 1
    return {'worker_boot_id': boot, 'worker_sha256': digest, 'hold_present': True,
            'populated_job_cgroups': populated, 'status': status()}


def run(expected_boot, deadline, *, clock=time.time, monotonic=time.monotonic, sleep=time.sleep, emit=print):
    remaining = deadline - clock()
    if not 0 < remaining <= 600:
        raise ValueError('invalid_deadline')
    monotonic_deadline = monotonic() + remaining
    while clock() < deadline and monotonic() < monotonic_deadline:
        result = sample(expected_boot)
        emit(json.dumps({'state': 'guarding', 'observed_at': clock(), **result}), flush=True)
        sleep(2)
    emit(json.dumps({'state': 'expired', 'observed_at': clock()}), flush=True)


if __name__ == '__main__':
    try:
        run(sys.argv[1], int(sys.argv[2]))
    except Exception as error:
        print(json.dumps({'state': 'failed', 'error_type': type(error).__name__,
                          'observed_at': time.time()}), flush=True)
        raise SystemExit(1)
