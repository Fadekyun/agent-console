"""Pure enrollment validation; hidden parent limits require trusted host proof."""
import math
import time

FIELDS = {'version', 'physical_host', 'guest_id', 'cgroup_path', 'namespace_root',
          'memory_limit_bytes', 'observed_at'}


def validate_enrollment(value, *, fresh=False, now=None):
    if (not isinstance(value, dict) or set(value) != FIELDS or type(value['version']) is not int
            or value['version'] != 1 or value['physical_host'] != 'n100'
            or value['guest_id'] != 'lxc-102' or value['cgroup_path'] != '/lxc/102'
            or value['namespace_root'] != '/lxc/102/ns'
            or type(value['memory_limit_bytes']) is not int
            or not 0 < value['memory_limit_bytes'] <= 2*1024**3
            or isinstance(value['observed_at'], bool)
            or not isinstance(value['observed_at'], (int, float))
            or not math.isfinite(value['observed_at']) or value['observed_at'] <= 0):
        raise ValueError('invalid_host_boundary_enrollment')
    if fresh and not -5 <= (time.time() if now is None else now)-value['observed_at'] <= 300:
        raise ValueError('stale_host_boundary_enrollment')
    return value


def memory_headroom(sample, enrollment, visible_limit, visible_current):
    enrolled = validate_enrollment(enrollment)
    if (sample['ct102_cgroup_path'] != enrolled['cgroup_path']
            or sample['ct102_namespace_root'] != enrolled['namespace_root']
            or sample['ct102_memory_limit_bytes'] != enrolled['memory_limit_bytes']):
        raise ValueError('host_boundary_changed_requires_enrollment')
    if type(visible_current) is not int or visible_current < 0:
        raise ValueError('invalid_namespace_usage')
    outer = enrolled['memory_limit_bytes']
    if visible_limit != 'max' and (not visible_limit.isdecimal() or int(visible_limit) <= 0):
        raise ValueError('invalid_visible_memory_limit')
    local = outer if visible_limit == 'max' else min(outer, int(visible_limit))
    available = min(max(0, outer-sample['ct102_memory_current_bytes']), max(0, local-visible_current))
    return {'guest_memory_limit_bytes':outer, 'guest_effective_memory_limit_bytes':local,
            'guest_available_bytes':available}
