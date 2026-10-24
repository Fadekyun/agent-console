"""Fixed N100 read-only proof of the effective CT102 memory boundary."""

BOUNDARY_SCRIPT = r'''
def ct102_boundary(read, command, configured_limit):
    import time
    pid_text = command(['/usr/bin/sudo', '-n', '/usr/bin/lxc-info', '-n', '102', '-pH']).strip()
    if not pid_text.isdecimal() or int(pid_text) <= 1: raise ValueError('invalid CT102 init PID')
    pid = int(pid_text)
    membership = read('/proc/' + str(pid) + '/cgroup').strip().splitlines()
    if len(membership) != 1 or not membership[0].startswith('0::/lxc/102/ns/'):
        raise ValueError('CT102 init outside enrolled namespace')
    mounts = [line.split() for line in read('/proc/' + str(pid) + '/mountinfo').splitlines()]
    cgroups = [parts for parts in mounts if ' - cgroup2 ' in ' '.join(parts) and parts[4] == '/sys/fs/cgroup']
    if len(cgroups) != 1 or cgroups[0][3] != '/lxc/102/ns':
        raise ValueError('CT102 namespace mount proof mismatch')
    root = '/sys/fs/cgroup/lxc/102'
    maximum = read(root + '/memory.max').strip()
    if not maximum.isdecimal(): raise ValueError('outer CT102 limit is unbounded')
    limit = int(maximum)
    if limit != configured_limit or not 0 < limit <= 2 * 1024**3:
        raise ValueError('actual CT102 limit differs from configured supported cap')
    for ancestor in ('/sys/fs/cgroup/lxc', '/sys/fs/cgroup'):
        try: parent = read(ancestor + '/memory.max').strip()
        except FileNotFoundError:
            if ancestor == '/sys/fs/cgroup': continue  # cgroup2 root cannot be limited
            raise
        if parent != 'max' and (not parent.isdecimal() or int(parent) < limit):
            raise ValueError('unexpected tighter ancestor memory boundary')
    current = read(root + '/memory.current').strip()
    if not current.isdecimal(): raise ValueError('invalid CT102 memory usage')
    # Reject concurrent restarts, remaps or configuration changes across reads.
    if (command(['/usr/bin/sudo', '-n', '/usr/bin/lxc-info', '-n', '102', '-pH']).strip() != pid_text
            or read(root + '/memory.max').strip() != maximum
            or read('/proc/' + str(pid) + '/cgroup').strip().splitlines() != membership):
        raise ValueError('CT102 boundary changed during probe')
    return {'guest_limit_bytes': limit, 'guest_current_bytes': int(current),
            'guest_cgroup_path': '/lxc/102', 'guest_namespace_root': '/lxc/102/ns',
            'boundary_observed_at': time.time()}
boundary = ct102_boundary(read, command, int(config['memory']) * 1024 * 1024)
'''
