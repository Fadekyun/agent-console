#!/usr/bin/env python3
"""Package reviewed fixed adapters for an operator-only, held worker install."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess


def git_file(repo, revision, name):
    return subprocess.check_output(['git', '-C', str(repo), 'show', revision + ':' + name])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('agc-repo', 'agc-sha', 'points-repo', 'points-sha', 'output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--previous-install-receipt', help='Exact prior installed.json for a held, drained upgrade')
    a = p.parse_args()
    if any(not re.fullmatch('[a-f0-9]{40}', sha) for sha in (a.agc_sha, a.points_sha)):
        p.error('exact reviewed commits required')
    files = {}
    agc_names = [
        'scripts/compute/adapter.py', 'scripts/compute/yuyutei-collect.cjs',
        'scripts/compute/bushi-run.cjs', 'scripts/compute/bushi-collect.cjs',
        'scripts/compute/bushi-apply.cjs',
        'scripts/compute/package.json', 'scripts/compute/package-lock.json',
        'scripts/lib/compute_artifacts.py', 'scripts/lib/yuyutei_hocg_prices.py',
        'scripts/lib/yuyutei_parsed.py',
        'scripts/tournaments/navi-bridge.cjs',
    ]
    agc_names += subprocess.check_output(['git', '-C', a.agc_repo, 'ls-tree', '-r', '--name-only',
        a.agc_sha, '--', 'scripts/compute/bushi/']).decode().splitlines()
    for name in agc_names:
        files['agc/' + name] = git_file(a.agc_repo, a.agc_sha, name)
    for name in ('scripts/compute-worker.cjs', 'src/compute_snapshot.js', 'src/points_rules.js', 'src/navi_events.js'):
        files['points/' + name] = git_file(a.points_repo, a.points_sha, name)
    files['tv/tv_build.py'] = (Path(__file__).resolve().parents[1] / 'adapters/tv_build.py').read_bytes()
    root = '/usr/local/lib/agc-worker-adapters'
    def adapter(argv, memory, cpus, timeout, keys=(), checkpoint=False, write=False, toolchain='debian13'):
        return dict(enabled=True, argv=argv, memory_bytes=memory, cpus=cpus, timeout_seconds=timeout,
                    parameter_keys=list(keys), supports_checkpoint=checkpoint, production_writes=write,
                    root_directory='/var/lib/agc-worker/toolchains/' + toolchain)
    agc = root + '/agc/scripts/compute/'
    registry = {
        'yuyutei.collect': adapter(['/usr/local/bin/node', agc + 'yuyutei-collect.cjs'], 2*1024**3, 1, 5400, checkpoint=True),
        'yuyutei.parse': adapter(['/usr/bin/python3', agc + 'adapter.py'], 2*1024**3, 1, 1800),
        'agc.build': adapter(['/usr/bin/python3', agc + 'adapter.py'], 8*1024**3, 12, 5400, ('storefront','public_env')),
        'agc.test': adapter(['/usr/bin/python3', agc + 'adapter.py'], 8*1024**3, 12, 5400, ('storefront','public_env')),
        'tv.build': adapter(['/usr/bin/python3', root + '/tv/tv_build.py'], 8*1024**3, 12, 5400, toolchain='debian12'),
        'points.weekly': adapter(['/usr/local/bin/node', root + '/points/scripts/compute-worker.cjs'], 2*1024**3, 1, 1800),
        'points.player_events': adapter(['/usr/local/bin/node', root + '/points/scripts/compute-worker.cjs'], 2*1024**3, 1, 1800),
        'bushi.run': adapter(['/usr/local/bin/node', agc + 'bushi-run.cjs'], 2*1024**3, 1, 1800,
             ('action','eventId','gid','applicationNumber','teamId','organizerRowId','orderId','family','include_finished','local_date_from','seriesId','mode'), checkpoint=True, write=True),
    }
    # Credential material is separately provisioned host-locally. Missing material
    # fails closed; this installer never reads, bundles or transmits credentials.
    registry['bushi.run']['credential_ids'] = ['agc-bushi-organizer-state.json']
    receipt = dict(version=1, agc_source=a.agc_sha, points_source=a.points_sha,
                   files={name:hashlib.sha256(data).hexdigest() for name,data in files.items()})
    previous=json.loads(Path(a.previous_install_receipt).read_text()) if a.previous_install_receipt else None
    if previous is not None and (previous.get('version') != 1 or not isinstance(previous.get('files'),dict)):
        raise ValueError('invalid prior installed receipt')
    payload = dict(previous=previous, files={name:base64.b64encode(data).decode() for name,data in files.items()}, registry=registry, receipt=receipt)
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    script = '''#!/bin/bash
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
python3 - <<'ADAPTER_INSTALL'
import base64,hashlib,json,os,pathlib,shutil,socket,tempfile
payload=json.loads(base64.b64decode(PAYLOAD))
root=pathlib.Path('/usr/local/lib/agc-worker-adapters')
registry=pathlib.Path('/etc/agc-worker/adapters.json')
previous=payload['previous']
with socket.socket(socket.AF_UNIX) as client:
 client.settimeout(10);client.connect('/run/agc-worker/rpc.sock');client.sendall(b'{"protocol_version":1,"action":"status"}\\n')
 with client.makefile('rb') as stream:status=json.loads(stream.readline(524288))
 assert status.get('ok') and status.get('active') is None,'Worker must be drained'
if previous is None:
 assert json.loads(registry.read_text()) == {}, 'Initial installer requires empty registry'
 assert not root.exists(), 'Prior adapter install must be reconciled explicitly'
else:
 assert root.is_dir() and not root.is_symlink() and json.loads((root/'installed.json').read_text())==previous,'Prior receipt changed'
 actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
 assert actual==set(previous['files'])|{'installed.json'},'Unexpected old adapter files'
 for name,digest in previous['files'].items():
  p=root/name;s=p.lstat()
  assert not p.is_symlink() and s.st_uid==0 and not s.st_mode&0o022
  assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==digest,'Prior adapter changed'
 for name,target in [('agc/scripts/compute/node_modules','/opt/agc-browser/node_modules'),('browsers','/opt/agc-browser/browsers')]:
  assert (root/name).is_symlink() and os.readlink(root/name)==target,'Prior dependency link changed'
 backup=root.with_name(root.name+'-rollback-'+previous['agc_source'][:12])
 assert not backup.exists(),'Prior backup must be reconciled explicitly'

for name in ('debian12','debian13'):
 p=pathlib.Path('/var/lib/agc-worker/toolchains')/name/'.agc-worker-toolchain.json'
 assert json.loads(p.read_text())['version']==1 and p.stat().st_uid==0
stage=pathlib.Path(tempfile.mkdtemp(prefix='.agc-adapters-',dir='/usr/local/lib'))
try:
 for name,encoded in payload['files'].items():
  relative=pathlib.PurePosixPath(name)
  assert not relative.is_absolute() and '..' not in relative.parts
  data=base64.b64decode(encoded,validate=True)
  assert hashlib.sha256(data).hexdigest()==payload['receipt']['files'][name]
  target=stage/name;target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  target.chmod(0o644)
 (stage/'agc/scripts/compute/node_modules').symlink_to('/opt/agc-browser/node_modules')
 (stage/'browsers').symlink_to('/opt/agc-browser/browsers')
 receipt=stage/'installed.json'
 with receipt.open('x') as stream:json.dump(payload['receipt'],stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
 receipt.chmod(0o644)
 for directory in [stage,*[x for x in stage.rglob('*') if x.is_dir() and not x.is_symlink()]]:
  directory.chmod(0o755);fd=os.open(directory,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
 if previous is not None:os.rename(root,backup)
 os.rename(stage,root)
 fd=os.open(root.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
 temporary=registry.with_suffix('.install')
 with temporary.open('x') as stream:json.dump(payload['registry'],stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
 temporary.chmod(0o600);os.replace(temporary,registry)
 fd=os.open(registry.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
 print(json.dumps({'installed':True,'agc_source':payload['receipt']['agc_source'],'points_source':payload['receipt']['points_source'],'kinds':sorted(payload['registry']),'held':True}))
finally:
 if stage.exists():shutil.rmtree(stage)
ADAPTER_INSTALL
systemctl restart agc-worker.service
'''.replace('PAYLOAD',repr(encoded))
    if len(script.encode()) > 1024**2:
        raise ValueError('setup source exceeds bound')
    output=Path(a.output)
    with output.open('x') as stream: stream.write(script)
    print(json.dumps({'file':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(files)}))


if __name__ == '__main__':
    main()
