# Runs in a hidden, noninteractive task under agc-worker, never staff context.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong worker account' }
$Base = 'C:\ProgramData\AGC-Worker'
$Data = Join-Path $Base 'data'
$Code = Join-Path $Base 'code'
$Distro = 'AGC-Worker'
. (Join-Path $PSScriptRoot 'Host-Disk.ps1')
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
if (-not (In-Window) -or -not [AgcHost]::OnAC()) { exit 0 }
$initialMemory = [AgcHost]::ReadMemory()
$initialDisk = Get-WorkerDiskSpace
if ($initialMemory.availablePhysical -lt ([Math]::Max(8GB,$initialMemory.totalPhysical * .25) + 1000000000) -or $initialDisk.AvailableFreeSpace -lt ($initialDisk.TotalSize * .15)) { exit 0 }
if (-not (Test-Path (Join-Path $Data 'distro-ready.json'))) { throw 'Independent distro was not initialized' }
$self = Get-Process -Id $PID
$self.PriorityClass = 'BelowNormal'
$keepAlive = $null
$previousIdle = [uint64]0; $previousKernel = [uint64]0; $previousUser = [uint64]0
[void][AgcHost]::GetSystemTimes([ref]$previousIdle,[ref]$previousKernel,[ref]$previousUser)
$logical = [Environment]::ProcessorCount
$staffPressureSince = $null
try {
    $keepAlive = Start-Process -FilePath 'wsl.exe' -ArgumentList '-d AGC-Worker -u root --exec /usr/bin/sleep infinity' -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 5
    & wsl.exe -d $Distro -u root --exec /usr/bin/systemctl start agc-worker.service
    if ($LASTEXITCODE -ne 0) { throw 'Worker supervisor unavailable' }
    while (In-Window) {
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
        $diskBytes = [int64]((Get-ChildItem -LiteralPath $Data -Recurse -File -Force | Measure-Object Length -Sum).Sum)
        $verified = $false
        $qualificationFile = Join-Path $Code 'policy-verified.json'
        if (Test-Path $qualificationFile) {
            $qualification = Get-Content -LiteralPath $qualificationFile -Raw | ConvertFrom-Json
            $verified = $qualification.worker_sid -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -and $qualification.cgroup_verified -eq $true -and $qualification.quota_verified -eq $true -and $qualification.separate_vm_verified -eq $true -and $qualification.gpu_enabled -eq $false
        }
        $sample = @{observed_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;cpu_count=$logical;memory_total_bytes=$memory.totalPhysical;memory_available_bytes=$memory.availablePhysical;commit_total_bytes=$memory.totalCommit;commit_available_bytes=$memory.availableCommit;worker_memory_bytes=$workerBytes;disk_total_bytes=$drive.TotalSize;disk_free_bytes=$drive.AvailableFreeSpace;worker_disk_used_bytes=$diskBytes;cpu_percent=[Math]::Max(0,[Math]::Min(100,$cpuPercent));on_ac=[AgcHost]::OnAC();policy_verified=$verified;gpu_qualified=$false;memory_accounting='all_wsl_private_commit_plus_watchdog'}
        $sample.quota_total_bytes=$drive.QuotaTotalBytes
        $sample.quota_available_bytes=$drive.QuotaAvailableBytes
        $sample.disk_accounting=$drive.Source
        $sample | ConvertTo-Json -Compress | & wsl.exe -d $Distro -u root --exec /usr/local/bin/agc-worker host-sample
        if ($LASTEXITCODE -ne 0) { throw 'Host telemetry delivery failed' }
        $staffReserve = [Math]::Max(8GB, $sample.memory_total_bytes * .25)
        if ($sample.memory_available_bytes -lt $staffReserve) {
            if ($null -eq $staffPressureSince) { $staffPressureSince = [DateTimeOffset]::UtcNow }
            elseif (([DateTimeOffset]::UtcNow - $staffPressureSince).TotalSeconds -ge 75) {
                # Supervisor already checkpointed/throttled, then stopped at60s;
                # worker-only VM termination now reclaims residual guest cache.
                break
            }
        } else { $staffPressureSince = $null }
        if (-not $sample.on_ac -or $sample.worker_memory_bytes -gt 16000000000 -or $sample.memory_available_bytes -lt 5000000000 -or $sample.disk_free_bytes -lt ($sample.disk_total_bytes * .15)) {
            # Linux supervisor receives the pressure sample and stops the job.
            Start-Sleep -Seconds 15
            break
        }
        if ($keepAlive.HasExited) { throw 'Worker WSL keepalive exited' }
        Start-Sleep -Seconds 5
    }
} finally {
    # Terminate only this account's AGC-Worker VM after the cgroup stop grace;
    # never wsl --shutdown, never another user's distribution or power schedule.
    if ($keepAlive) {
        & wsl.exe -d $Distro -u root --exec /usr/bin/systemctl stop agc-worker-jobs.slice
        & wsl.exe -d $Distro -u root --exec /usr/bin/systemctl stop agc-worker.service
        & wsl.exe --terminate $Distro
        if (-not $keepAlive.HasExited) { Stop-Process -Id $keepAlive.Id -Force -ErrorAction SilentlyContinue }
    }
}
