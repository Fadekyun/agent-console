# Invoked only by the administrator-installed, limited hidden worker task.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$Sha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]{1,40}$')][string]$Label,
    [ValidateRange(60,3600)][int]$TimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'Host-Disk.ps1')
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong worker Windows account' }
$Data = 'C:\ProgramData\AGC-Worker\data'
$Source = "C:\ProgramData\AGC-Worker\code\setup\$Sha256.sh"
$Receipt = Join-Path $Data "setup-$Label-$Sha256.json"
$Log = Join-Path $Data "setup-$Label-$Sha256.log"
$result = @{version=1;source_sha256=$Sha256;label=$Label;state='starting';started_at=[DateTimeOffset]::UtcNow.ToString('o')}
function Save-Receipt {
    $result | ConvertTo-Json | Set-Content -LiteralPath "$Receipt.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$Receipt.tmp" -Destination $Receipt -Force
}
try {
    Start-Transcript -Path $Log -Force | Out-Null
    Save-Receipt
    $now = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
    if ([int]$now.DayOfWeek -in @(1,2) -or ([int]$now.DayOfWeek -eq 0 -and $now.TimeOfDay -ge [TimeSpan]::FromMinutes(1425))) { throw 'outside_worker_schedule' }
    Add-Type -AssemblyName System.Windows.Forms
    if ([Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -ne 'Online') { throw 'worker_not_on_ac' }
    $os = Get-CimInstance Win32_OperatingSystem
    $disk = Get-WorkerDiskSpace
    $result.disk = $disk
    $reserve = [Math]::Max(8GB,[uint64]$os.TotalVisibleMemorySize * 1024 * .25)
    if ([uint64]$os.FreePhysicalMemory * 1024 -lt ($reserve + 2GB) -or $disk.AvailableFreeSpace -lt ($disk.TotalSize * .15 + 8GB) -or $disk.QuotaAvailableBytes -lt 8GB) { throw 'staff_headroom_hold' }
    if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) { throw 'setup_source_digest_changed' }
    # Hold new admissions before inspecting the active slot. Existing work is
    # not killed by operator setup; the coordinator must drain it first.
    & wsl.exe -d AGC-Worker -u root --exec /usr/bin/touch /etc/agc-worker/hold
    if ($LASTEXITCODE -ne 0) { throw 'worker_hold_failed' }
    $statusText = '{"protocol_version":1,"action":"status"}' | & wsl.exe -d AGC-Worker -u root --exec /usr/local/bin/agc-worker rpc
    if ($LASTEXITCODE -ne 0) { throw 'worker_status_unavailable' }
    $status = ($statusText -join "`n") | ConvertFrom-Json
    if ($status.ok -ne $true -or $null -ne $status.active) { throw 'worker_not_drained' }
    & wsl.exe -d AGC-Worker -u root --exec /usr/bin/mkdir -p /opt/agc-worker-setup
    if ($LASTEXITCODE -ne 0) { throw 'worker_setup_directory_failed' }
    $linuxSource = "/opt/agc-worker-setup/$Sha256.sh"
    $decode = "import base64,hashlib,os,sys; data=base64.b64decode(sys.stdin.buffer.read().strip(),validate=True); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; tmp=sys.argv[1]+'.tmp'; f=open(tmp,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,sys.argv[1])"
    [Convert]::ToBase64String([IO.File]::ReadAllBytes($Source)) | & wsl.exe -d AGC-Worker -u root --exec /usr/bin/python3 -c $decode $linuxSource $Sha256
    if ($LASTEXITCODE -ne 0) { throw 'worker_setup_copy_failed' }
    & wsl.exe -d AGC-Worker -u root --exec /usr/bin/chmod 0700 $linuxSource
    if ($LASTEXITCODE -ne 0) { throw 'worker_setup_permissions_failed' }
    $result.state='running'; Save-Receipt
    # Setup remains root only inside this worker VM; no Windows admin token or
    # staff distribution is used. Central queue must be held during setup.
    & wsl.exe -d AGC-Worker -u root --exec /usr/bin/systemd-run --wait --pipe --collect "--unit=agc-setup-$($Sha256.Substring(0,24))" --setenv=WSL_DISTRO_NAME=AGC-Worker --slice=agc-worker-jobs.slice --property=MemoryHigh=1792M --property=MemoryMax=2G --property=MemorySwapMax=0 --property=CPUQuota=100% --property=CPUWeight=10 --property=IOWeight=10 --property=Nice=10 --property=TasksMax=256 "--property=RuntimeMaxSec=$TimeoutSeconds" --property=TimeoutStopSec=15 --property=KillMode=control-group --property=OOMPolicy=kill -- /bin/bash $linuxSource
    $result.exit_code=$LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'bounded_worker_setup_failed' }
    $result.state='succeeded'
} catch {
    $result.state='failed'
    $result.error_type=$_.Exception.GetType().Name
    $result.message='Operator setup failed; inspect the protected setup log'
    $result.exit_code=$LASTEXITCODE
} finally {
    $result.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
    Save-Receipt
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
if ($result.state -ne 'succeeded') { exit 1 }
