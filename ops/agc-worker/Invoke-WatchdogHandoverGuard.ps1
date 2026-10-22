# Only the dedicated hidden limited-account task invokes this fixed helper.
[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$Transaction)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=[Text.UTF8Encoding]::new($false)
$Code='C:\ProgramData\AGC-Worker\code\handover'
$Data='C:\ProgramData\AGC-Worker\data'
$plan=Get-Content -LiteralPath (Join-Path $Code "$Transaction.json") -Raw | ConvertFrom-Json
$receipt=Join-Path $Data "handover-$Transaction.json"
$self=[Security.Principal.WindowsIdentity]::GetCurrent()
if($self.User.Value -ne $plan.worker_sid -or $self.Name -notmatch '\\agc-worker$'){throw 'Wrong guard identity'}
$deadline=[DateTimeOffset]::FromUnixTimeSeconds([long]$plan.deadline)
$now=[DateTimeOffset]::UtcNow
if(($deadline-$now).TotalSeconds -lt 480 -or ($deadline-$now).TotalSeconds -gt 600){throw 'Guard deadline invalid'}
$guest=Join-Path $Code 'watchdog_handover_guard.py'
if((Get-FileHash -LiteralPath $guest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $plan.guest_sha256){throw 'Guard source mismatch'}
if((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $plan.guard_sha256){throw 'Guard launcher mismatch'}
$lifecycleFile=Join-Path $PSScriptRoot 'WorkerLifecycle.ps1'
if(-not (Test-Path -LiteralPath $lifecycleFile)){throw 'lifecycle_library_unavailable'}
if((Get-FileHash -LiteralPath $lifecycleFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$plan.lifecycle_sha256){throw 'Worker lifecycle adapter mismatch'}
. $lifecycleFile -DataPath $Data
if(-not (Initialize-AgcWorkerLifecycle)){throw 'lifecycle_library_unavailable'}
# The old pinned watchdog is still alive during this one connection establishment.
# This list is not a promise against disappearance: same-boot handshake must pass
# BEFORE the operator may kill it. Once connected, never invoke WSL again.
$process=$null
$result=@{version=1;transaction=$Transaction;guard_pid=$PID;worker_sid=$self.User.Value;deadline=$plan.deadline;state='starting'}
function Save-Guard {
    $result.observed_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($result | ConvertTo-Json -Depth 8 -Compress))
    [IO.File]::WriteAllBytes("$receipt.tmp",$bytes)
    if([IO.File]::Exists($receipt)){[IO.File]::Replace("$receipt.tmp",$receipt,[System.Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move("$receipt.tmp",$receipt)}
}
try {
    Save-Guard
    $gate=Get-AgcWorkerGate
    if (-not $gate.Allowed) { throw 'lifecycle_gate_closed' }
    $running=@(Get-AgcWorkerRunningDistros -WslPath "$env:SystemRoot\System32\wsl.exe" -TimeoutMilliseconds 5000)
    if($running -notcontains 'AGC-Worker'){throw 'Existing worker distro is unavailable'}
    Assert-AgcWorkerGateOpen | Out-Null
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName="$env:SystemRoot\System32\wsl.exe"
    $info.Arguments="-d AGC-Worker -u root --exec /usr/bin/python3 -I -u - $($plan.worker_boot_id) $($plan.deadline)"
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=Start-AgcWorkerAdmittedProcessInfo -StartInfo $info -TimeoutMilliseconds 2000
    if($null -eq $process){throw 'Guard keeper admission refused'}
    $result.keeper_pid=$process.Id
    # Recheck after creation; if the gate closed in the narrow window, kill
    # only this just-created exact process before writing any guest input.
    if(-not (Get-AgcWorkerGate).Allowed){$process.Kill(); throw 'lifecycle_gate_closed'}
    $process.StandardInput.Write([IO.File]::ReadAllText($guest))
    $process.StandardInput.Close()
    $errors=$process.StandardError.ReadToEndAsync()
    $line=$process.StandardOutput.ReadLineAsync()
    while([DateTimeOffset]::UtcNow -lt $deadline) {
        if($line.Wait(1000)){
            if($null -eq $line.Result){throw 'Existing guest guard exited; reconnection is forbidden'}
            if($line.Result.Length -gt 16384){throw 'Guard frame too large'}
            $sample=$line.Result | ConvertFrom-Json
            $result.guest=$sample; $result.state=$sample.state; Save-Guard
            if($sample.state -ne 'guarding'){break}
            $line=$process.StandardOutput.ReadLineAsync()
        }
        if($process.HasExited){throw 'Guest keeper exited'}
    }
    if($result.state -eq 'guarding'){$result.state='expired'; Save-Guard}
    # Python independently has the same wall/monotonic deadline. No VM shutdown
    # command, service stop, reconnect or old watchdog cleanup appears here.
    if(-not $process.WaitForExit(5000)){throw 'Guard exit is unconfirmed'}
} catch {
    $result.state='failed'; $result.error_type=$_.Exception.GetType().Name; Save-Guard
    exit 1
} finally {
    if($process){$process.Dispose()}
}
