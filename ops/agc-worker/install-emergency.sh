#!/bin/bash
# Operator-only CT102 install; no package install, browser login or timer cutover.
# Requires a separately provisioned immutable AGC adapter bundle and credential.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
test "$(id -u)" = 0
test "$(hostname)" = bushi-automation
test "$(systemd-detect-virt --container)" = lxc
test -f /sys/fs/cgroup/cgroup.controllers
SOURCE=${1:?reviewed ops/agc-worker source directory}
ARCHIVE=${2:?reviewed minimal Bushi source archive}
ARCHIVE_SHA=${3:?source archive SHA256}
SOURCE_SHA=${4:?reviewed Git revision}
PUBLIC_KEY=${5:?dedicated CT115 public key file}
CREDENTIAL=${6:?dedicated organizer credential file}
BOUNDARY_RECEIPT=${7:?fresh root-protected N100 boundary enrollment receipt}
test -f "$SOURCE/worker.py"
test -f "$SOURCE/emergency_worker.py"
test -f "$SOURCE/emergency_boundary.py"
test -x /usr/bin/node
test -f /usr/local/lib/agc-worker-adapters/agc/scripts/compute/bushi-run.cjs
test -f /usr/local/lib/agc-worker-adapters/agc/.agc-worker-reviewed.json
python3 - "$ARCHIVE" "$ARCHIVE_SHA" "$SOURCE_SHA" "$PUBLIC_KEY" "$CREDENTIAL" "$BOUNDARY_RECEIPT" "$SOURCE" <<'PY'
import hashlib,json,os,re,stat,sys
from pathlib import Path
archive,digest,revision,key,credential,boundary,source=sys.argv[1:]
assert re.fullmatch('[0-9a-f]{64}',digest) and re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',revision)
p=Path(archive);assert 0<p.stat().st_size<=16*1024**2
assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==digest
assert re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',Path(key).read_text().strip())
p=Path(credential);s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077 and 0<s.st_size<=1024**2
assert isinstance(json.loads(p.read_text()),dict)
root=Path('/usr/local/lib/agc-worker-adapters/agc')
for p in [root,*root.rglob('*')]:
 s=p.lstat();assert s.st_uid==0 and (p.is_symlink() or not s.st_mode&0o022),str(p)
 if p.is_symlink(): assert p.resolve(strict=True).is_relative_to(root),str(p)
marker=json.loads((root/'.agc-worker-reviewed.json').read_text())
assert marker['source_sha']==revision
assert marker['wrapper_sha256']==hashlib.file_digest((root/'scripts/compute/bushi-run.cjs').open('rb'),'sha256').hexdigest()
helper=Path(source)/'emergency_boundary.py'
for p in (Path(boundary),helper):
 assert p.is_absolute()
 for parent in (p,*p.parents):
  s=parent.lstat();assert s.st_uid==0 and not s.st_mode&0o022 and not stat.S_ISLNK(s.st_mode),'Protected enrollment/source required'
 assert p.is_file() and p.stat().st_size<=65536
import runpy
check=runpy.run_path(str(helper))['validate_enrollment']
check(json.loads(Path(boundary).read_text()),fresh=True)
# Visible root may be max because Proxmox enforces a hidden /lxc/102 parent.
# The authenticated host receipt supplies that actual boundary, never an arg.
visible=Path('/sys/fs/cgroup/memory.max').read_text().strip()
assert visible=='max' or visible.isdecimal() and int(visible)>0,'Invalid visible cgroup memory limit'
PY
test ! -e /etc/bushi-emergency/policy.json || python3 - <<'PY'
import json,socket
from pathlib import Path
p=json.load(open('/etc/bushi-emergency/policy.json'))
assert p.get('enabled') is False,'hold/drain and disable policy before reinstall'
assert Path('/etc/bushi-emergency/hold').is_file(),'hold existing emergency admissions first'
if Path('/run/bushi-emergency/rpc.sock').exists():
 with socket.socket(socket.AF_UNIX) as client:
  client.settimeout(10);client.connect('/run/bushi-emergency/rpc.sock')
  client.sendall(b'{"protocol_version":1,"action":"status"}\n')
  with client.makefile('rb') as stream: status=json.loads(stream.readline(524288))
  assert status.get('ok') is True and status.get('active') is None,'existing emergency job must drain first'
