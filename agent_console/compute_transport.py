"""One pinned outbound SSH identity, fixed RPC, bounded JSON; no shell runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time

from .workflow_store import canonical

MAX_RPC_BYTES = 512 * 1024
RPC_SECONDS = 12


class WorkerUnavailable(ValueError):
    pass


def trusted_json(path):
    """Only host-installed configuration may enroll workers or producers."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > 65536):
            raise PermissionError('compute configuration must be root-owned and non-writable')
        return json.load(stream)


def binding(job, attempt):
    manifest = json.loads(job['manifest_json'])
    return {'job_id': job['id'], 'attempt_id': attempt['id'],
            'lease_generation': attempt['generation'], 'kind': job['kind'],
            'source_sha': manifest['source_sha'], 'input_sha256': job['input_digest'],
            'manifest_sha256': job['manifest_digest']}


def validate_binding(response, expected):
    if response.get('binding') != expected:
        raise ValueError('worker_binding_mismatch')


class SSHWorker:
    actions = frozenset({'status', 'start', 'heartbeat', 'inspect', 'stop', 'stage', 'fetch', 'ack'})
    host = '100.111.183.86'
    user = 'agc-worker'
    rpc_command = 'agc-worker rpc'
    config_variable = 'AGENT_CONSOLE_COMPUTE_LAPTOP_CONFIG'

    def __init__(self, config):
        if config.get('enabled') is not True:
            raise ValueError('worker is not enabled')
        if config.get('host') != self.host or config.get('user') != self.user:
            raise ValueError('worker identity is not the enrolled fixed target')
        for key in ('identity_file', 'known_hosts_file'):
            if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
                raise ValueError('worker key and host pins require absolute configured paths')
        self.command = (
            '/usr/bin/ssh', '-F', '/dev/null', '-T', '-i', config['identity_file'],
            '-o', 'UserKnownHostsFile=' + config['known_hosts_file'],
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
            '-o', 'ConnectionAttempts=1', '-o', 'LogLevel=ERROR',
            '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'SendEnv=-*', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            self.user + '@' + self.host, self.rpc_command,
        )

    @classmethod
    def from_env(cls):
        location = os.getenv(cls.config_variable)
        return cls(trusted_json(location)) if location else None

    def call(self, action, **payload):
        if action not in self.actions:
            raise ValueError('unsupported worker action')
        data = (canonical({'protocol_version': 1, 'action': action, **payload}) + '\n').encode()
        if len(data) > MAX_RPC_BYTES:
            raise ValueError('worker request exceeds limit')
        process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        deadline, output, sent = time.monotonic() + RPC_SECONDS, bytearray(), 0
        try:
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise WorkerUnavailable('worker_timeout')
                    ready = selector.select(remaining)
                    if not ready:
                        raise WorkerUnavailable('worker_timeout')
                    for key, events in ready:
                        if events & selectors.EVENT_WRITE:
                            sent += os.write(key.fd, data[sent:sent + 65536])
                            if sent == len(data):
                                selector.unregister(key.fileobj)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, min(65536, MAX_RPC_BYTES + 1 - len(output)))
                            if not chunk:
                                selector.unregister(key.fileobj)
                            output.extend(chunk)
                            if len(output) > MAX_RPC_BYTES:
                                raise WorkerUnavailable('worker_response_oversized')
            if process.wait(timeout=max(.001, deadline - time.monotonic())) != 0:
                raise WorkerUnavailable('worker_unavailable')
            response = json.loads(output.decode('utf-8'))
            if not isinstance(response, dict) or response.get('protocol_version') != 1 or response.get('ok') is not True:
                raise WorkerUnavailable('worker_request_rejected')
            return response
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
            raise WorkerUnavailable('worker_transport_unavailable') from None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()


class EmergencySSHWorker(SSHWorker):
    """Separate root-enrolled key and fixed command; never an SSH hop or shell."""
    actions = SSHWorker.actions | {'host-sample'}
    host = '192.168.1.67'
    user = 'bushi-compute'
    rpc_command = 'agc-emergency rpc'
    config_variable = 'AGENT_CONSOLE_COMPUTE_EMERGENCY_CONFIG'
