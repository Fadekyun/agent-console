#!/usr/bin/python3
"""Create an exact, self-contained operator upgrade and qualification bundle."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess


def digest(data): return hashlib.sha256(data).hexdigest()


def make_bundle(repo, revision, before, new_worker):
    if not re.fullmatch('[a-f0-9]{40}', revision): raise ValueError('exact_source_revision_required')
    if set(before) != {'worker.py','qualification_gate.py','release-qualified-hold.sh'}: raise ValueError('exact_file_set_required')
    if not before['worker.py']: raise ValueError('old_worker_digest_required')
    if not re.fullmatch('[a-f0-9]{64}', new_worker) or any(value is not None and not re.fullmatch('[a-f0-9]{64}', value) for value in before.values()):
        raise ValueError('exact_hashes_required')
    def source(name):
        return subprocess.check_output(['git','-C',str(repo),'show',revision+':ops/agc-worker/'+name])
    files = {}
    for name, mode in [('worker.py',0o755),('qualification_gate.py',0o644),('release-qualified-hold.sh',0o755)]:
        data = source(name)
        files[name] = dict(before_sha256=before[name],sha256=digest(data),mode=mode,data=base64.b64encode(data).decode())
    if files['worker.py']['sha256'] != new_worker: raise ValueError('reviewed_worker_hash_mismatch')
    plan = dict(version=1,source_revision=revision,files=files)
    encoded = base64.b64encode(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).decode()
    updater = Path(__file__).with_name('private_job_upgrade.py').read_bytes()
    shell = b'#!/bin/bash\nset -euo pipefail\numask 0077\n/usr/bin/python3 -I - <<\'AGC_PRIVATE_JOB_UPGRADE\'\n'
    bundle = {'upgrade-linux.sh':shell+updater+b'\nmain(json.loads(base64.b64decode("'+encoded.encode()+b'")))\nAGC_PRIVATE_JOB_UPGRADE\n'}
    for name in ('qualify-native.sh','qualify-toolchains.sh'):
        # These fixed wrappers refuse any different installed worker before
        # executing the exact selected qualifier source.
        guard = ('#!/bin/bash\nset -euo pipefail\n'
            '/usr/bin/python3 -I -c \'import hashlib;from pathlib import Path;'
            'assert hashlib.sha256(Path("/usr/local/lib/agc-worker/worker.py").read_bytes()).hexdigest()=="'+new_worker+'"\'\n').encode()
        bundle[name] = guard+source(name)
    for name in ('Start-Worker.ps1','Verify-WorkerPolicy.ps1'): bundle[name] = source(name)
    manifest = dict(version=1,source_revision=revision,worker_sha256=new_worker,updater_sha256=digest(updater),
        installed_files={name:{k:v for k,v in item.items() if k!='data'} for name,item in files.items()},
        files={name:dict(sha256=digest(data),size=len(data)) for name,data in bundle.items()},
        required_existing_setup_helpers={name:digest(source(name)) for name in ('Setup-Worker.ps1','Invoke-WorkerSetup.ps1','Host-Disk.ps1')},
        windows_handover='Root-reviewed watchdog-only PID/task handover after fresh inspection; no graceful stop/WSL termination',
        qualification='Fresh receipts required; no existing evidence authorizes release')
    return bundle, manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repo','source-revision','old-worker-sha256','new-worker-sha256','output'): p.add_argument('--'+name,required=True)
    p.add_argument('--old-gate-sha256',help='Omit only when installed qualification_gate.py is absent')
    p.add_argument('--old-release-sha256',help='Omit only when installed release-qualified-hold.sh is absent')
    a = p.parse_args()
    bundle, manifest = make_bundle(a.repo,a.source_revision,{'worker.py':a.old_worker_sha256,
        'qualification_gate.py':a.old_gate_sha256,'release-qualified-hold.sh':a.old_release_sha256},a.new_worker_sha256)
    destination = Path(a.output); destination.mkdir(mode=0o700)
    for name,data in bundle.items():
        (destination/name).write_bytes(data); (destination/name).chmod(0o600)
    raw = (json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    (destination/'manifest.json').write_bytes(raw); (destination/'manifest.json').chmod(0o600)
    print(json.dumps({'bundle':str(destination),'manifest_sha256':digest(raw),'files':manifest['files']}))


if __name__ == '__main__': main()
