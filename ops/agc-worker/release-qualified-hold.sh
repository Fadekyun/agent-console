#!/bin/bash
# Operator-only activation after installed native/toolchain qualification.
# Queue owner hold and every dynamic capacity/schedule gate remain independent.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
python3 -I - <<'PY'
import hashlib,json,os,socket,stat,time
from pathlib import Path
worker=Path('/usr/local/lib/agc-worker/worker.py')
expected='c4e193682e3cdcbce5ce90ede8cb00687550b67908db29b40ef908b9d6782e02'
assert hashlib.sha256(worker.read_bytes()).hexdigest()==expected,'Qualified worker changed'
native=Path('/var/lib/agc-worker-qualification-results/ce36f2be6bf84e2fbe275b487722fa1a.json')
result=json.loads(native.read_text())
assert result['state']=='passed' and result['worker_source_sha256']==expected
assert len(result['cases'])==5
root=Path('/usr/local/lib/agc-worker-adapters')
receipt=json.loads((root/'installed.json').read_text())
for name,digest in receipt['files'].items():
    file=root/name;info=file.lstat()
    assert not stat.S_ISLNK(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
    assert hashlib.sha256(file.read_bytes()).hexdigest()==digest,'Installed adapter changed'
registry=json.loads(Path('/etc/agc-worker/adapters.json').read_text())
assert set(registry)=={'yuyutei.collect','yuyutei.parse','agc.build','agc.test','tv.build','points.weekly','points.player_events','bushi.run'}
credential=Path('/etc/agc-worker/credentials/agc-bushi-organizer-state.json')
info=credential.lstat()
assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o600
with socket.socket(socket.AF_UNIX) as client:
    client.settimeout(10);client.connect('/run/agc-worker/rpc.sock')
    client.sendall(b'{"protocol_version":1,"action":"status"}\n')
    with client.makefile('rb') as stream:status=json.loads(stream.readline(524288))
assert status.get('ok') is True and status.get('active') is None,'Worker must be drained'
assert status.get('host',{}).get('policy_verified') is True,'Windows policy seal required'
hold=Path('/etc/agc-worker/hold');assert hold.is_file() and not hold.is_symlink()
hold.unlink()
fd=os.open(hold.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
print(json.dumps({'state':'qualified_operator_hold_released','worker_sha256':expected,
                  'native_evidence':str(native),'released_at':time.time(),
                  'queue_owner_hold':'unchanged','capacity_and_schedule_gates':'unchanged'}))
PY
