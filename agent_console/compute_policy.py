"""Pure, fail-closed capacity policy for reviewed compute adapters.

Telemetry timestamps and the healthy observation window must come from the
trusted central sampler, never from a submitted job. Reservations include all
outstanding attempts, including unknown attempts; only reconciliation releases
them. This module performs no sampling, process creation, or runtime changes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time
import math
from types import MappingProxyType
from zoneinfo import ZoneInfo


MiB = 1024 ** 2
GiB = 1024 ** 3
TELEMETRY_MAX_AGE_SECONDS = 30
HEALTHY_WINDOW_SECONDS = 300
HOST_MIN_AVAILABLE_BYTES = 4 * GiB
HOST_RESERVE_BYTES = 2 * GiB
GUEST_RESERVE_BYTES = 512 * MiB
GUEST_DISK_RESERVE_BYTES = 6 * GiB
LAPTOP_RAM_CEILING_BYTES = 16_000_000_000
HOST_GATE_REASONS = frozenset({
    'host_snapshot_unavailable', 'host_snapshot_stale', 'host_maintenance',
    'host_snapshot_memory_low', 'host_snapshot_container_memory_low',
    'host_snapshot_disk_low', 'host_snapshot_temporary_disk_low',
    'host_snapshot_memory_pressure',
})


@dataclass(frozen=True)
class ComputeProfile:
    name: str
    physical_host: str
    guest_id: str
    cpu_cores: float
    memory_high_bytes: int
    memory_max_bytes: int
    memory_swap_max_bytes: int
    pids_max: int
    scratch_bytes: int
    timeout_seconds: int
    enabled: bool = False
    resources: tuple[str, ...] = ()


PROFILES: Mapping[str, ComputeProfile] = MappingProxyType({
    'maintenance.report': ComputeProfile(
        'maintenance.report', 'n100', 'lxc-115', .25, 384 * MiB,
        512 * MiB, 0, 64, 256 * MiB, 300, enabled=True),
    'yuyutei.collect': ComputeProfile(
        'yuyutei.collect', 'agc-laptop', 'AGC-Worker', 1, 1536 * MiB,
        2 * GiB, 0, 256, 8 * GiB, 5400, enabled=True),
    'yuyutei.parse': ComputeProfile(
        'yuyutei.parse', 'agc-laptop', 'AGC-Worker', 1, 1536 * MiB,
        2 * GiB, 0, 256, 8 * GiB, 5400, enabled=True),
    'agc.build': ComputeProfile(
        'agc.build', 'agc-laptop', 'AGC-Worker', 12, 6 * GiB,
        8 * GiB, 0, 512, 20 * GiB, 5400, enabled=True),
    'agc.test': ComputeProfile(
        'agc.test', 'agc-laptop', 'AGC-Worker', 12, 6 * GiB,
        8 * GiB, 0, 512, 20 * GiB, 5400, enabled=True),
    'tv.build': ComputeProfile(
        'tv.build', 'agc-laptop', 'AGC-Worker', 12, 6 * GiB,
        8 * GiB, 0, 512, 20 * GiB, 5400, enabled=True),
    'points.weekly': ComputeProfile(
        'points.weekly', 'agc-laptop', 'AGC-Worker', 1, 1536 * MiB,
        2 * GiB, 0, 256, 4 * GiB, 3600, enabled=True),
    'points.player_events': ComputeProfile(
        'points.player_events', 'agc-laptop', 'AGC-Worker', 1, 1536 * MiB,
        2 * GiB, 0, 256, 4 * GiB, 3600, enabled=True),
    'bushi.run': ComputeProfile(
        'bushi.run', 'agc-laptop', 'AGC-Worker', 1, 1536 * MiB,
        2 * GiB, 0, 256, 4 * GiB, 7200, enabled=True, resources=('bushi-organizer',)),
    'bushi.emergency': ComputeProfile(
        'bushi.emergency', 'n100', 'lxc-102', .5, 768 * MiB,
        GiB, 0, 256, 512 * MiB, 1200, enabled=True, resources=('bushi-organizer',)),
})


@dataclass(frozen=True)
class WorkerDescriptor:
    worker_id: str
    physical_host: str
    guest_id: str
    enabled: bool = False
    max_concurrent_jobs: int = 1
    verified_memory_total_bytes: int = 0
    verified_logical_cpus: int = 0
    ram_ceiling_bytes: int | None = None
    ram_reclaimable: bool = True
    schedule: str = 'always'
    timezone: str = 'Asia/Singapore'


# Inventory is descriptive, not a capacity reservation or fresh admission input.
# The owner-approved laptop ceiling includes VM/GPU overhead; no RAM is pinned.
WORKERS: Mapping[str, WorkerDescriptor] = MappingProxyType({
    'ct115-local': WorkerDescriptor(
        'ct115-local', 'n100', 'lxc-115',
        verified_memory_total_bytes=16_500_310_016, verified_logical_cpus=4),
    'bushi-emergency': WorkerDescriptor('bushi-emergency', 'n100', 'lxc-102',
        ram_ceiling_bytes=GiB, schedule='explicit urgent paid action only; all days'),
    'agc-laptop': WorkerDescriptor(
        'agc-laptop', 'agc-laptop', 'AGC-Worker',
        verified_memory_total_bytes=33_066_012_672, verified_logical_cpus=16,
        ram_ceiling_bytes=LAPTOP_RAM_CEILING_BYTES,
        schedule='Wednesday-Sunday; Sunday admission closes 23:45'),
})


def get_profile(name: str) -> ComputeProfile:
    try:
        return PROFILES[name]
    except (KeyError, TypeError):
        raise ValueError('unknown compute profile') from None


def _number(value: object) -> bool:
    # bool is an int subclass, but is never meaningful capacity/telemetry.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def laptop_schedule_reason(now: float) -> str | None:
    """Return the admission closure; never starts, wakes, or stops a laptop."""
    if not _number(now):
        return 'invalid_clock'
    try:
        local = datetime.fromtimestamp(now, ZoneInfo('Asia/Singapore'))
    except (ValueError, OverflowError, OSError):
        return 'invalid_clock'
    if local.weekday() < 2:
        return 'schedule_closed'
    if local.weekday() == 6 and local.time() >= time(23, 45):
        return 'schedule_draining'
    return None


def laptop_schedule_available(now: float) -> bool:
    return laptop_schedule_reason(now) is None


def laptop_availability(now: float, *, online: bool = False) -> str:
    """Explain planned availability without implying enrollment or capacity."""
    return laptop_schedule_reason(now) or (
        'worker_disabled' if online is True else 'waiting_for_worker')


def admission(
    profile: str | ComputeProfile,
    telemetry: Mapping[str, object] | None,
    reservations: Iterable[Mapping[str, object]],
    now: float,
) -> str | None:
    """Return a stable blocked reason, or None when capacity permits admission.

    The caller must acquire its durable reservation in the same transaction as
    this decision. This policy does not replace the physical-host concurrency
    constraint. Reservation subtraction is conservative even when resident RAM
    has already reduced MemAvailable.
    """
    if isinstance(profile, str):
        try:
            profile = get_profile(profile)
        except ValueError:
            return 'unknown_profile'
    if (not isinstance(profile, ComputeProfile) or not isinstance(profile.name, str)
            or PROFILES.get(profile.name) != profile):
        return 'invalid_profile'
    if not profile.enabled:
        return 'profile_disabled'
    if not _number(now):
        return 'invalid_clock'
    if not isinstance(telemetry, Mapping):
        return 'telemetry_missing'
    if telemetry.get('enabled') is not True:
        return 'worker_disabled'
    if telemetry.get('host_gate') is not None:
        gate = telemetry['host_gate']
        return gate if isinstance(gate, str) and gate in HOST_GATE_REASONS else 'invalid_telemetry:host_gate'
    if telemetry.get('operator_hold') is not False:
        return 'operator_hold'
    if telemetry.get('online') is not True:
        return 'waiting_for_worker'
    if telemetry.get('physical_host') != profile.physical_host:
        return 'physical_host_mismatch'
    if telemetry.get('guest_id') != profile.guest_id:
        return 'guest_mismatch'

    if profile.physical_host == 'agc-laptop':
        return laptop_admission(profile, telemetry, reservations, now)

    if profile.name == 'bushi.emergency':
        if telemetry.get('policy_verified') is not True:
            return 'emergency_policy_unverified'
        if telemetry.get('worker_ready') is not True:
            return 'worker_not_ready'
        if (not isinstance(telemetry.get('capabilities'), list)
                or 'bushi.emergency' not in telemetry['capabilities']):
            return 'adapter_unavailable'
        if (not _number(telemetry.get('guest_memory_limit_bytes'))
                or not GiB <= telemetry['guest_memory_limit_bytes'] <= 2 * GiB):
            return 'emergency_guest_ceiling_unverified'

    numeric = (
        'sampled_at', 'healthy_since', 'host_cpu_count',
        'host_memory_total_bytes', 'host_available_bytes',
        'guest_available_bytes', 'guest_disk_free_bytes',
        'memory_full_avg60', 'io_some_avg60',
        'thinpool_data_percent', 'thinpool_metadata_percent',
    )
    for key in numeric:
        if not _number(telemetry.get(key)):
            return 'invalid_telemetry:' + key
    sampled = telemetry['sampled_at']
    if sampled > now:
        return 'telemetry_from_future'
    if now - sampled > TELEMETRY_MAX_AGE_SECONDS:
        return 'telemetry_stale'
    if telemetry['healthy_since'] > sampled:
        return 'invalid_telemetry:healthy_since'
    if telemetry['host_cpu_count'] < 1 or telemetry['host_cpu_count'] % 1:
        return 'invalid_telemetry:host_cpu_count'
    if telemetry['host_memory_total_bytes'] <= 0:
        return 'invalid_telemetry:host_memory_total_bytes'
    if telemetry['host_available_bytes'] > telemetry['host_memory_total_bytes']:
        return 'invalid_telemetry:host_available_bytes'
    if telemetry['guest_available_bytes'] > telemetry['host_memory_total_bytes']:
        return 'invalid_telemetry:guest_available_bytes'
    for key in ('memory_full_avg60', 'io_some_avg60',
                'thinpool_data_percent', 'thinpool_metadata_percent'):
        if telemetry[key] > 100:
            return 'invalid_telemetry:' + key
    if telemetry['memory_full_avg60'] >= 1:
        return 'memory_pressure'
    if telemetry['io_some_avg60'] >= 10:
        return 'io_pressure'
    if telemetry['thinpool_data_percent'] >= 80:
        return 'thinpool_data_pressure'
    if telemetry['thinpool_metadata_percent'] >= 70:
        return 'thinpool_metadata_pressure'

    host_memory = guest_memory = guest_scratch = 0
    host_cpu = 0.0
    if not isinstance(reservations, Iterable) or isinstance(reservations, (str, bytes, Mapping)):
        return 'invalid_reservations'
    for reservation in reservations:
        if not isinstance(reservation, Mapping):
            return 'invalid_reservations'
        if not isinstance(reservation.get('physical_host'), str):
            return 'invalid_reservations'
        if not isinstance(reservation.get('guest_id'), str):
            return 'invalid_reservations'
        for key in ('memory_max_bytes', 'scratch_bytes', 'cpu_cores'):
            if not _number(reservation.get(key)):
                return 'invalid_reservations'
        if reservation['physical_host'] != profile.physical_host:
            continue
        host_memory += reservation['memory_max_bytes']
        host_cpu += reservation['cpu_cores']
        if reservation['guest_id'] == profile.guest_id:
            guest_memory += reservation['memory_max_bytes']
            guest_scratch += reservation['scratch_bytes']

    if host_cpu + profile.cpu_cores > telemetry['host_cpu_count']:
        return 'host_cpu_capacity'
    if telemetry['host_available_bytes'] < HOST_MIN_AVAILABLE_BYTES:
        return 'host_memory_headroom'
    if telemetry['host_available_bytes'] - host_memory - profile.memory_max_bytes < HOST_RESERVE_BYTES:
        return 'host_memory_reserve'
    if telemetry['guest_available_bytes'] - guest_memory - profile.memory_max_bytes < GUEST_RESERVE_BYTES:
        return 'guest_memory_reserve'
    if telemetry['guest_disk_free_bytes'] - guest_scratch - profile.scratch_bytes < GUEST_DISK_RESERVE_BYTES:
        return 'guest_disk_reserve'
    if sampled - telemetry['healthy_since'] < HEALTHY_WINDOW_SECONDS:
        return 'warming_up'
    return None


def laptop_admission(profile, telemetry, reservations, now):
    """Windows host and isolated guest must both fit the reclaimable budget.

    Do not apply N100 thinpool telemetry to the laptop, or count its GPU shared
    memory as extra capacity. Central and worker-side gates are both required.
    """
    closed = laptop_schedule_reason(now)
    if closed:
        return closed
    if telemetry.get('on_ac') is not True:
        return 'waiting_for_ac'
    if telemetry.get('policy_verified') is not True:
        return 'worker_policy_unverified'
    if telemetry.get('worker_ready') is not True:
        return 'worker_not_ready'
    capabilities = telemetry.get('capabilities')
    if not isinstance(capabilities, list) or profile.name not in capabilities:
        return 'adapter_unavailable'
    fields = ('sampled_at', 'healthy_since', 'host_cpu_count',
              'host_memory_total_bytes', 'host_available_bytes',
              'guest_available_bytes', 'guest_disk_free_bytes',
              'host_disk_total_bytes', 'host_disk_free_bytes',
              'worker_memory_bytes', 'worker_disk_used_bytes',
              'host_cpu_percent', 'host_commit_percent')
    for key in fields:
        if not _number(telemetry.get(key)):
            return 'invalid_telemetry:' + key
    sampled = telemetry['sampled_at']
    if sampled > now + 5:
        return 'telemetry_from_future'
    if now - sampled > TELEMETRY_MAX_AGE_SECONDS:
        return 'telemetry_stale'
    if telemetry['healthy_since'] > min(sampled, now):
        return 'invalid_telemetry:healthy_since'
    cpus = telemetry['host_cpu_count']
    if cpus < 1 or cpus % 1:
        return 'invalid_telemetry:host_cpu_count'
    total = telemetry['host_memory_total_bytes']
    if total <= 0 or telemetry['host_available_bytes'] > total:
        return 'invalid_telemetry:host_available_bytes'
    if telemetry['guest_available_bytes'] > total:
        return 'invalid_telemetry:guest_available_bytes'
    disk_total = telemetry['host_disk_total_bytes']
    if disk_total <= 0 or telemetry['host_disk_free_bytes'] > disk_total:
        return 'invalid_telemetry:host_disk_free_bytes'
    if telemetry['host_cpu_percent'] > 100 or telemetry['host_commit_percent'] > 100:
        return 'invalid_telemetry:host_pressure'
    if telemetry['host_cpu_percent'] >= 90:
        return 'staff_cpu_priority'
    if telemetry['host_commit_percent'] >= 85:
        return 'staff_memory_priority'
    if not isinstance(reservations, Iterable) or isinstance(reservations, (str, bytes, Mapping)):
        return 'invalid_reservations'
    for reservation in reservations:
        if not isinstance(reservation, Mapping) or not isinstance(reservation.get('physical_host'), str):
            return 'invalid_reservations'
        if reservation['physical_host'] == 'agc-laptop':
            return 'physical_host_reserved'
    # Current worker resident bytes and prospective peak are deliberately both
    # charged; the 1 GB envelope covers VM overhead outside the Linux cgroup.
    worker_memory = telemetry['worker_memory_bytes']
    if worker_memory + profile.memory_max_bytes + 1_000_000_000 > LAPTOP_RAM_CEILING_BYTES:
        return 'worker_memory_ceiling'
    reserve = max(8 * GiB, total * .25)
    if telemetry['host_available_bytes'] - profile.memory_max_bytes < reserve:
        return 'staff_memory_reserve'
    if telemetry['guest_available_bytes'] - profile.memory_max_bytes < GiB:
        return 'guest_memory_reserve'
    if profile.cpu_cores > min(cpus, 12):
        return 'host_cpu_capacity'
    if telemetry['worker_disk_used_bytes'] + profile.scratch_bytes > 300_000_000_000:
        return 'worker_disk_ceiling'
    if telemetry['host_disk_free_bytes'] - profile.scratch_bytes < disk_total * .15:
        return 'staff_disk_reserve'
    if telemetry['guest_disk_free_bytes'] - profile.scratch_bytes < GUEST_DISK_RESERVE_BYTES:
        return 'guest_disk_reserve'
    if min(sampled, now) - telemetry['healthy_since'] < HEALTHY_WINDOW_SECONDS:
        return 'warming_up'
    return None


def emergency_runtime_hold(telemetry, now):
    """Safety floor while running; readiness/warm-up only govern new starts."""
    if (not isinstance(telemetry, Mapping) or telemetry.get('online') is not True
            or telemetry.get('policy_verified') is not True or telemetry.get('host_gate') is not None
            or not _number(telemetry.get('sampled_at'))
            or not -5 <= now - telemetry['sampled_at'] <= TELEMETRY_MAX_AGE_SECONDS):
        return True
    for key, minimum in (('host_available_bytes', HOST_RESERVE_BYTES),
                         ('guest_available_bytes', GUEST_RESERVE_BYTES),
                         ('guest_disk_free_bytes', GUEST_DISK_RESERVE_BYTES)):
        if not _number(telemetry.get(key)) or telemetry[key] < minimum:
            return True
    for key, ceiling in (('memory_full_avg60', 1), ('io_some_avg60', 10),
                         ('thinpool_data_percent', 80), ('thinpool_metadata_percent', 70)):
        if not _number(telemetry.get(key)) or telemetry[key] >= ceiling:
            return True
    return False
