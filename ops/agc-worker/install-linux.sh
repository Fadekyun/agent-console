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
install -d -m 0700 /var/lib/agc-worker
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
Restart=on-failure
RestartSec=5
RuntimeDirectory=agc-worker
RuntimeDirectoryMode=0700
MemoryMax=384M
MemorySwapMax=0
CPUQuota=50%
Nice=10
UMask=0077
KillMode=control-group
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start agc-worker-jobs.slice
systemctl enable agc-worker.service
systemctl restart agc-worker.service
