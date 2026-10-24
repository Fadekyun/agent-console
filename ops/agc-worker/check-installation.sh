#!/bin/bash
# Read-only checks inside the newly provisioned, held worker VM.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
test "$(nproc)" = 12
test "$(awk '/SwapTotal:/{print $2}' /proc/meminfo)" = 0
python3 - <<'PY'
import json,pathlib,subprocess
mem={line.split(':')[0]:int(line.split()[1])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if len(line.split())==3}
assert 13000000000 < mem['MemTotal'] <= 15000000000
print(json.dumps({'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'cpus':int(subprocess.check_output(['nproc'])),'memory_total_bytes':mem['MemTotal'],'memory_available_bytes':mem['MemAvailable'],'swap_bytes':mem['SwapTotal'],'hold':True}))
PY
# Managed install stages/disables only; the gated Windows launcher starts the
# supervisor. Do not require the unit to be active here.
systemctl is-active agc-worker-jobs.slice >/dev/null 2>&1 || true
test -f /etc/agc-worker/managed-mode
test "$(systemctl is-enabled agc-worker.service 2>/dev/null || true)" != enabled
test -f /etc/systemd/journald.conf.d/agc-worker.conf
test -d /var/log/agc-worker
grep -q 'SystemMaxUse=128M' /etc/systemd/journald.conf.d/agc-worker.conf
grep -q 'MaxRetentionSec=7day' /etc/systemd/journald.conf.d/agc-worker.conf
systemctl show agc-worker-jobs.slice -p ControlGroup -p MemoryMax -p MemoryHigh -p MemorySwapMax -p CPUQuotaPerSecUSec -p TasksMax
cat /etc/wsl.conf
printf '\nWorker process owner: '
id agcjob
