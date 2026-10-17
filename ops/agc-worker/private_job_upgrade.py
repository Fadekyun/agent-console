"""Embedded operator updater: three fixed files, held/drained supervisor only."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

FILES = {'worker.py': 0o755, 'qualification_gate.py': 0o644, 'release-qualified-hold.sh': 0o755}
ROOT = Path('/usr/local/lib/agc-worker')
STATE = Path('/var/lib/agc-worker')
CONFIG = Path('/etc/agc-worker')
BACKUPS = Path('/var/lib/agc-worker-upgrades')
LOCK = Path('/run/lock/agc-private-job-upgrade.lock')
UNITS = [Path('/etc/systemd/system/agc-worker.service'), Path('/etc/systemd/system/agc-worker-jobs.slice')]
WSL_CONFIG = Path('/etc/wsl.conf')
ADAPTERS = Path('/usr/local/lib/agc-worker-adapters')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def require(condition, reason):
    if not condition: raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(data): return hashlib.sha256(data).hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def protected(path, *, directory=False):
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 0 and not info.st_mode & 0o022, 'unprotected_path:'+str(path))
    return info


def ancestry(path):
    for parent in reversed([path, *path.parents]): protected(parent, directory=True)


def read_file(path):
    info = protected(path)
    require(info.st_nlink == 1 and info.st_size <= 2*1024**2, 'file_not_bounded_regular')
    return path.read_bytes()


def validate_payload(plan):
    require(set(plan) == {'version', 'source_revision', 'files'} and plan['version'] == 1
            and re.fullmatch('[a-f0-9]{40}', plan['source_revision']) and set(plan['files']) == set(FILES), 'invalid_upgrade_plan')
    for name, item in plan['files'].items():
        require(set(item) == {'before_sha256', 'sha256', 'mode', 'data'} and item['mode'] == FILES[name]
                and HEX.fullmatch(item['sha256']) and (item['before_sha256'] is None or HEX.fullmatch(item['before_sha256'])), 'invalid_file_descriptor')
        data = base64.b64decode(item['data'], validate=True)
        require(len(data) <= 2*1024**2 and digest(data) == item['sha256'], 'payload_hash_mismatch')
    require(plan['files']['worker.py']['before_sha256'] is not None, 'old_worker_digest_required')


def check_before(root, files, reader=read_file):
    for name, item in files.items():
        path = root/name
        if item['before_sha256'] is None:
            require(not os.path.lexists(path), 'unexpected_existing_file:'+name)
        else:
            require(digest(reader(path)) == item['before_sha256'], 'old_file_changed:'+name)


def check_after(root, files, reader=read_file):
    for name, item in files.items():
        require(digest(reader(root/name)) == item['sha256']
                and stat.S_IMODE((root/name).lstat().st_mode) == item['mode'], 'new_file_mismatch:'+name)


def atomic_bytes(path, data, mode):
    temporary = path.with_name(path.name+'.private-upgrade-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fchmod(stream.fileno(), mode); os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20)


def supervisor_stopped():
    fields = dict(line.split('=',1) for line in command(['systemctl','show','agc-worker.service',
        '--property=ActiveState,MainPID,ControlGroup']).stdout.splitlines() if '=' in line)
    if fields.get('ActiveState') not in {'inactive','failed'} or fields.get('MainPID') != '0': return False
    group = fields.get('ControlGroup')
    events = Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events' if group else None
    return events is None or not events.exists() or 'populated 0' in events.read_text()


def status():
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5); client.connect('/run/agc-worker/rpc.sock')
        client.sendall(b'{"protocol_version":1,"action":"status"}\n')
        with client.makefile('rb') as stream: value = json.loads(stream.readline(524288))
    require(value.get('ok') is True and value.get('active') is None
            and 'operator_hold' in value.get('admission', {}).get('holds', []), 'worker_not_held_and_drained')
    return value


def jobs_empty():
    rows = command(['systemctl', 'list-units', '--all', '--type=service', '--no-legend', '--plain', 'agc-job-*.service']).stdout.splitlines()
    for row in rows:
        unit = row.split()[0]
        require(re.fullmatch(r'agc-job-[a-f0-9]{32}\.service', unit), 'unexpected_job_unit')
        fields = dict(line.split('=',1) for line in command(['systemctl','show',unit,
            '--property=ActiveState,SubState,ControlGroup']).stdout.splitlines() if '=' in line)
        require(fields.get('ActiveState') in {'inactive','failed'} or
                (fields.get('ActiveState'), fields.get('SubState')) == ('active','exited'), 'job_unit_still_active')
        group = fields.get('ControlGroup')
        if group:
            events = Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events'
            require(not events.exists() or 'populated 0' in events.read_text(), 'job_cgroup_not_empty')


def preserved_snapshot():
    # Metadata only for credentials/config: no credential bytes enter the bundle,
    # backups or log. The updater never writes any of these paths.
    result = {}
    paths = [CONFIG, *sorted(CONFIG.rglob('*')), WSL_CONFIG, *UNITS]
    for path in paths:
        info = path.lstat()
        result[str(path)] = [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                             info.st_size, info.st_mtime_ns]
        if path == CONFIG/'hold':
            # Setup touches the existing hold on every invocation. Its time is
            # not config drift; type/ownership/mode and existence remain bound.
            result[str(path)] = [info.st_mode, info.st_uid, info.st_gid]
    for path in UNITS+[WSL_CONFIG]: result[str(path)].append(digest(read_file(path)))
    installed = ADAPTERS/'installed.json'
    raw = read_file(installed)
    manifest = json.loads(raw)
    require(isinstance(manifest.get('files'),dict) and 0 < len(manifest['files']) <= 4096, 'adapter_manifest_missing')
    result[str(installed)] = digest(raw)
    for name in manifest['files']:
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative)==name, 'invalid_adapter_path')
        path = ADAPTERS/relative
        ancestry(path.parent)
        result[str(path)] = digest(read_file(path))
    return result


def main(plan):
    require(os.geteuid() == 0 and os.environ.get('WSL_DISTRO_NAME') == 'AGC-Worker', 'worker_root_context_required')
    os.umask(0o077)
    validate_payload(plan)
    with open(LOCK,'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        upgrade(plan)


def upgrade(plan):
    for path in (ROOT, STATE, STATE/'jobs', CONFIG): ancestry(path)
    protected(CONFIG/'hold')
    status(); jobs_empty()
    identity = digest(canonical(plan))
    ancestry(BACKUPS.parent)
    BACKUPS.mkdir(mode=0o700, exist_ok=True); protected(BACKUPS, directory=True)
    sync_directory(BACKUPS.parent)
    backup = BACKUPS/identity
    journal = backup/'receipt.json'
    if backup.exists():
        ancestry(backup)
        receipt = json.loads(read_file(journal))
        require(receipt.get('plan_sha256') == identity and receipt.get('state') == 'completed', 'incomplete_upgrade_requires_reconciliation')
        check_after(ROOT, plan['files'])
        require(preserved_snapshot() == receipt['preserved'], 'preserved_config_changed')
        require(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in (STATE, STATE/'jobs')), 'private_state_modes_changed')
        print(json.dumps({'state':'already_installed_held','receipt':str(journal),'worker_sha256':plan['files']['worker.py']['sha256']}))
        return
    check_before(ROOT, plan['files'])
    saved = preserved_snapshot()
    backup.mkdir(mode=0o700)
    sync_directory(BACKUPS)
    for name, item in plan['files'].items():
        if item['before_sha256'] is not None: atomic_bytes(backup/name, read_file(ROOT/name), 0o600)
    receipt = {'version':1, 'plan_sha256':identity, 'source_revision':plan['source_revision'],
               'state':'prepared', 'preserved':saved, 'files':{k:{n:v for n,v in d.items() if n!='data'} for k,d in plan['files'].items()},
               'before_metadata':{name:{'uid':(ROOT/name).stat().st_uid,'gid':(ROOT/name).stat().st_gid,
                   'mode':stat.S_IMODE((ROOT/name).stat().st_mode)} if item['before_sha256'] is not None else None
                   for name,item in plan['files'].items()}}
    atomic_bytes(journal, canonical(receipt), 0o600)
    installing = False
    try:
        status(); jobs_empty(); check_before(ROOT, plan['files'])
        protected(CONFIG/'hold')
        receipt['state'] = 'installing'; atomic_bytes(journal, canonical(receipt), 0o600)
        installing = True
        command(['systemctl','stop','agc-worker.service'])
        require(supervisor_stopped(), 'supervisor_stop_unconfirmed')
        jobs_empty(); protected(CONFIG/'hold')
        for name, item in plan['files'].items(): atomic_bytes(ROOT/name, base64.b64decode(item['data'],validate=True), item['mode'])
        for path in (STATE, STATE/'jobs'): protected(path,directory=True); path.chmod(0o700)
        check_after(ROOT, plan['files'])
        require(preserved_snapshot() == saved, 'preserved_config_changed')
        command(['systemctl','start','agc-worker.service'])
        deadline = time.monotonic()+15
        while True:
            try: status(); break
            except (OSError, ValueError):
                if time.monotonic() >= deadline: raise
                time.sleep(.25)
        jobs_empty()
        require(preserved_snapshot() == saved, 'preserved_config_changed')
        require(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in (STATE, STATE/'jobs')), 'private_state_modes_changed')
        receipt.update(state='completed', completed_at=time.time(), held=True, qualification='required_fresh')
        atomic_bytes(journal, canonical(receipt), 0o600)
        print(json.dumps({'state':'installed_held','receipt':str(journal),'worker_sha256':plan['files']['worker.py']['sha256'],
                          'configuration_credentials_adapters_units':'preserved','qualification':'required_fresh'}))
    except BaseException as error:
        # Never automatically restore the old direct-host-path launcher. Any
        # partial install stays held and the supervisor is stopped for review.
        stopped = False
        if installing:
            try:
                stop = subprocess.run(['systemctl','stop','agc-worker.service'],check=False,capture_output=True,timeout=20)
                receipt['cleanup_stop_exit_code'] = stop.returncode
                stopped = supervisor_stopped()
            except BaseException as cleanup_error:
                receipt['cleanup_error_type'] = type(cleanup_error).__name__
        receipt.update(state=('failed_held' if stopped else 'failed_stop_unconfirmed') if installing else 'failed_before_install',
                       supervisor_stop_confirmed=stopped, failure_type=type(error).__name__, failed_at=time.time())
        try: atomic_bytes(journal, canonical(receipt), 0o600)
        except BaseException as journal_error:
            print(json.dumps({'state':'upgrade_failure_journal_unconfirmed','failure_type':type(error).__name__,
                              'journal_error_type':type(journal_error).__name__,'receipt':str(journal)}))
        raise
