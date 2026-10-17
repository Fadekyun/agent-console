#!/usr/bin/python3
"""Operator-only held-worker release from explicitly reviewed native receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import time

RESULTS = Path('/var/lib/agc-worker-qualification-results')
WORKER = Path('/usr/local/lib/agc-worker/worker.py')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def protected_bytes(path, limit=2*1024**2, *, owner=0, boundary=Path('/')):
    """Read one bounded regular file below protected, non-symlink ancestry."""
    path, boundary = Path(path), Path(boundary)
    require(path.is_absolute() and path != boundary and path.is_relative_to(boundary) and '..' not in path.parts,
            'receipt_path_not_absolute_or_outside_boundary')
    current = path.parent
    while True:
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022,
                'unprotected_ancestor')
        if current == boundary:
            break
        current = current.parent
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == owner
                and not info.st_mode & 0o022 and info.st_size <= limit, 'unprotected_or_oversized_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit+1)
        require(len(data) == info.st_size and len(data) <= limit, 'file_changed_or_oversized')
        return data
    finally:
        os.close(fd)


def reviewed_json(path, digest):
    require(bool(HEX.fullmatch(digest)), 'invalid_receipt_digest')
    require(Path(path).parent == RESULTS, 'receipt_outside_results_directory')
    data = protected_bytes(path)
    require(hashlib.sha256(data).hexdigest() == digest, 'reviewed_receipt_changed')
    value = json.loads(data)
    require(isinstance(value, dict), 'invalid_receipt')
    return value


def namespace_proof(value, uid, gid):
    require(isinstance(value, dict) and value.get('cwd') == '/run/agc-job/workspace'
            and value.get('uid') == uid and value.get('gid') == gid
            and value.get('job_root_mode') == '0710' and value.get('metadata_mode') == '0640'
            and value.get('private_mount') is True, 'private_namespace_proof_missing')


def validate_native(value, expected, uid, gid):
    require(value.get('version') == 1 and value.get('state') == 'passed'
            and value.get('worker_source_sha256') == expected, 'native_worker_or_state_mismatch')
    cases = value.get('cases')
    modes = {'success': 'succeeded', 'oom': 'oom', 'timeout': 'timeout', 'lease': 'unknown', 'timer': 'unknown'}
    require(isinstance(cases, list) and len(cases) == 5
            and {x.get('mode') for x in cases} == set(modes), 'five_distinct_native_cases_required')
    for case in cases:
        require(case.get('production_umask') == '0077' and case.get('process_group_empty') is True
                and case.get('duplicate_did_not_execute') is True, 'native_containment_proof_missing')
        namespace_proof(case.get('namespace'), uid, gid)
        require(case['namespace'].get('host_state_inaccessible') is True, 'private_host_state_proof_missing')
        receipt = case.get('receipt', {})
        require(receipt.get('state') == modes[case['mode']] and receipt.get('termination_confirmed') is True,
                'native_terminal_receipt_missing')
        if case['mode'] == 'success':
            require(case.get('atomic_lease_replacement') is True, 'atomic_lease_proof_missing')
        if case['mode'] == 'lease':
            require(receipt.get('reason') == 'lease_lost' and receipt.get('checkpoint_requested') is True
                    and HEX.fullmatch(receipt.get('checkpoint_sha256', ''))
                    and 79 <= case.get('elapsed_seconds', 0) <= 90, 'lease_stop_proof_missing')
        if case['mode'] == 'timer':
            timer = case.get('timer_unit_result', {})
            require(case.get('timer_stopped_without_watchdog') is True
                    and timer.get('Result') == 'success' and timer.get('ExecMainStatus') == '0'
                    and 83 <= case.get('elapsed_seconds', 0) <= 90, 'independent_timer_proof_missing')


def validate_toolchains(value, expected, uid, gid):
    require(value.get('version') == 1 and value.get('passed') is True
            and value.get('worker_source_sha256') == expected and value.get('production_umask') == '0077',
            'toolchain_worker_or_umask_mismatch')
    results = value.get('results')
    require(isinstance(results, list) and len(results) == 2
            and {x.get('toolchain') for x in results} == {'debian12', 'debian13'},
            'both_toolchains_required')
    for item in results:
        require(item.get('passed') is True, 'toolchain_failed')
        namespace_proof(item.get('namespace'), uid, gid)


def validate_seal(host, expected, now=None):
    require(host.get('policy_verified') is True and host.get('qualified_worker_sha256') == expected,
            'matching_windows_policy_seal_required')
    require(-5 <= (time.time() if now is None else now)-host.get('observed_at', 0) <= 30, 'policy_sample_stale')


def release(args):
    require(os.geteuid() == 0 and os.environ.get('WSL_DISTRO_NAME') == 'AGC-Worker', 'operator_worker_context_required')
    require(bool(HEX.fullmatch(args.worker_sha256)), 'invalid_worker_digest')
    require(hashlib.sha256(protected_bytes(WORKER)).hexdigest() == args.worker_sha256, 'installed_worker_changed')
    account = pwd.getpwnam('agcjob')
    validate_native(reviewed_json(args.native_receipt, args.native_sha256), args.worker_sha256, account.pw_uid, account.pw_gid)
    validate_toolchains(reviewed_json(args.toolchain_receipt, args.toolchain_sha256), args.worker_sha256, account.pw_uid, account.pw_gid)
    root = Path('/usr/local/lib/agc-worker-adapters')
    installed = json.loads(protected_bytes(root/'installed.json'))
    require(isinstance(installed.get('files'), dict) and bool(installed['files']), 'installed_adapter_manifest_missing')
    for name, digest in installed['files'].items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative) == name,
                'invalid_installed_adapter_path')
        require(hashlib.sha256(protected_bytes(root/relative)).hexdigest() == digest, 'installed_adapter_changed')
    registry = json.loads(protected_bytes('/etc/agc-worker/adapters.json'))
    require(set(registry) == {'yuyutei.collect', 'yuyutei.parse', 'agc.build', 'agc.test', 'tv.build',
                             'points.weekly', 'points.player_events', 'bushi.run'}, 'adapter_registry_changed')
    credential = Path('/etc/agc-worker/credentials/agc-bushi-organizer-state.json')
    info = credential.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
            'credential_not_private')
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10); client.connect('/run/agc-worker/rpc.sock')
        client.sendall(b'{"protocol_version":1,"action":"status"}\n')
        with client.makefile('rb') as stream:
            status = json.loads(stream.readline(524288))
    require(status.get('ok') is True and status.get('active') is None, 'worker_not_drained')
    validate_seal(status.get('host', {}), args.worker_sha256)
    hold = Path('/etc/agc-worker/hold')
    protected_bytes(hold)
    hold.unlink()
    fd = os.open(hold.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    print(json.dumps({'state': 'qualified_operator_hold_released', 'worker_sha256': args.worker_sha256,
        'native_evidence': args.native_receipt, 'native_sha256': args.native_sha256,
        'toolchain_evidence': args.toolchain_receipt, 'toolchain_sha256': args.toolchain_sha256,
        'released_at': time.time(), 'queue_owner_hold': 'unchanged', 'capacity_and_schedule_gates': 'unchanged'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('worker-sha256', 'native-receipt', 'native-sha256', 'toolchain-receipt', 'toolchain-sha256'):
        parser.add_argument('--'+name, required=True)
    release(parser.parse_args())