PY
if systemctl is-active --quiet bushi-emergency.service; then systemctl stop bushi-emergency.service; fi
install -d -m 0700 /etc/bushi-emergency
python3 - <<'PY'
import json,pwd,subprocess
from pathlib import Path
receipt=Path('/etc/bushi-emergency/accounts.json')
owned=json.loads(receipt.read_text()) if receipt.exists() else {}
for name,shell in [('bushi-emergency-job','/usr/sbin/nologin'),('bushi-compute','/bin/sh')]:
 try:
  account=pwd.getpwnam(name)
  assert owned.get(name)==account.pw_uid,'Existing unmanaged emergency account: '+name
 except KeyError:
  subprocess.run(['useradd','--system','--create-home','--shell',shell,name],check=True)
  owned[name]=pwd.getpwnam(name).pw_uid
  temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(owned));temporary.chmod(0o600);temporary.replace(receipt)
PY
test "$(id -u bushi-emergency-job)" != 0
test "$(id -u bushi-compute)" != 0
# An impossible password permits public-key account checks; Match policy below
# still disables password/interactive authentication for this dedicated user.
usermod --password '*' bushi-compute
# Preserve unmanaged accounts: reject any privileged supplementary groups.
python3 - <<'PY'
import grp,pwd
for name in ('bushi-emergency-job','bushi-compute'):
 account=pwd.getpwnam(name)
 groups={g.gr_name for g in grp.getgrall() if name in g.gr_mem}
 assert not groups, (name,'unexpected supplementary groups')
 assert account.pw_gid!=0
PY
install -d -m 0755 /usr/local/lib/bushi-emergency
install -d -m 0700 /etc/bushi-emergency /etc/bushi-emergency/credentials /var/lib/bushi-emergency /var/lib/bushi-emergency/objects
install -d -m 0700 /var/lib/bushi-emergency/jobs
install -m 0755 "$SOURCE/worker.py" /usr/local/lib/bushi-emergency/worker.py
install -m 0644 "$SOURCE/emergency_boundary.py" /usr/local/lib/bushi-emergency/emergency_boundary.py
install -m 0755 "$SOURCE/emergency_worker.py" /usr/local/lib/bushi-emergency/emergency_worker.py
install -m 0600 "$ARCHIVE" "/var/lib/bushi-emergency/objects/$ARCHIVE_SHA"
install -m 0600 "$CREDENTIAL" /etc/bushi-emergency/credentials/agc-bushi-organizer-state.json
touch /etc/bushi-emergency/hold
python3 - "$ARCHIVE" "$ARCHIVE_SHA" "$SOURCE_SHA" "$BOUNDARY_RECEIPT" <<'PY'
import json,os,sys
from pathlib import Path
archive,digest,revision,boundary=sys.argv[1:]
sys.path.insert(0,'/usr/local/lib/bushi-emergency')
from emergency_boundary import validate_enrollment
enrollment=validate_enrollment(json.loads(Path(boundary).read_text()),fresh=True)
root=Path('/etc/bushi-emergency')
policy={'version':1,'enabled':False,'installed_source_sha':revision,
 'source_artifact':{'sha256':digest,'size':Path(archive).stat().st_size},'boundary_enrollment':enrollment}
adapter={'bushi.emergency':{'enabled':False,'argv':['/usr/bin/node','/usr/local/lib/agc-worker-adapters/agc/scripts/compute/bushi-run.cjs'],
 'memory_bytes':1024**3,'cpus':1,'cpu_quota_percent':50,'timeout_seconds':1200,
 'parameter_keys':['action','eventId','gid','applicationNumber','teamId','organizerRowId','orderId'],
 'production_writes':True,'supports_checkpoint':False,'credential_ids':['agc-bushi-organizer-state.json']}}
for name,value in [('policy.json',policy),('adapters.json',adapter)]:
 p=root/name;p.write_text(json.dumps(value,sort_keys=True)+'\n');p.chmod(0o600)
