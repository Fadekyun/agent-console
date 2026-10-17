#!/bin/bash
# Operator qualification only. Run via Setup-Worker.ps1 after serialized setup.
# No production policy marker, registry, source checkout or queue state changes.
set -euo pipefail
umask 0077
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
exec /usr/bin/python3 - <<'AGC_NATIVE_QUALIFICATION'
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import time
import traceback
import uuid

os.umask(0o077)  # Match the production daemon, including directory creation.
run_id = uuid.uuid4().hex
base = Path('/var/lib/agc-worker-qualification-' + run_id)
results = Path('/var/lib/agc-worker-qualification-results')
source = Path('/usr/local/lib/agc-worker/worker.py')
qual_slice = 'agcqualification' + run_id + '.slice'
units, auxiliary_units, launch_count = set(), set(), {}
evidence = {'version': 1, 'run_id': run_id, 'state': 'running', 'cases': [],
            'worker_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'scope': 'isolated real systemd/cgroup behavior; not end-to-end queue or admission proof',
            'admission_exceptions': ['policy_unverified', 'isolated instance warmup'],
            'started_at': time.time(), 'work_directory': str(base), 'slice': qual_slice}
lock = open('/run/lock/agc-native-qualification.lock', 'w')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
base.mkdir(mode=0o700)
results.mkdir(mode=0o700, exist_ok=True)
spec = importlib.util.spec_from_file_location('qualified_worker', source)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

