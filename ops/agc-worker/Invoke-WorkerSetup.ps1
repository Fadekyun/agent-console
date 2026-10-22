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
$Distro = 'AGC-Worker'
$result = @{version=1;source_sha256=$Sha256;label=$Label;state='starting';started_at=[DateTimeOffset]::UtcNow.ToString('o')}
function Save-Receipt {
    $result | ConvertTo-Json | Set-Content -LiteralPath "$Receipt.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$Receipt.tmp" -Destination $Receipt -Force
}
try {
    Start-Transcript -Path $Log -Force | Out-Null
    Save-Receipt
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1'))) { throw 'lifecycle_library_unavailable' }
    . (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1') -DataPath $Data
    if (-not (Initialize-AgcWorkerLifecycle)) { throw 'lifecycle_library_unavailable' }
    $gate = Get-AgcWorkerGate
    if (-not $gate.Allowed) {
        # A closed/unhealthy gate must not touch the hold or start WSL. The
        # operator RPC/task remains a refused start, not a partial setup.
        $result.state = 'setup_suppressed'
        $result.reason = $gate.Reason
        return
    }
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
    # not killed by operator setup; the coordinator must drain it first. Every
    # launch is atomic-admitted; the gate is rechecked between steps.
    $held = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/touch','/etc/agc-worker/hold') -TimeoutMilliseconds 20000
    if ($held.Started -ne $true -or $held.TimedOut -eq $true -or $held.ExitCode -ne 0) { throw 'worker_hold_failed' }
    Assert-AgcWorkerGateOpen | Out-Null
    $statusText = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/local/bin/agc-worker','rpc') -TimeoutMilliseconds 30000 -StandardInput ('{"protocol_version":1,"action":"status"}' + [Environment]::NewLine)
    if ($statusText.Started -ne $true -or $statusText.OutputTruncated -eq $true -or $statusText.TimedOut -eq $true -or $statusText.ExitCode -ne 0) { throw 'worker_status_unavailable' }
    $status = ([string]$statusText.Stdout) | ConvertFrom-Json
    if ($status.ok -ne $true -or $null -ne $status.active) { throw 'worker_not_drained' }
    Assert-AgcWorkerGateOpen | Out-Null
    $mkdir = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/mkdir','-p','/opt/agc-worker-setup') -TimeoutMilliseconds 20000
    if ($mkdir.Started -ne $true -or $mkdir.TimedOut -eq $true -or $mkdir.ExitCode -ne 0) { throw 'worker_setup_directory_failed' }
    $linuxSource = "/opt/agc-worker-setup/$Sha256.sh"
    $decode = "import base64,hashlib,os,sys; data=base64.b64decode(sys.stdin.buffer.read().strip(),validate=True); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; tmp=sys.argv[1]+'.tmp'; f=open(tmp,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,sys.argv[1])"
    $encoded = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Source))
    $copy = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/python3','-c',$decode,$linuxSource,$Sha256) -TimeoutMilliseconds 120000 -StandardInput $encoded
    if ($copy.Started -ne $true -or $copy.OutputTruncated -eq $true -or $copy.TimedOut -eq $true -or $copy.ExitCode -ne 0) { throw 'worker_setup_copy_failed' }
    Assert-AgcWorkerGateOpen | Out-Null
    $chmod = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/chmod','0700',$linuxSource) -TimeoutMilliseconds 20000
    if ($chmod.Started -ne $true -or $chmod.TimedOut -eq $true -or $chmod.ExitCode -ne 0) { throw 'worker_setup_permissions_failed' }
    Assert-AgcWorkerGateOpen | Out-Null
    $result.state='running'; Save-Receipt
    # Setup remains root only inside this worker VM; no Windows admin token or
    # staff distribution is used. Central queue must be held during setup.
    # Recheck the gate immediately before the long bounded unit.
    Assert-AgcWorkerGateOpen | Out-Null
    $bounded = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/systemd-run','--wait','--pipe','--collect',"--unit=agc-setup-$($Sha256.Substring(0,24))",'--setenv=WSL_DISTRO_NAME=AGC-Worker','--slice=agc-worker-jobs.slice','--property=MemoryHigh=1792M','--property=MemoryMax=2G','--property=MemorySwapMax=0','--property=CPUQuota=100%','--property=CPUWeight=10','--property=IOWeight=10','--property=Nice=10','--property=TasksMax=256',"--property=RuntimeMaxSec=$TimeoutSeconds",'--property=TimeoutStopSec=15','--property=KillMode=control-group','--property=OOMPolicy=kill','--','/bin/bash',$linuxSource) -TimeoutMilliseconds (($TimeoutSeconds + 30) * 1000) -MaxOutputBytes 1048576
    $result.exit_code = if ($null -eq $bounded.ExitCode) { -1 } else { [int]$bounded.ExitCode }
    if ($bounded.Started -ne $true -or $bounded.TimedOut -eq $true -or $bounded.OutputTruncated -eq $true -or $result.exit_code -ne 0) { throw 'bounded_worker_setup_failed' }
    $result.state='succeeded'
} catch {
    if ($_.Exception.Message -eq 'lifecycle_library_unavailable') {
        $result.state='failed'; $result.error_type='lifecycle_library_unavailable'
    } else {
        $result.state='failed'
        $result.error_type=$_.Exception.GetType().Name
        $result.message='Operator setup failed; inspect the protected setup log'
        $result.exit_code=$LASTEXITCODE
    }
} finally {
    $result.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
    Save-Receipt
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
if ($result.state -eq 'setup_suppressed') { exit 0 }
if ($result.state -ne 'succeeded') { exit 1 }
