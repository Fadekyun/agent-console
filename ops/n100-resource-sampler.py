#!/usr/bin/env python3
"""Root host sampler. Only numeric, non-secret metrics cross into CT115."""
import json
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


def main():
    pid = int(subprocess.check_output(['lxc-info', '-n', '115', '-pH'], timeout=2).strip())
    root = pathlib.Path(f'/proc/{pid}/root')
    values = {}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1);values[key] = int(value.split()[0]) * 1024
    pressure = next(line for line in pathlib.Path('/proc/pressure/memory').read_text().splitlines() if line.startswith('full '))
    psi = float(dict(item.split('=') for item in pressure.split()[1:])['avg10'])
    cgroup = pathlib.Path('/sys/fs/cgroup/lxc/115')
    current = int((cgroup / 'memory.current').read_text())
    limits = [(p / 'memory.max').read_text().strip() for p in (cgroup, cgroup / 'ns')]
    limit = min(int(value) for value in limits if value != 'max')
    disk = os.statvfs(root)
    temporary = os.statvfs('/var/lib/agent-console-tmp')
    data = dict(sampled_at=time.time(), host_available_bytes=values['MemAvailable'],
        host_full_psi_avg10=psi, ct115_memory_headroom_bytes=max(0,limit-current),
        ct115_disk_free_bytes=disk.f_bavail*disk.f_frsize,
        tmp_free_bytes=temporary.f_bavail*temporary.f_frsize)
    data['maintenance'] = pathlib.Path('/run/agent-console-maintenance').exists()
    atomic(pathlib.Path('/run/agent-console-host-resources.json'), data)
    # Enter the container's user namespace when creating its tmpfs file.
    # A host-root write through /proc/PID/root cannot map UID0 in this LXC.
    subprocess.run(['lxc-attach', '-n', '115', '--clear-env', '--', '/usr/bin/python3', '-c', GUEST_WRITE],
                   input=json.dumps(data), text=True, check=True, timeout=5)


if __name__ == '__main__':main()