def command(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=25,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if check and result.returncode:
        raise RuntimeError('native control failed: ' + args[0] + ' ' + result.stderr[-2000:])
    return result

def production_drained():
    assert Path('/etc/agc-worker/hold').is_file(), 'production worker must stay held'
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect('/run/agc-worker/rpc.sock')
        client.sendall(b'{"protocol_version":1,"action":"status"}\n')
        with client.makefile('rb') as stream:
            status = json.loads(stream.readline(524288))
    assert status.get('ok') is True and status.get('active') is None, 'production job is active'

original_admission = worker.admission_holds
def qualification_admission(host, now=None, required_memory=0):
    # The real signed-off marker intentionally does not exist yet. All actual
    # AC, run-day, disk, memory, CPU and fresh-host-sample checks remain active.
    return [hold for hold in original_admission(host, now, required_memory)
            if hold != 'policy_unverified']
worker.admission_holds = qualification_admission

def scoped_run(args, check=True):
    args = list(args)
    if args[0] == 'systemd-run':
        unit = next(value.split('=', 1)[1] for value in args if value.startswith('--unit='))
        if unit.startswith('agc-lease-'):
            auxiliary_units.update({unit + '.timer', unit + '.service'})
            args = [value.replace('--property=Slice=system.slice', '--property=Slice=' + qual_slice) for value in args]
        else:
            assert unit.startswith('agc-job-') and unit.endswith('.service')
            units.add(unit)
            launch_count[unit] = launch_count.get(unit, 0) + 1
    # Redirect only this isolated module's slice operations; never change the
    # production jobs slice. Every child is <=128MiB and the test slice <=256MiB.
    args = [value.replace('agc-worker-jobs.slice', qual_slice) for value in args]
    if args[:3] == ['systemctl', 'set-property', '--runtime']:
        assert args[3] == qual_slice
        args = [value.replace('CPUQuota=1200%', 'CPUQuota=100%') for value in args]
    return command(args, check=check)
worker.run = scoped_run

adapter = base / 'harmless-adapter.py'
adapter.write_text(r'''
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('mode','manifest','workspace','output'): p.add_argument('--'+name,required=True)
a=p.parse_args(); output=Path(a.output); workspace=Path(a.workspace)
def save(name,value):
    with (output/name).open('w') as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
def process(pid):
    text=Path('/proc/'+str(pid)+'/stat').read_text()
    return {'pid':pid,'start_ticks':text[text.rfind(')')+2:].split()[19]}
def checkpoint(*unused): save('checkpoint.json',{'checkpoint':True,'pid':os.getpid()})
signal.signal(signal.SIGUSR1,checkpoint)
import stat
assert Path.cwd()==workspace==Path('/run/agc-job/workspace')
assert output==Path('/run/agc-job/output') and Path(a.manifest)==Path('/run/agc-job/manifest.json')
root=workspace.parent; metadata=root.stat()
assert metadata.st_uid==0 and metadata.st_gid==os.getgid() and stat.S_IMODE(metadata.st_mode)==0o710
manifest=json.loads(Path(a.manifest).read_text());lease=Path(manifest['worker_lease_file'])
assert lease==Path('/run/agc-job/lease.json')
before=json.loads(lease.read_text())['last_heartbeat_monotonic']
for path in (Path(a.manifest),lease):
 assert stat.S_IMODE(path.stat().st_mode)==0o640 and path.stat().st_uid==0
 try:
  with path.open('a'): pass
 except PermissionError:pass
 except OSError as error:
  assert error.errno==30
 else:raise AssertionError('job can write supervisor metadata')
private=json.loads((workspace/'source/qualification.json').read_text())
for name in ('fences.json','receipts/canary','jobs/sibling/canary'):
 try:(Path(private['host_state'])/name).read_bytes()
 except (PermissionError,FileNotFoundError):pass
 else:raise AssertionError('job read private host/sibling state')
for name in ('HOME','TMPDIR','XDG_CACHE_HOME','npm_config_cache'):
 assert os.environ[name].startswith('/run/agc-job/workspace/')
save('namespace.json',{'cwd':str(Path.cwd()),'uid':os.getuid(),'gid':os.getgid(),
 'job_root_mode':'0710','metadata_mode':'0640','host_state_inaccessible':True,
 'private_mount':any(' /run/agc-job ' in line for line in Path('/proc/self/mountinfo').read_text().splitlines())})
processes=[process(os.getpid())]
if a.mode in ('timeout','lease','timer'):
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(300)'])
    processes.append(process(child.pid))
save('processes.json',processes)
while not (workspace/'release').exists():
 if a.mode=='success' and json.loads(lease.read_text())['last_heartbeat_monotonic']>before:
  save('lease-replaced.json',{'replacement_visible':True})
 time.sleep(.05)
if a.mode=='success':
    save('success.json',{'ok':True,'executions':1})
    (output/'empty-file').touch()
elif a.mode=='oom':
    allocated=[]
    for i in range(256):
        allocated.append(bytearray(1024*1024));time.sleep(.01)
    raise RuntimeError('64MiB OOM cap was not enforced')
else:
    while True: time.sleep(.2)
''')
adapter.chmod(0o644)
config = base / 'config'
config.mkdir(mode=0o700)
registry = {}
for mode in ('success', 'oom', 'timeout', 'lease', 'timer'):
    registry['qual.' + mode] = {'enabled': True, 'argv': ['/usr/bin/python3', '/run/agc-job/workspace/source/harmless-adapter.py', '--mode', mode],
        'parameter_keys': [], 'production_writes': mode in ('lease', 'timer'), 'supports_checkpoint': mode == 'lease',
        'memory_bytes': 128 * 1024**2, 'cpus': 1, 'timeout_seconds': 150 if mode in ('lease', 'timer') else (8 if mode == 'timeout' else 30)}
(config / 'adapters.json').write_text(json.dumps(registry))
instance = None

def current_process(entry):
    try:
        text = Path('/proc/' + str(entry['pid']) + '/stat').read_text()
        fields = text[text.rfind(')') + 2:].split()
        return fields[19] == entry['start_ticks'] and fields[0] != 'Z'
    except FileNotFoundError:
        return False

def wait_until(predicate, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.1)
    raise AssertionError('timed out: ' + label)

def real_properties(unit):
    output = command(['systemctl', 'show', unit,
        '--property=Slice,MemoryMax,MemorySwapMax,CPUQuotaPerSecUSec,KillMode,RemainAfterExit,ControlGroup,ActiveState,SubState,Result,ExecMainStatus']).stdout
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)

