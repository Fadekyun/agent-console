#!/usr/bin/python3
"""Held CT102 launcher qualification; no queue, credential or portal access.

The CT115 operator must keep authenticated host samples fresh throughout this
run. Only the isolated fixture skips disabled policy and startup warm-up; actual
outer-cgroup, storage and N100 pressure admission checks remain active.
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import traceback
import uuid

assert os.geteuid() == 0 and os.uname().nodename == 'bushi-automation'
os.umask(0o077)
sys.path.insert(0, '/usr/local/lib/bushi-emergency')
import emergency_worker as emergency
worker = emergency.worker
run_id = uuid.uuid4().hex
base = Path('/var/lib/bushi-emergency/qualification-' + run_id)
scratch = Path('/var/lib/bushi-emergency/jobs/qualification-' + run_id)
results = Path('/var/lib/bushi-emergency/qualification-results')
units, timers, launched = set(), set(), []
instance = None
evidence = {'version': 1, 'state': 'running', 'run_id': run_id, 'cases': [],
    'source_sha256': worker.digest_file('/usr/local/lib/bushi-emergency/worker.py'),
    'scope': 'isolated shared production launcher; not queue or organizer acceptance',
    'production_umask': '0077', 'credential_loaded': False, 'remote_requests': 0,
    'admission_exceptions': ['isolated disabled-policy marker', 'isolated startup warm-up']}
lock = open('/run/lock/bushi-emergency-native-qualification.lock', 'w')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def held():
    root = Path('/etc/bushi-emergency')
    assert (root/'hold').is_file()
    assert json.loads((root/'policy.json').read_text())['enabled'] is False
    assert json.loads((root/'adapters.json').read_text())['bushi.emergency']['enabled'] is False
    for path in (root, root/'credentials', root/'credentials/agc-bushi-organizer-state.json'):
        metadata = path.lstat()
        assert metadata.st_uid == 0 and not metadata.st_mode & 0o077
        assert not stat.S_ISLNK(metadata.st_mode)
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5); client.connect('/run/bushi-emergency/rpc.sock')
        client.sendall(b'{"protocol_version":1,"action":"status"}\n')
        with client.makefile('rb') as stream:
            value = json.loads(stream.readline(524288))
    assert value['ok'] is True and value['active'] is None and value['capabilities'] == []
    assert stat.S_IMODE(Path('/var/lib/bushi-emergency').stat().st_mode) == 0o700


def wait_for(predicate, seconds, label):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.1)
    raise AssertionError('Timed out: '+label)


native_run = worker.run
def isolated_run(args, check=True, timeout=None):
    args = list(args)
    if args[0] == 'systemd-run':
        unit = next(v.split('=', 1)[1] for v in args if v.startswith('--unit='))
        if unit.startswith('agc-lease-'):
            timers.update((unit+'.timer', unit+'.service'))
        else:
            assert unit.startswith('agc-job-') and unit.endswith('.service')
            units.add(unit); launched.append(unit)
            assert not any(v.startswith('--property=LoadCredential=') for v in args)
            args.insert(args.index('--'), '--property=PrivateNetwork=yes')
    return native_run(args, check, timeout=timeout)
worker.run = isolated_run
original_admission = worker.admission_holds
worker.admission_holds = lambda host, now=None, required_memory=0: [
    h for h in original_admission(host, now, required_memory) if h != 'policy_unverified']

PROBE = r'''
import argparse,json,os,signal,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser()
for k in ('mode','manifest','workspace','output'):p.add_argument('--'+k,required=True)
a=p.parse_args();out=Path(a.output);work=Path(a.workspace)
def save(name,value):
 temporary=out/(name+'.tmp')
 with temporary.open('w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
 os.replace(temporary,out/name)
def identity(pid):
 text=Path('/proc/'+str(pid)+'/stat').read_text()
 return {'pid':pid,'start_ticks':text[text.rfind(')')+2:].split()[19]}
assert os.getuid()!=0 and 'CREDENTIALS_DIRECTORY' not in os.environ
assert Path.cwd()==work==Path('/run/agc-job/workspace')
assert out==Path('/run/agc-job/output')
assert Path(a.manifest)==Path('/run/agc-job/manifest.json')
assert work.parent.stat().st_uid==0 and work.parent.stat().st_gid==os.getgid()
assert work.parent.stat().st_mode&0o777==0o710
manifest=json.loads(Path(a.manifest).read_text());lease=Path(manifest['worker_lease_file'])
assert lease==Path('/run/agc-job/lease.json')
for path in (Path(a.manifest),lease):
 assert path.stat().st_mode&0o777==0o640
 try:path.open('a').close()
 except OSError as e:assert e.errno in (13,30)
 else:raise AssertionError('metadata writable')
private=json.loads((work/'source/private.json').read_text())
for path in (private['state']+'/fences.json',private['state']+'/jobs/sibling/canary',
             private['credential_canary']):
 try:Path(path).read_bytes()
 except (PermissionError,FileNotFoundError):pass
 else:raise AssertionError('private host path readable')
assert any(' /run/agc-job ' in line for line in Path('/proc/self/mountinfo').read_text().splitlines())
for key in ('HOME','TMPDIR','XDG_CACHE_HOME','npm_config_cache'):
 assert os.environ[key].startswith('/run/agc-job/workspace/')
processes=[identity(os.getpid())]
if a.mode=='timer':
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 child=subprocess.Popen(['/usr/bin/python3','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(300)'])
 processes.append(identity(child.pid))
before=json.loads(lease.read_text())['last_heartbeat_monotonic']
save('ready.json',{'uid':os.getuid(),'gid':os.getgid(),'processes':processes,'private_host_paths_inaccessible':True})
while not (work/'release').exists():
 if json.loads(lease.read_text())['last_heartbeat_monotonic']>before:save('lease.json',{'atomic_replacement_visible':True})
 time.sleep(.05)
if a.mode=='timer':
 while True:time.sleep(.2)
else:
 js=r"""
 const assert=require('node:assert/strict');
 const {chromium}=require('/usr/local/lib/agc-worker-adapters/agc/scripts/compute/node_modules/playwright');
 (async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage();let requests=0;await page.route('**/*',r=>{requests++;return r.abort()});
 await page.goto('data:text/html,<title>AGC private job</title>');assert.equal(await page.title(),'AGC private job');
 assert.equal(requests,0);console.log(JSON.stringify({browser_version:browser.version(),remote_requests:requests}));
 }finally{await browser.close()}})().catch(()=>process.exitCode=1);
 """
 done=subprocess.run(['/usr/bin/node','--max-old-space-size=128','-e',js],capture_output=True,text=True,timeout=45)
 assert done.returncode==0,done.stderr[-2000:]
 save('browser.json',json.loads(done.stdout))
'''


try:
    held()
    base.mkdir(mode=0o700); scratch.mkdir(mode=0o700); results.mkdir(mode=0o700, exist_ok=True)
    state = base/'state'; state.mkdir(mode=0o700)
    (state/'jobs').symlink_to(scratch, target_is_directory=True)
    config = base/'config'; config.mkdir(mode=0o700)
    canary = config/'credential-canary'; canary.write_text('non-secret qualification canary')
    registry = {}
    for mode in ('browser', 'timer'):
        registry['qual.'+mode] = {'enabled': True, 'argv': ['/usr/bin/python3',
            '/run/agc-job/workspace/source/probe.py', '--mode', mode], 'parameter_keys': [],
            'production_writes': mode == 'timer', 'memory_bytes': 1024**3 if mode == 'browser' else 128*1024**2,
            'cpus': 1, 'cpu_quota_percent': 50, 'timeout_seconds': 60 if mode == 'browser' else 150}
    (config/'adapters.json').write_text(json.dumps(registry))
    instance = emergency.BaseWorker(state, config)
    (state/'jobs/sibling').mkdir(mode=0o700); (state/'jobs/sibling/canary').write_text('private')
    instance.started = time.monotonic()-121; instance.healthy_since = time.monotonic()-181
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for name, raw in [('probe.py', PROBE.encode()), ('private.json', json.dumps({
                'state': str(state), 'credential_canary': str(canary)}).encode())]:
            entry = tarfile.TarInfo(name); entry.size = len(raw); archive.addfile(entry, io.BytesIO(raw))
    raw = data.getvalue(); digest = hashlib.sha256(raw).hexdigest()
    worker.install_object(state/'objects', digest, len(raw), io.BytesIO(raw))
    for mode in ('browser', 'timer'):
        held(); instance.host = worker.read_json('/run/bushi-emergency/host.json')
        manifest = {'version': 1, 'kind': 'qual.'+mode, 'source_sha': '0'*40,
            'input_sha256': hashlib.sha256(b'').hexdigest(), 'parameters': {},
            'source_artifact': {'sha256': digest, 'size': len(raw)}, 'production_writes': mode == 'timer'}
        request = {'protocol_version': 1, 'action': 'start', 'job_id': 'qual-'+run_id+'-'+mode,
            'attempt_id': 'attempt-1', 'lease_generation': 1, **{k: manifest[k] for k in ('kind','source_sha','input_sha256')},
            'manifest_sha256': hashlib.sha256(worker.canonical(manifest)).hexdigest(), 'manifest': manifest}
        binding = worker.validate_binding(request)
        receipt = instance.dispatch(request); assert receipt['state'] == 'running', receipt
        unit = receipt['unit']; job = state/'jobs'/instance.key(binding)
        wait_for(lambda: (job/'output/ready.json').exists(), 12, 'unprivileged adapter ready')
        ready = worker.read_json(job/'output/ready.json')
        assert ready['uid'] == __import__('pwd').getpwnam(worker.JOB_USER).pw_uid
        assert stat.S_IMODE(state.stat().st_mode) == stat.S_IMODE(scratch.stat().st_mode) == 0o700
        assert not Path('/run/agc-job/manifest.json').exists(), 'mount escaped unit'
        denied = subprocess.run(['/usr/sbin/runuser','-u',worker.JOB_USER,'--','/usr/bin/test','-r',str(job/'manifest.json')])
        assert denied.returncode == 1, 'host user can read private job metadata'
        instance.dispatch({**binding,'protocol_version':1,'action':'heartbeat'})
        wait_for(lambda: (job/'output/lease.json').exists(), 5, 'atomic heartbeat visible')
        props = dict(line.split('=',1) for line in native_run(['systemctl','show',unit,
            '-p','MemoryMax,MemorySwapMax,CPUQuotaPerSecUSec,ControlGroup,PrivateNetwork,User']).stdout.splitlines() if '=' in line)
        assert props['MemoryMax'] == str(registry[manifest['kind']]['memory_bytes'])
        assert props['MemorySwapMax'] == '0' and props['CPUQuotaPerSecUSec'] == '500ms'
        assert props['PrivateNetwork'] == 'yes' and props['User'] == worker.JOB_USER
        cg = Path('/sys/fs/cgroup')/props['ControlGroup'].lstrip('/')
        assert (cg/'memory.oom.group').read_text().strip() == '1'
        case = {'mode': mode, 'unit': unit, 'properties': props, 'ready': ready}
        evidence['cases'].append(case); began = time.monotonic(); (job/'workspace/release').touch()
        if mode == 'timer':
            # No watchdog or heartbeat: only the independent root timer acts.
            wait_for(lambda: instance.unit_empty(unit), 90, 'independent lease timer')
            case['empty_before_watchdog'] = True
            timer_service = receipt['lease_timer'].replace('.timer','.service')
            timer_result = dict(line.split('=',1) for line in native_run(['systemctl','show',timer_service,
                '-p','Result,ExecMainStatus']).stdout.splitlines() if '=' in line)
            assert timer_result == {'Result':'success','ExecMainStatus':'0'}, timer_result
            case['independent_timer_result'] = timer_result
        deadline = time.monotonic()+50
        while time.monotonic() < deadline:
            instance.tick(); receipt = instance.bound_receipt(binding)
            if receipt['state'] in worker.TERMINAL: break
            time.sleep(.5)
        assert receipt['state'] == ('succeeded' if mode == 'browser' else 'unknown'), receipt
        assert receipt['termination_confirmed'] and instance.unit_empty(unit)
        elapsed = time.monotonic()-began
        if mode == 'timer': assert 75 <= elapsed <= 90, elapsed
        count = len(launched)
        assert instance.dispatch(request)['state'] == receipt['state'] and len(launched) == count
        case.update(receipt=receipt, elapsed_seconds=round(elapsed,2), duplicate_did_not_execute=True)
        if mode == 'browser': case['browser'] = worker.read_json(job/'output/browser.json')
        print(json.dumps({'case': mode,'state':receipt['state'],'elapsed_seconds':round(elapsed,2)}),flush=True)
    held(); evidence['state'] = 'passed'
except BaseException as error:
    evidence.update(state='failed',error=str(error),traceback=traceback.format_exc(limit=8))
finally:
    for unit in sorted(timers,reverse=True)+sorted(units):
        try: native_run(['systemctl','stop',unit],check=False)
        except BaseException as error: evidence.update(state='failed',cleanup_error=str(error))
    if instance:
        evidence['all_cgroups_empty'] = all(instance.unit_empty(unit) for unit in units)
        if not evidence['all_cgroups_empty']: evidence['state'] = 'failed'
    evidence['finished_at'] = time.time()
    if results.is_dir(): worker.atomic_json(results/(run_id+'.json'), evidence)
    if evidence['state'] == 'passed': shutil.rmtree(base); shutil.rmtree(scratch)
    print(json.dumps({'state':evidence['state'],'evidence':str(results/(run_id+'.json')),'source_sha256':evidence['source_sha256']}),flush=True)
raise SystemExit(0 if evidence['state']=='passed' else 1)
