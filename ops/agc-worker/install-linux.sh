#!/bin/bash
# Run only as root inside the independent AGC-Worker distribution.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /sys/fs/cgroup/cgroup.controllers
SOURCE=${1:?Path to the reviewed worker source bundle is required}
test -f "$SOURCE/worker.py"
command -v python3 >/dev/null
command -v systemd-run >/dev/null
getent passwd agcjob >/dev/null || useradd --system --create-home --home-dir /var/lib/agc-job-home --shell /usr/sbin/nologin agcjob
install -d -m 0755 /usr/local/lib/agc-worker /etc/agc-worker
install -d -m 0700 /var/lib/agc-worker /var/log/agc-worker
install -m 0755 "$SOURCE/worker.py" /usr/local/lib/agc-worker/worker.py
install -m 0644 "$SOURCE/qualification_gate.py" /usr/local/lib/agc-worker/qualification_gate.py
install -m 0755 "$SOURCE/release-qualified-hold.sh" /usr/local/lib/agc-worker/release-qualified-hold.sh
ln -sfn /usr/local/lib/agc-worker/worker.py /usr/local/bin/agc-worker
if ! test -e /etc/agc-worker/adapters.json; then
  install -m 0600 "$SOURCE/adapters.example.json" /etc/agc-worker/adapters.json
fi
# Only an explicit, later root release removes the hold after quota/VM checks.
touch /etc/agc-worker/hold
cat >/etc/systemd/system/agc-worker-jobs.slice <<'UNIT'
[Unit]
Description=AGC laptop jobs, one admitted job at a time
[Slice]
MemoryAccounting=yes
MemoryHigh=13000000000
MemoryMax=14500000000
MemorySwapMax=0
CPUAccounting=yes
CPUQuota=1200%
CPUWeight=10
IOWeight=10
TasksMax=768
UNIT
cat >/etc/systemd/system/agc-worker.service <<'UNIT'
[Unit]
Description=AGC headless worker lease and receipt supervisor
After=local-fs.target
[Service]
ExecStart=/usr/local/bin/agc-worker serve
# Managed mode: only the gated Windows launcher may start/recover this unit.
# No independent systemd restart while the lifecycle gate is Stopping.
Restart=no
RuntimeDirectory=agc-worker
RuntimeDirectoryMode=0700
MemoryMax=384M
MemorySwapMax=0
CPUQuota=50%
Nice=10
UMask=0077
KillMode=control-group
TimeoutStopSec=15
UNIT
systemctl daemon-reload
# Managed mode: the Windows AGCLifecycle gate is the only boot/launch authority.
# The supervisor is started explicitly by the gated Windows launcher and must
# never re-enable itself independently of that gate.
cat >/etc/agc-worker/managed-mode <<'MARKER'
{"schema":1,"managed":true,"bootGate":"AGCLifecycle","distro":"AGC-Worker","launcher":"Start-Worker.ps1"}
MARKER
chmod 0644 /etc/agc-worker/managed-mode
mkdir -p /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/agc-worker.conf <<'JOURNAL'
[Journal]
SystemMaxUse=128M
SystemMaxFileSize=8M
SystemMaxFiles=16
MaxRetentionSec=7day
SystemKeepFree=256M
# Do not duplicate the same records into rsyslog's legacy files.
ForwardToSyslog=no
JOURNAL
systemctl restart systemd-journald 2>/dev/null || true
# Age vacuum applies to journald files; the active segment may survive until
# rotation, which is documented in docs/2026-10-22-worker-lifecycle.md.
timeout 30 journalctl --vacuum-size=128M --vacuum-time=7d >/dev/null 2>&1 || true
# Bound legacy guest log files. This allowlist is the only deletion surface;
# receipts, data, databases, WAL, backups and worker jobs are never included.
find /var/log -maxdepth 1 -type f \( -name 'syslog*' -o -name 'kern.log*' -o -name 'auth.log*' -o -name 'daemon.log*' \) -mtime +7 -delete 2>/dev/null || true
# Managed install stages and disables only. The Windows gated launcher (or an
# explicit operator qualification run through it) performs the first start.
systemctl disable agc-worker.service >/dev/null 2>&1 || true