PY
mount_unit=$(systemd-escape --path --suffix=mount /var/lib/bushi-emergency/jobs)
cat >"/etc/systemd/system/$mount_unit" <<'UNIT'
[Unit]
Description=Bounded Bushi emergency scratch only
[Mount]
What=tmpfs
Where=/var/lib/bushi-emergency/jobs
Type=tmpfs
Options=size=536870912,mode=0700,nosuid,nodev,noexec
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/bushi-emergency-jobs.slice <<'UNIT'
[Unit]
Description=One tightly capped Bushi emergency browser
[Slice]
MemoryMax=1073741824
MemorySwapMax=0
CPUQuota=50%
CPUWeight=5
IOWeight=5
TasksMax=128
UNIT
cat >/etc/systemd/system/bushi-emergency.slice <<'UNIT'
[Unit]
Description=Aggregate emergency browser and supervisor budget
[Slice]
MemoryMax=1280M
MemorySwapMax=0
CPUQuota=50%
CPUWeight=5
IOWeight=5
TasksMax=160
UNIT
cat >/etc/systemd/system/bushi-emergency.service <<UNIT
[Unit]
Description=Fenced Bushi emergency worker (default disabled admission)
Requires=$mount_unit
After=$mount_unit
[Service]
ExecStart=/usr/bin/python3 -I /usr/local/lib/bushi-emergency/emergency_worker.py serve
Slice=bushi-emergency.slice
Restart=on-failure
RestartSec=5
RuntimeDirectory=bushi-emergency
RuntimeDirectoryMode=0700
MemoryMax=128M
MemorySwapMax=0
CPUQuota=50%
CPUWeight=5
IOWeight=5
Nice=15
UMask=0077
KillMode=control-group
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
UNIT
cat >/usr/local/sbin/bushi-emergency-rpc <<'SH'
#!/bin/sh
# sudo rule accepts no arguments; caller input is bounded JSON only.
test "$#" -eq 0 || exit 64
exec /usr/bin/python3 -I /usr/local/lib/bushi-emergency/emergency_worker.py rpc
SH
chmod 0755 /usr/local/sbin/bushi-emergency-rpc
printf '%s\n' 'bushi-compute ALL=(root) NOPASSWD: /usr/local/sbin/bushi-emergency-rpc ""' >/etc/sudoers.d/bushi-emergency
chmod 0440 /etc/sudoers.d/bushi-emergency
visudo -cf /etc/sudoers.d/bushi-emergency >/dev/null
key=$(cat "$PUBLIC_KEY")
printf 'from="192.168.1.115",restrict,command="/usr/bin/sudo -n /usr/local/sbin/bushi-emergency-rpc" %s\n' "$key" >/etc/ssh/bushi-compute_authorized_keys
chmod 0644 /etc/ssh/bushi-compute_authorized_keys
ssh_fragment=/etc/ssh/sshd_config.d/70-bushi-emergency.conf
ssh_backup=$(mktemp /etc/bushi-emergency/ssh-fragment-before.XXXXXX)
ssh_had_before=false
if test -e "$ssh_fragment"; then cp -- "$ssh_fragment" "$ssh_backup"; ssh_had_before=true; fi
restore_ssh_fragment() {
  if test "$ssh_had_before" = true; then cp -- "$ssh_backup" "$ssh_fragment"; else rm -f -- "$ssh_fragment"; fi
}
trap restore_ssh_fragment ERR
cat >/etc/ssh/sshd_config.d/70-bushi-emergency.conf <<'SSH'
# Console #203 dedicated CT115-initiated fixed JSON RPC; no new listening port.
Match User bushi-compute
    AuthorizedKeysFile /etc/ssh/bushi-compute_authorized_keys
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitUserRC no
    ForceCommand /usr/bin/sudo -n /usr/local/sbin/bushi-emergency-rpc
Match all
SSH
/usr/sbin/sshd -t
effective=$(/usr/sbin/sshd -T -C user=bushi-compute,host=bushi-automation,addr=192.168.1.115)
printf '%s\n' "$effective" | grep -Fx 'forcecommand /usr/bin/sudo -n /usr/local/sbin/bushi-emergency-rpc' >/dev/null
printf '%s\n' "$effective" | grep -Fx 'authorizedkeysfile /etc/ssh/bushi-compute_authorized_keys' >/dev/null
printf '%s\n' "$effective" | python3 -c 'import fnmatch,sys; lines=sys.stdin.read().splitlines(); allow=[p for s in lines if s.startswith("allowusers ") for p in s.split()[1:]]; deny=[p for s in lines if s.startswith("denyusers ") for p in s.split()[1:]]; assert not allow or any(fnmatch.fnmatch("bushi-compute",p) for p in allow),"Existing SSH AllowUsers excludes worker; operator review required"; assert not any(fnmatch.fnmatch("bushi-compute",p) for p in deny),"Existing SSH DenyUsers excludes worker"'
systemctl daemon-reload
systemctl start "$mount_unit" bushi-emergency-jobs.slice
systemctl enable --now bushi-emergency.service
if systemctl is-active --quiet ssh.socket; then
  test "$(systemctl show ssh.service -p KillMode --value)" = process
  # SIGHUP cannot safely reuse systemd's inherited listening descriptors here.
  # KillMode=process retains established connection children across the restart.
  systemctl restart ssh.service
else
  systemctl reload ssh.service
fi
systemctl is-active --quiet ssh.service
trap - ERR
rm -f -- "$ssh_backup"
printf '%s\n' '{"worker_id":"bushi-emergency","state":"installed_held_disabled","policy":"/etc/bushi-emergency/policy.json","requires":"root native qualification and global organizer fence before enablement"}'
