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
systemctl is-active agc-worker.service agc-worker-jobs.slice
systemctl show agc-worker-jobs.slice -p ControlGroup -p MemoryMax -p MemoryHigh -p MemorySwapMax -p CPUQuotaPerSecUSec -p TasksMax
cat /etc/wsl.conf
printf '\nWorker process owner: '
id agcjob
