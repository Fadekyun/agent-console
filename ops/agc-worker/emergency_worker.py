#!/usr/bin/python3
"""Root-selected CT102 emergency policy, never selectable by a job request.

Fixed Bushi paid-participation adapter only; the queue owns the cross-host
organizer fence. No application source, credential path or argv comes from RPC.
"""
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker

GIB = 1024**3
MIB = 1024**2
worker.STATE_ROOT = Path('/var/lib/bushi-emergency')
worker.CONFIG_ROOT = Path('/etc/bushi-emergency')
worker.RUNTIME_ROOT = Path('/run/bushi-emergency')
worker.JOB_SLICE = 'bushi-emergency-jobs.slice'
worker.CONTROL_SLICE = 'bushi-emergency.slice'
worker.JOB_USER = 'bushi-emergency-job'
worker.SLICE_CPU_QUOTA = worker.PRESSURE_CPU_QUOTA = '50%'
worker.CRITICAL_HOST_MEMORY_BYTES = 2 * GIB
worker.MAX_ARTIFACT = 16 * MIB
SOURCE_FIELDS = {'observed_at', 'physical_host', 'memory_total_bytes', 'memory_available_bytes',
                 'cpu_count', 'ct102_memory_limit_bytes', 'memory_full_avg60', 'io_some_avg60',
                 'thinpool_data_percent', 'thinpool_metadata_percent'}
FIXED_ARGV = ['/usr/bin/node', '/usr/local/lib/agc-worker-adapters/agc/scripts/compute/bushi-run.cjs']


def trusted_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('untrusted_emergency_policy')
    value = worker.read_json(path)
    if not isinstance(value, dict):
        raise ValueError('invalid_emergency_policy')
    return value


def local_guest():
    # The cgroup namespace root is the LXC boundary, not the service's child
    # cgroup. Guest MemAvailable alone can expose host memory on some LXCs.
    maximum = (Path('/sys/fs/cgroup') / 'memory.max').read_text().strip()
    if maximum == 'max':
        raise ValueError('guest_memory_unbounded')
    limit = int(maximum)
    used = int(Path('/sys/fs/cgroup/memory.current').read_text())
    scratch = worker.STATE_ROOT / 'jobs'
    mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    if not any(fields[1] == str(scratch) and fields[2] == 'tmpfs' for fields in mounts):
        raise ValueError('emergency_scratch_not_tmpfs')
    info = os.statvfs(scratch)
    if info.f_blocks * info.f_frsize > 512 * MIB:
        raise ValueError('emergency_scratch_exceeds_cap')
    persistent = os.statvfs(worker.STATE_ROOT)
    return {'guest_memory_limit_bytes': limit, 'guest_available_bytes': max(0, limit-used),
            'guest_disk_free_bytes': persistent.f_bavail * persistent.f_frsize,
            'scratch_available_bytes': info.f_bavail * info.f_frsize,
            'scratch_limit_bytes': info.f_blocks * info.f_frsize}