try:
    production_drained()
    instance = worker.Worker(base / 'state', config)
    (instance.state / 'receipts/canary').write_text('private qualification receipt')
    (instance.state / 'jobs/sibling').mkdir(mode=0o700)
    (instance.state / 'jobs/sibling/canary').write_text('private sibling')
    instance.started = time.monotonic() - 121
    instance.healthy_since = time.monotonic() - 181
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode='w') as archive:
        for name,content in [('harmless-adapter.py',adapter.read_bytes()),
                             ('qualification.json',json.dumps({'host_state':str(instance.state)}).encode())]:
            item = tarfile.TarInfo(name); item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    archive_bytes = archive_buffer.getvalue()
    source_digest = hashlib.sha256(archive_bytes).hexdigest()
    worker.install_object(instance.state / 'objects', source_digest, len(archive_bytes), io.BytesIO(archive_bytes))
    for mode, expected in [('success', 'succeeded'), ('oom', 'oom'), ('timeout', 'timeout'), ('lease', 'unknown'), ('timer', 'unknown')]:
        production_drained()
        instance.host = worker.read_json('/run/agc-worker/host.json')
        holds = qualification_admission(instance.host, required_memory=128 * 1024**2)
        assert not holds, 'actual host admission holds: ' + ','.join(holds)
        manifest = {'version': 1, 'kind': 'qual.' + mode, 'source_sha': '0' * 40,
                    'input_sha256': hashlib.sha256(b'').hexdigest(), 'parameters': {},
                    'source_artifact': {'sha256': source_digest, 'size': len(archive_bytes)},
                    'production_writes': mode in ('lease', 'timer')}
        request = {'protocol_version': 1, 'action': 'start', 'job_id': 'qual-' + run_id + '-' + mode,
                   'attempt_id': 'attempt-1', 'lease_generation': 1, 'kind': manifest['kind'],
                   'source_sha': manifest['source_sha'], 'input_sha256': manifest['input_sha256'],
                   'manifest_sha256': hashlib.sha256(worker.canonical(manifest)).hexdigest(), 'manifest': manifest}
        binding = worker.validate_binding(request)
        started = instance.dispatch(request)
        assert started['state'] == 'running', started
        unit = started['unit']
        command(['systemctl', 'set-property', '--runtime', qual_slice, 'MemoryMax=256M', 'MemorySwapMax=0', 'CPUQuota=100%'])
        job = instance.state / 'jobs' / instance.key(binding)
        wait_until(lambda: (job / 'output/processes.json').exists(), 5, 'adapter ready')
        processes = worker.read_json(job / 'output/processes.json')
        namespace = worker.read_json(job / 'output/namespace.json')
        assert namespace['private_mount'] and namespace['host_state_inaccessible'], namespace
        assert (instance.state.stat().st_mode & 0o777)==0o700
        assert ((instance.state/'jobs').stat().st_mode & 0o777)==0o700
        # Bind mounts must remain inside the service, not expose the same files
        # through /run in the host namespace running this qualification driver.
        assert not (worker.JOB_VIEW/'manifest.json').exists(), 'job mount escaped service namespace'
        outside=command(['/usr/sbin/runuser','-u',worker.JOB_USER,'--','/usr/bin/python3','-c',
            'import pathlib,sys\np=pathlib.Path(sys.argv[1])\ntry:p.read_bytes()\nexcept PermissionError:pass\nelse:raise SystemExit(1)',
            str(job/'manifest.json')],check=False)
        assert outside.returncode==0, 'unprivileged host process can traverse private worker state'
        if mode=='success':
            instance.dispatch({**binding,'protocol_version':1,'action':'heartbeat'})
            wait_until(lambda:(job/'output/lease-replaced.json').exists(),5,'atomic lease replacement visible')
        if mode == 'oom':
            command(['systemctl', 'set-property', '--runtime', unit, 'MemoryHigh=60M', 'MemoryMax=64M'])
        properties = real_properties(unit)
        assert int(properties['MemoryMax']) == (64 if mode == 'oom' else 128) * 1024**2, properties
        assert properties['MemorySwapMax'] == '0' and properties['CPUQuotaPerSecUSec'] == '1s', properties
        assert properties['Slice'] == qual_slice and properties['RemainAfterExit'] == 'yes', properties
        assert properties['KillMode'] == 'control-group', properties
        cgroup = Path('/sys/fs/cgroup') / properties['ControlGroup'].lstrip('/')
        assert (cgroup / 'memory.oom.group').read_text().strip() == '1', 'OOMPolicy=kill must enable whole-cgroup OOM'
        case = {'mode': mode, 'expected_state': expected, 'unit': unit, 'properties': properties, 'memory_oom_group': 1,
                'processes': processes, 'namespace': namespace, 'production_umask':'0077',
                'began_monotonic': time.monotonic()}
        evidence['cases'].append(case)
        (job / 'workspace/release').touch()
        if mode == 'timer':
            # Deliberately do not call the Python watchdog or heartbeat for
            # this one case. The independent root-owned timer must kill every
            # process even while the supervisor makes no progress.
            time.sleep(84)
            case['timer_stopped_without_watchdog'] = instance.unit_empty(unit)
            case['timer_unit_result'] = real_properties(started['lease_timer'].replace('.timer', '.service'))
            assert case['timer_stopped_without_watchdog'], 'independent lease timer left a live cgroup'
            assert not any(current_process(entry) for entry in processes), 'lease timer left a child alive'
        deadline = time.monotonic() + (130 if mode == 'lease' else 45)
        while time.monotonic() < deadline:
            instance.tick()
            receipt = instance.bound_receipt(binding)
            if receipt['state'] in worker.TERMINAL: break
            time.sleep(1)
        case['elapsed_seconds'] = time.monotonic() - case.pop('began_monotonic')
        case['receipt'] = receipt
        assert receipt['state'] == expected and receipt.get('termination_confirmed') is True, receipt
        assert not any(current_process(entry) for entry in processes), 'child/main process remains alive'
        assert instance.unit_empty(unit), 'test cgroup remains populated'
        if mode == 'lease':
            assert receipt['reason'] == 'lease_lost' and receipt.get('checkpoint_requested'), receipt
            assert receipt.get('checkpoint_sha256'), 'checkpoint artifact not captured before lease stop'
            assert 79 <= case['elapsed_seconds'] <= 90, 'real lease-loss stop exceeded its 90-second total budget'
        if mode == 'timer':
            assert 83 <= case['elapsed_seconds'] <= 90, 'independent lease timer exceeded its stop budget'
        inventory = receipt['result']['inventory_artifact']
        inventory_path = instance.state / 'objects' / inventory['sha256']
        assert worker.digest_file(inventory_path) == inventory['sha256']
        for artifact in worker.read_json(inventory_path)['files']:
            obj = instance.state / 'objects' / artifact['sha256']
            assert obj.stat().st_size == artifact['size'] and worker.digest_file(obj) == artifact['sha256']
        before = dict(launch_count)
        duplicate = instance.dispatch(request)
        assert duplicate['state'] == expected and launch_count == before, 'duplicate executed again'
        changed = {**request, 'attempt_id': 'attempt-2'}
        try:
            instance.dispatch(changed)
            raise AssertionError('old generation replay was accepted')
        except ValueError as error:
            assert 'generation_fenced' in str(error)
        case['duplicate_did_not_execute'] = True
        case['process_group_empty'] = True
        print(json.dumps({'native_case': mode, 'state': expected, 'elapsed_seconds': round(case['elapsed_seconds'], 2)}), flush=True)
    evidence['state'] = 'passed'
