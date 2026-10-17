#!/usr/bin/env python3
"""Root host sampler. Only numeric, non-secret metrics cross into CT115."""
import json
import math
import os
import pathlib
import subprocess
import tempfile
import time

GUEST_WRITE = '''import json,os,pathlib,sys,tempfile
path=pathlib.Path('/run/agent-console-host-resources.json')
data=json.load(sys.stdin)
fd,temporary=tempfile.mkstemp(prefix='.console-resources-',dir=path.parent)
try:
 with os.fdopen(fd,'w') as handle:
  json.dump(data,handle);handle.flush();os.fsync(handle.fileno());os.fchmod(handle.fileno(),0o644)
 os.replace(temporary,path)
finally:
 if os.path.exists(temporary):os.unlink(temporary)
'''


def atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.console-resources-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle);handle.flush();os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
            owner = path.parent.stat()
            os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def counter(value):
    value = value.strip()
    if not value.isascii() or not value.isdecimal() or int(value) > 2 ** 63 - 1:
        raise ValueError('invalid memory counter')
    return int(value)


def container_memory(cgroup):
    """Estimate reclaimable headroom at each actual finite-limit boundary."""
    try:
        samples = []
        for path in (cgroup, cgroup / 'ns'):
            maximum = (path / 'memory.max').read_text().strip()
            if maximum == 'max':
                continue
            limit = counter(maximum)
            current = counter((path / 'memory.current').read_text())
            stats = dict(line.split() for line in (path / 'memory.stat').read_text().splitlines())
            inactive = counter(stats['inactive_file'])
            if limit == 0 or inactive > current:
                raise ValueError('inconsistent memory counters')
            pressure = next(line for line in (path / 'memory.pressure').read_text().splitlines()
                            if line.startswith('full '))
            psi = float(dict(item.split('=') for item in pressure.split()[1:])['avg10'])
            if not math.isfinite(psi) or not 0 <= psi <= 100:
                raise ValueError('invalid memory pressure')
            # Inactive file cache is reclaimable; anonymous/active/kernel memory
            # remains charged. Never subtract more than the measured usage.
            headroom = max(0, limit - max(0, current - min(inactive, current)))
            samples.append((headroom, limit, current, inactive, psi))
        headroom, limit, current, inactive, _ = min(samples, key=lambda sample: sample[0])
        psi = max(sample[4] for sample in samples)
        return dict(ct115_memory_headroom_bytes=0 if psi > 5 else headroom,
                    ct115_memory_current_bytes=current, ct115_memory_limit_bytes=limit,
                    ct115_memory_inactive_file_bytes=inactive,
                    ct115_memory_raw_headroom_bytes=max(0, limit - current),
                    ct115_full_psi_avg10=psi, ct115_memory_sample_valid=True)
    except (OSError, ValueError, KeyError, StopIteration):
        # Publish an immediate veto rather than leaving an older permissive
        # snapshot usable until its normal freshness deadline.
        return dict(ct115_memory_headroom_bytes=0, ct115_memory_sample_valid=False)


def main():
    pid = int(subprocess.check_output(['lxc-info', '-n', '115', '-pH'], timeout=2).strip())
    root = pathlib.Path(f'/proc/{pid}/root')
    values = {}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1);values[key] = int(value.split()[0]) * 1024
    pressure = next(line for line in pathlib.Path('/proc/pressure/memory').read_text().splitlines() if line.startswith('full '))
    psi = float(dict(item.split('=') for item in pressure.split()[1:])['avg10'])
    cgroup = pathlib.Path('/sys/fs/cgroup/lxc/115')
    disk = os.statvfs(root)
    temporary = os.statvfs('/var/lib/agent-console-tmp')
    data = dict(sampled_at=time.time(), host_available_bytes=values['MemAvailable'],
        host_full_psi_avg10=psi, **container_memory(cgroup),
        ct115_disk_free_bytes=disk.f_bavail*disk.f_frsize,
        tmp_free_bytes=temporary.f_bavail*temporary.f_frsize)
    data['maintenance'] = pathlib.Path('/run/agent-console-maintenance').exists()
    atomic(pathlib.Path('/run/agent-console-host-resources.json'), data)
    # Enter the container's user namespace when creating its tmpfs file.
    # A host-root write through /proc/PID/root cannot map UID0 in this LXC.
    subprocess.run(['lxc-attach', '-n', '115', '--clear-env', '--', '/usr/bin/python3', '-c', GUEST_WRITE],
                   input=json.dumps(data), text=True, check=True, timeout=5)


if __name__ == '__main__':main()