def validate_sample(sample, now=None):
    now = time.time() if now is None else now
    if not isinstance(sample, dict) or set(sample) != SOURCE_FIELDS or sample['physical_host'] != 'n100':
        raise ValueError('invalid_emergency_host_sample')
    for key in SOURCE_FIELDS - {'physical_host'}:
        value = sample[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid_emergency_host_sample')
    if not -5 <= now-sample['observed_at'] <= 30:
        raise ValueError('stale_emergency_host_sample')
    if not (0 < sample['memory_available_bytes'] <= sample['memory_total_bytes']
            and 1 <= sample['cpu_count'] <= 4096
            and 0 < sample['ct102_memory_limit_bytes'] <= 2 * GIB):
        raise ValueError('invalid_emergency_host_capacity')
    if any(sample[key] > 100 for key in ('memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent', 'thinpool_metadata_percent')):
        raise ValueError('invalid_emergency_host_pressure')
    return sample


def emergency_admission(host, now=None, required_memory=0):
    holds = []
    try:
        sample = validate_sample(host.get('n100_sample'), now)
        guest = local_guest()
    except (ValueError, OSError, TypeError, AttributeError):
        return ['emergency_headroom_unverified']
    if guest['guest_memory_limit_bytes'] != sample['ct102_memory_limit_bytes']:
        holds.append('guest_ceiling_mismatch')
    minimum = max(4 * GIB, 2 * GIB + required_memory) if required_memory else 2 * GIB
    if sample['memory_available_bytes'] < minimum:
        holds.append('n100_memory_reserve')
    guest_reserve = 512 * MIB
    if guest['guest_available_bytes'] < required_memory + guest_reserve:
        holds.append('guest_memory_reserve')
    for field, limit in (('memory_full_avg60', 1), ('io_some_avg60', 10),
                         ('thinpool_data_percent', 80), ('thinpool_metadata_percent', 70)):
        if sample[field] >= limit:
            holds.append('n100_' + field)
    if guest['scratch_available_bytes'] < (64 * MIB if required_memory else 8 * MIB):
        holds.append('scratch_headroom')
    if guest['guest_disk_free_bytes'] < 6 * GIB + (512 * MIB if required_memory else 0):
        holds.append('persistent_disk_headroom')
    if host.get('policy_verified') is not True:
        holds.append('policy_unverified')
    return holds


worker.admission_holds = emergency_admission
BaseWorker = worker.Worker


class EmergencyWorker(BaseWorker):
    def __init__(self, state=None, config=None):
        super().__init__(state, config)
        self.policy = trusted_json(self.config / 'policy.json')
        if self.policy.get('version') != 1 or not worker.HEX64.fullmatch(self.policy.get('source_artifact', {}).get('sha256', '')):
            raise ValueError('invalid_emergency_policy')
        if set(self.adapters) != {'bushi.emergency'}:
            raise ValueError('emergency_adapter_registry_not_fixed')
        adapter = self.adapters['bushi.emergency']
        if (adapter.get('argv') != FIXED_ARGV or adapter.get('memory_bytes') != GIB
                or adapter.get('cpus') != 1 or adapter.get('cpu_quota_percent') != 50
                or adapter.get('timeout_seconds') != 1200
                or adapter.get('credential_ids') != ['agc-bushi-organizer-state.json']
                or adapter.get('production_writes') is not True or adapter.get('root_directory')):
            raise ValueError('emergency_adapter_limits_not_fixed')
        source = self.state / 'objects' / self.policy['source_artifact']['sha256']
        if (source.stat().st_size != self.policy['source_artifact'].get('size')
                or worker.digest_file(source) != source.name
                or not 0 < source.stat().st_size <= worker.MAX_ARTIFACT):
            raise ValueError('installed_source_artifact_mismatch')
        reviewed = trusted_json(Path('/usr/local/lib/agc-worker-adapters/agc/.agc-worker-reviewed.json'))
        if reviewed.get('source_sha') != self.policy.get('installed_source_sha'):
            raise ValueError('installed_source_revision_mismatch')
        wrapper = Path(FIXED_ARGV[1])
        info = wrapper.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or reviewed.get('wrapper_sha256') != worker.digest_file(wrapper)):
            raise ValueError('installed_wrapper_mismatch')
        worker.credential_properties('bushi.emergency', adapter)
        self.adapters['bushi.emergency']['enabled'] = self.policy.get('enabled') is True

    def sampled_host(self, sample):
        disk = os.statvfs(self.state)
        guest = local_guest()
        return {'observed_at': sample['observed_at'], 'n100_sample': sample,
                'policy_verified': self.policy.get('enabled') is True,
                'memory_available_bytes': sample['memory_available_bytes'],
                'memory_total_bytes': sample['memory_total_bytes'],
                'worker_memory_bytes': max(0, guest['guest_memory_limit_bytes']-guest['guest_available_bytes']),
                'disk_total_bytes': disk.f_blocks*disk.f_frsize,
                'disk_free_bytes': disk.f_bavail*disk.f_frsize,
                'worker_disk_used_bytes': 0, 'cpu_percent': 0}

    def status(self):
        result = super().status()
        guest = local_guest()
        result.update(worker_id='bushi-emergency', physical_host='n100', guest_id='lxc-102')
        result['telemetry'] = {**guest, 'sampled_at': self.host.get('observed_at'),
            'policy_verified': self.policy.get('enabled') is True,
            'installed_source_sha': self.policy['installed_source_sha'],
            'installed_source_artifact': self.policy['source_artifact']}
        result['installed_source_artifact'] = self.policy['source_artifact']
        return result

    def stage(self, request):
        # The only source is installed by the operator; no source bytes from a
        # remote producer are written or executed, even when it offers a blob.
        artifact = self.policy['source_artifact']
        if request.get('artifact_id') != artifact['sha256'] or request.get('size') != artifact['size']:
            raise ValueError('emergency_source_not_installed')
        return {'artifact_id': artifact['sha256'], 'offset': artifact['size'], 'complete': True}

    def start(self, request):
        binding = worker.validate_binding(request)
        previous = self.bound_receipt(binding)
        if previous['state'] != 'not_found':
            return previous
        manifest = request.get('manifest', {})
        if not isinstance(manifest, dict):
            raise ValueError('invalid_emergency_manifest')
        emergency = manifest.get('emergency', {})
        if not isinstance(emergency, dict):
            raise ValueError('invalid_emergency_deadline')
        deadline = emergency.get('deadline')
        if (binding['kind'] != 'bushi.emergency' or emergency.get('urgent') is not True
                or set(emergency) != {'urgent', 'deadline'} or type(deadline) is not int
                or not 0 < deadline-time.time() <= 1200):
            raise ValueError('explicit_current_emergency_deadline_required')
        if (manifest.get('source_sha') != self.policy['installed_source_sha']
                or manifest.get('source_artifact') != self.policy['source_artifact']
                or manifest.get('inputs_artifact') is not None):
            raise ValueError('emergency_source_not_installed')
        params = manifest.get('parameters', {})
        if (not isinstance(params, dict) or params.get('action') != 'participate'
                or not isinstance(params.get('orderId'), str) or manifest.get('production_writes') is not True):
            raise ValueError('emergency_paid_participation_only')
        try:
            order_id = str(uuid.UUID(params['orderId']))
        except ValueError:
            raise ValueError('emergency_order_uuid_required') from None
        if order_id != params['orderId'] or manifest.get('business_key') not in (None, 'agc:paid-participation:' + order_id):
            raise ValueError('emergency_business_key_mismatch')
        remaining = int(deadline-time.time())
        if remaining < 1:
            raise ValueError('emergency_deadline_elapsed')
        self.adapters['bushi.emergency']['timeout_seconds'] = min(1200, remaining)
        receipt = super().start(request)
        receipt['emergency_deadline'] = deadline
        self.persist(receipt)
        return receipt

    def dispatch(self, request):
        if isinstance(request, dict) and request.get('action') == 'host-sample':
            if request.get('protocol_version') != 1 or set(request) != {'protocol_version', 'action', 'sample'}:
                raise ValueError('invalid_host_sample_request')
            sample = validate_sample(request['sample'])
            with self.lock:
                if sample['observed_at'] < self.host.get('observed_at', 0):
                    raise ValueError('older_host_sample')
                self.host = self.sampled_host(sample)
                worker.atomic_json(worker.RUNTIME_ROOT / 'host.json', self.host)
            return {'protocol_version': 1, 'ok': True, 'sampled_at': sample['observed_at']}
        return super().dispatch(request)

    def tick(self):
        with self.lock:
            if self.active and time.time() >= self.active.get('emergency_deadline', float('inf')):
                self.terminate('emergency_deadline_elapsed')
                return
            super().tick()


worker.Worker = EmergencyWorker
if __name__ == '__main__':
    worker.main()