except BaseException as error:
    evidence.update(state='failed', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc(limit=8))
finally:
    cleanup = []
    for auxiliary in sorted(auxiliary_units, reverse=True):
        try:
            command(['systemctl', 'stop', auxiliary], check=False)
            command(['systemctl', 'reset-failed', auxiliary], check=False)
        except BaseException as error:
            cleanup.append({'auxiliary_unit': auxiliary, 'error_type': type(error).__name__})
            evidence['state'] = 'failed'
    for unit in sorted(units):
        try:
            command(['systemctl', 'stop', unit], check=False)
            empty = instance.unit_empty(unit) if instance else False
            cleanup.append({'unit': unit, 'empty': empty})
            if not empty: evidence['state'] = 'failed'
            command(['systemctl', 'reset-failed', unit], check=False)
        except BaseException as error:
            cleanup.append({'unit': unit, 'error_type': type(error).__name__})
            evidence['state'] = 'failed'
    try:
        command(['systemctl', 'stop', qual_slice], check=False)
    except BaseException as error:
        cleanup.append({'slice': qual_slice, 'error_type': type(error).__name__})
        evidence['state'] = 'failed'
    evidence.update(cleanup=cleanup, finished_at=time.time())
    output = results / (run_id + '.json')
    worker.atomic_json(output, evidence)
    if evidence['state'] == 'passed': shutil.rmtree(base)
    print(json.dumps({'native_qualification': evidence['state'], 'evidence': str(output),
                      'failure_workspace_preserved': evidence['state'] != 'passed',
                      'worker_source_sha256': evidence['worker_source_sha256']}), flush=True)
raise SystemExit(0 if evidence['state'] == 'passed' else 1)
AGC_NATIVE_QUALIFICATION
