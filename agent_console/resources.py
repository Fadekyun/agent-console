"""Host-supplied, credential-free launch admission shared by CLI and HTTP."""
from __future__ import annotations

import json
import math
import os
import stat
import time
from pathlib import Path

GIB = 1024 ** 3


class ResourceUnavailable(RuntimeError):
    def __init__(self, reasons):
        self.reasons = reasons
        super().__init__('Launch temporarily unavailable: ' + ', '.join(reasons))


def resource_status():
    configured = os.getenv('AGENT_CONSOLE_RESOURCE_SNAPSHOT')
    if not configured:
        return {'configured': False, 'launch_allowed': True, 'reasons': []}
    reasons = []
    metrics = {}
    try:
        fd = os.open(configured, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_size > 8192:
                raise ValueError('untrusted snapshot')
            data = json.load(handle)
        keys = ('sampled_at', 'host_available_bytes', 'host_full_psi_avg10',
                'ct115_memory_headroom_bytes', 'ct115_disk_free_bytes', 'tmp_free_bytes')
        for key in keys:
            value = data[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('invalid snapshot')
            metrics[key] = value
        age = time.time() - metrics['sampled_at']
        if data.get('maintenance') is True:
            reasons.append('maintenance_window')
        if age < -5 or age > 60:
            reasons.append('snapshot_stale')
        for key, threshold, reason in (
            ('host_available_bytes', 2 * GIB, 'host_memory_low'),
            ('ct115_memory_headroom_bytes', GIB // 2, 'container_memory_low'),
            ('ct115_disk_free_bytes', 4 * GIB, 'container_disk_low'),
            ('tmp_free_bytes', 10 * GIB, 'temporary_disk_low'),
        ):
            if metrics[key] < threshold:
                reasons.append(reason)
        if metrics['host_full_psi_avg10'] > 5:
            reasons.append('host_memory_pressure')
    except (OSError, ValueError, KeyError, TypeError):
        reasons = ['snapshot_unavailable']
        metrics = {}
    return {'configured': True, 'launch_allowed': not reasons, 'reasons': reasons,
            'metrics': metrics, 'retry_after': 30}


def require_launch_resources():
    status = resource_status()
    if not status['launch_allowed']:
        raise ResourceUnavailable(status['reasons'])
