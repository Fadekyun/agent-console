# Runs in a hidden, noninteractive task under agc-worker, never staff context.
# The shared AGCLifecycle gate is consulted before any wsl.exe launch. A stop
# request closes worker admission and drains within the coordinator budget.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong worker account' }
$Base = 'C:\ProgramData\AGC-Worker'
$Data = Join-Path $Base 'data'
$Code = Join-Path $Base 'code'
$Distro = 'AGC-Worker'
. (Join-Path $PSScriptRoot 'Host-Disk.ps1')
. (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1')
$lifecycle = Initialize-AgcWorkerLifecycle
if (-not $lifecycle) {
    # Fail closed without any fallback disk I/O.
    exit 0
}
if (-not (Test-AgcWorkerAdmissionApi)) {
    # The atomic bounded admission overload is required for all ordinary WSL
    # launches; refuse to start rather than reproduce a check-then-start race.
    exit 0
}
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class AgcHost {
 [StructLayout(LayoutKind.Sequential)] public struct Memory {
  public uint length,load; public ulong totalPhysical,availablePhysical,totalCommit,availableCommit,totalVirtual,availableVirtual,extended;
 }
 [StructLayout(LayoutKind.Sequential)] public struct Power {
  public byte ac,battery,percent,reserved; public uint lifetime,fullLifetime;
 }
 [DllImport("kernel32.dll")] public static extern bool GlobalMemoryStatusEx(ref Memory value);
 [DllImport("kernel32.dll")] public static extern bool GetSystemPowerStatus(out Power value);
 [DllImport("kernel32.dll")] public static extern bool GetSystemTimes(out ulong idle,out ulong kernel,out ulong user);
 public static Memory ReadMemory() {var m=new Memory();m.length=(uint)Marshal.SizeOf(m);if(!GlobalMemoryStatusEx(ref m))throw new Exception("memory telemetry unavailable");return m;}
 public static bool OnAC() {Power p;if(!GetSystemPowerStatus(out p))return false;return p.ac==1;}
}
'@
function In-Window {
    $now = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
    return ([int]$now.DayOfWeek -notin @(1,2)) -and -not ([int]$now.DayOfWeek -eq 0 -and $now.TimeOfDay -ge [TimeSpan]::FromMinutes(1425))
}
$gate = Get-AgcWorkerGate
if (-not $gate.Allowed) {
    Write-AgcWorkerLog -Event 'worker_start_suppressed' -Data @{reason=$gate.Reason; error=$gate.Error}
    exit 0
}
if (-not (In-Window) -or -not [AgcHost]::OnAC()) {
    Write-AgcWorkerLog -Event 'worker_start_suppressed' -Data @{reason='schedule_or_power'}
    exit 0
}
$initialMemory = [AgcHost]::ReadMemory()
$initialDisk = Get-WorkerDiskSpace
if ($initialMemory.availablePhysical -lt ([Math]::Max(8GB,$initialMemory.totalPhysical * .25) + 1000000000) -or $initialDisk.AvailableFreeSpace -lt ($initialDisk.TotalSize * .15)) {
    Write-AgcWorkerLog -Event 'worker_start_suppressed' -Data @{reason='staff_headroom'}
    exit 0
}
if (-not (Test-Path (Join-Path $Data 'distro-ready.json'))) { throw 'Independent distro was not initialized' }
$self = Get-Process -Id $PID
$self.PriorityClass = 'BelowNormal'
$keepAlive = $null
$stopRequest = $null
$suppressed = $false
$previousIdle = [uint64]0; $previousKernel = [uint64]0; $previousUser = [uint64]0
[void][AgcHost]::GetSystemTimes([ref]$previousIdle,[ref]$previousKernel,[ref]$previousUser)
$logical = [Environment]::ProcessorCount
$staffPressureSince = $null
try {
    $keepAlive = Start-AgcWorkerKeepAlive -Distro $Distro -Seconds 600
    if ($null -eq $keepAlive) {
        $suppressed = $true
        Write-AgcWorkerLog -Event 'worker_start_suppressed' -Data @{reason='admission_refused'}
        return
    }
    Start-Sleep -Seconds 2
    # Close the admission/stop race immediately after process creation. A stop
    # that arrived between the first gate check and this launch wins.
    $gate = Get-AgcWorkerGate
    if (-not $gate.Allowed) {
        $stopRequest = $gate.StopRequest
        Write-AgcWorkerLog -Event 'worker_start_aborted' -Data @{reason=$gate.Reason}
        return
    }
    $start = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/systemctl','start','agc-worker.service') -TimeoutMilliseconds 20000
    if ($start.Started -ne $true) {
        if ($start.Suppressed -eq $true) { $suppressed = $true; Write-AgcWorkerLog -Event 'worker_start_suppressed' -Data @{reason='admission_refused'}; return }
        throw 'Worker supervisor unavailable'
    }
    if ($start.TimedOut -eq $true -or ($null -ne $start.ExitCode -and [int]$start.ExitCode -ne 0)) { throw 'Worker supervisor unavailable' }
    Write-AgcWorkerLog -Event 'worker_started' -Data @{boot_id=([DateTimeOffset]::UtcNow.ToString('o'))}
    while (In-Window) {
        $gate = Get-AgcWorkerGate
        if ($null -ne $gate.StopRequest -or -not $gate.Allowed) {
            $stopRequest = $gate.StopRequest
            Write-AgcWorkerLog -Event 'worker_stop_observed' -Data @{reason=$gate.Reason; request_id=$gate.StopRequest.RequestId}
            break
        }
        $memory = [AgcHost]::ReadMemory()
        $drive = Get-WorkerDiskSpace
        $idle=[uint64]0;$kernel=[uint64]0;$user=[uint64]0
        if (-not [AgcHost]::GetSystemTimes([ref]$idle,[ref]$kernel,[ref]$user)) { throw 'CPU telemetry unavailable' }
        $total = ($kernel - $previousKernel) + ($user - $previousUser)
        $cpuPercent = if ($total -gt 0) {100.0 * (1.0 - (($idle - $previousIdle) / $total))} else {100.0}
        $previousIdle=$idle;$previousKernel=$kernel;$previousUser=$user
        # Conservative: count every WSL VM's Windows private commitment. This
        # over-reserves staff WSL too, rather than undercounting worker overhead.
        $vmmem = @(Get-Process -Name 'vmmemWSL' -ErrorAction SilentlyContinue)
        $workerBytes = [int64](($vmmem | Measure-Object PrivateMemorySize64 -Sum).Sum) + [int64](Get-Process -Id $PID).PrivateMemorySize64
        # Use the SID quota counters instead of an unbounded recursive scan in
        # the 2 s telemetry loop. If the quota is unavailable, fail closed at
        # the 300 GB ceiling rather than reporting zero usage.
        if ($drive.QuotaTotalBytes -gt 0 -and $drive.QuotaAvailableBytes -le $drive.QuotaTotalBytes) {
            $diskBytes = [int64]($drive.QuotaTotalBytes - $drive.QuotaAvailableBytes)
        } else {
            $diskBytes = [int64]300000000001
        }
        $verified = $false
        $qualificationFile = Join-Path $Code 'policy-verified.json'
        if (Test-Path $qualificationFile) {
            $qualification = Get-Content -LiteralPath $qualificationFile -Raw | ConvertFrom-Json
            $verified = $qualification.worker_sid -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -and $qualification.cgroup_verified -eq $true -and $qualification.quota_verified -eq $true -and $qualification.separate_vm_verified -eq $true -and $qualification.gpu_enabled -eq $false
        }
        $sample = @{observed_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;cpu_count=$logical;memory_total_bytes=$memory.totalPhysical;memory_available_bytes=$memory.availablePhysical;commit_total_bytes=$memory.totalCommit;commit_available_bytes=$memory.availableCommit;worker_memory_bytes=$workerBytes;disk_total_bytes=$drive.TotalSize;disk_free_bytes=$drive.AvailableFreeSpace;worker_disk_used_bytes=$diskBytes;cpu_percent=[Math]::Max(0,[Math]::Min(100,$cpuPercent));on_ac=[AgcHost]::OnAC();policy_verified=$verified;gpu_qualified=$false;memory_accounting='all_wsl_private_commit_plus_watchdog'}
        $sample.quota_total_bytes=$drive.QuotaTotalBytes
        $sample.qualified_worker_sha256=if ($verified) {$qualification.worker_sha256} else {$null}
        $sample.quota_available_bytes=$drive.QuotaAvailableBytes
        $sample.disk_accounting=$drive.Source
        $payload = $sample | ConvertTo-Json -Compress
        $sampleResult = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/local/bin/agc-worker','host-sample') -TimeoutMilliseconds 20000 -StandardInput ($payload + [Environment]::NewLine)
        if ($sampleResult.Started -ne $true) {
            if ($sampleResult.Suppressed -eq $true) { $suppressed = $true; Write-AgcWorkerLog -Event 'worker_stop_observed' -Data @{reason='admission_refused'}; break }
            throw 'Host telemetry delivery failed'
        }
        if ($sampleResult.TimedOut -eq $true -or ($null -ne $sampleResult.ExitCode -and [int]$sampleResult.ExitCode -ne 0)) { throw 'Host telemetry delivery failed' }
        $staffReserve = [Math]::Max(8GB, $sample.memory_total_bytes * .25)
        if ($sample.memory_available_bytes -lt $staffReserve) {
            if ($null -eq $staffPressureSince) { $staffPressureSince = [DateTimeOffset]::UtcNow }
            elseif (([DateTimeOffset]::UtcNow - $staffPressureSince).TotalSeconds -ge 75) {
                # Supervisor already checkpointed/throttled, then stopped at60s;
                # worker-only VM termination now reclaims residual guest cache.
                Write-AgcWorkerLog -Event 'worker_stop_pressure' -Data @{reason='staff_memory'}
                break
            }
        } else { $staffPressureSince = $null }
        if (-not $sample.on_ac -or $sample.worker_memory_bytes -gt 16000000000 -or $sample.memory_available_bytes -lt 5000000000 -or $sample.disk_free_bytes -lt ($sample.disk_total_bytes * .15)) {
            # Linux supervisor receives the pressure sample and stops the job.
            # Keep the poll short so a coordinator stop is never delayed by a
            # long sleep outside the 25 s budget.
            Start-Sleep -Seconds 2
            break
        }
        if ($keepAlive -and $keepAlive.HasExited) {
            # Restart only while admission is open. The finite keeper avoids an
            # infinite orphan client holding the VM after a stop.
            $gate = Get-AgcWorkerGate
            if (-not $gate.Allowed) { $stopRequest = $gate.StopRequest; break }
            [void](Stop-AgcWorkerKeepAlive -Process $keepAlive -WaitMilliseconds 1000)
            $keepAlive = Start-AgcWorkerKeepAlive -Distro $Distro -Seconds 600
        }
        Start-Sleep -Seconds 2
    }
} finally {
    # Graceful phase: stop the supervisor first so it can checkpoint and
    # terminate only its exact job cgroup. Admission is already closed by the
    # gate/stop request; the scoped fallback never starts a stopped distro.
    $requestId = if ($stopRequest -and $stopRequest.RequestId) { [string]$stopRequest.RequestId } else { 'local-' + [Guid]::NewGuid().ToString('N') }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    # A coordinator stop with an exhausted deadline must remain unconfirmed;
    # only a local, non-coordinator drain gets the ordinary 21 s budget.
    $budget = if ($stopRequest) { Get-AgcWorkerRemainingMilliseconds -StopRequest $stopRequest } else { 21000 }
    if ($suppressed) {
        # A refused admitted launch must never fall back to a wsl -d path that
        # could start the distro. Only exact-distro termination/verification.
        [void](Invoke-AgcWorkerScopedTerminate -RequestId $requestId -ExpectedSid $sid -StopRequest $stopRequest `
            -Reason 'admission_refused' -TotalBudgetMilliseconds $budget -Distro $Distro -DataPath $Data -Phase 'terminate')
    } else {
        [void](Invoke-AgcWorkerGracefulDrain -RequestId $requestId -ExpectedSid $sid -StopRequest $stopRequest `
            -Reason $(if ($stopRequest) { 'coordinator_stop' } else { 'worker_drain' }) `
            -TotalBudgetMilliseconds $budget -Distro $Distro -DataPath $Data -KeepAlive $keepAlive)
    }
}

if ($suppressed) { exit 0 }
