# Persistent worker owner stop entrypoint installed at
# C:\ProgramData\AGCLifecycle\owners\worker-stop.ps1 and invoked only by the
# fixed AGC-Worker-LifecycleStop task as the agc-worker account.
#
# Cycle-capable owner helper (issue #210). Legacy stop/terminate/verify remain
# supported for mixed-version rollout.
# AGC_OWNER_CYCLE_PROTOCOL=1
#
# Phases come from the admin-owned request file written by the coordinator.
# The helper must never force-terminate before the explicit stop phase has been
# attempted; an unknown/missing phase defaults to Verify.
[CmdletBinding()]
param([switch]$FromTask)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Data = 'C:\ProgramData\AGC-Worker\data'
$RequestFile = 'C:\ProgramData\AGCLifecycle\requests\worker\current.json'
$Distro = 'AGC-Worker'
. 'C:\ProgramData\AGC-Worker\code\WorkerLifecycle.ps1'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$account = $identity.Name

function Write-Unconfirmed {
    param([string]$RequestId, [string]$BootId, [string]$Phase, [string]$Reason)
    $now = [DateTimeOffset]::UtcNow
    $receipt = @{schema=1;owner='worker';requestId=$RequestId;bootId=$BootId;phase=$Phase;outcome='unconfirmed';
        stopped=$false;verified=$false;scope='distro';source='owner';
        startedUtc=$now.ToString('o');finishedUtc=$now.ToString('o');durationMs=0;
        accountName=$account;accountSid=$sid;distro=$Distro;
        details=@{phase=$Phase;reason=$Reason}}
    if (Get-Command Write-AgcWorkerStopReceipt -ErrorAction SilentlyContinue) {
        $receipt.shared_receipt_written = Write-AgcWorkerStopReceipt -Data $receipt -RequestId $(if($RequestId){$RequestId}else{'unknown'}) -Outcome 'unconfirmed' -Phase $Phase
    }
    [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $Data -RequestId $(if($RequestId){$RequestId}else{'unknown'}) -Phase $Phase)
    exit 1
}

if (-not $FromTask) { Write-Unconfirmed '' '' 'verify' 'from_task_required' }
# The coordinator is SYSTEM. It may start the fixed task, but it may never run
# this helper directly or terminate the owner distro as SYSTEM.
if (-not $account -or $account -match '\\SYSTEM$' -or $sid -eq 'S-1-5-18') { Write-Unconfirmed '' '' 'verify' 'owner_token_required' }
if ($account -notmatch '\\agc-worker$') { Write-Unconfirmed '' '' 'verify' 'owner_identity_mismatch' }
if (-not (Initialize-AgcWorkerLifecycle)) { Write-Unconfirmed '' '' 'verify' 'lifecycle_unavailable' }

# Bounded admin-owned request read. Reparse points, oversized files and
# non-regular files are rejected before any WSL command.
try {
    $item = Get-Item -LiteralPath $RequestFile -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 65536) {
        Write-Unconfirmed '' '' 'verify' 'request_file_invalid'
    }
    $request = Get-Content -LiteralPath $RequestFile -Raw -ErrorAction Stop | ConvertFrom-Json
    $requestHash = (Get-FileHash -LiteralPath $RequestFile -Algorithm SHA256).Hash.ToLowerInvariant()
} catch { Write-Unconfirmed '' '' 'verify' 'request_file_unreadable' }
if ($request.schema -ne 1 -or $request.owner -ne 'worker') { Write-Unconfirmed ([string]$request.requestId) ([string]$request.bootId) 'verify' 'request_binding_invalid' }
$requestId = [string]$request.requestId
$bootId = [string]$request.bootId
if ($requestId -notmatch '^[a-f0-9]{32}$') { Write-Unconfirmed $requestId $bootId 'verify' 'request_id_invalid' }
# Phase is parsed before any deadline refusal so a legacy/cycle decision never
# depends on the current time.
$phase = ([string]$request.phase).ToLowerInvariant()
if ($phase -eq 'graceful') { $phase = 'stop' }
if ($phase -notin @('stop','terminate','verify','cycle')) { $phase = 'verify' }

if ($phase -eq 'cycle') {
    # Exact owner SID binding comes from the admin-owned lifecycle configuration
    # (Users RX), which the native service also validates. The worker control
    # directory is admin-only because it holds the DPAPI credential; it is never
    # read on the cycle path.
    $binding = Test-AgcWorkerOwnerSidBinding -ConfigPath (Get-AgcWorkerLifecycleConfigPath) `
        -ExpectedSid $sid -ExpectedDistro $Distro
    if (-not $binding.Bound) { Write-Unconfirmed $requestId $bootId 'cycle' $binding.Reason }
    $validated = Get-AgcWorkerCycleRequest -Request $request
    if (-not $validated.Valid) { Write-Unconfirmed $requestId $bootId 'cycle' $validated.Reason }
    if ($validated.Deadline -le [DateTimeOffset]::UtcNow) { Write-Unconfirmed $requestId $bootId 'cycle' 'cycle_deadline_exhausted' }
    $gateCheck = {
        $current = Get-AgcStopRequest
        return ($null -ne $current -and [string]$current.RequestId -eq $requestId -and [string]$current.BootId -eq $bootId)
    }.GetNewClosure()
    $requestCheck = {
        try {
            $currentHash = (Get-FileHash -LiteralPath $RequestFile -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($currentHash -ne $requestHash) { return $false }
            $currentRequest = Get-Content -LiteralPath $RequestFile -Raw -ErrorAction Stop | ConvertFrom-Json
            return ([string]$currentRequest.requestId -eq $requestId -and [string]$currentRequest.bootId -eq $bootId -and
                [string]$currentRequest.phase -eq 'cycle')
        } catch { return $false }
    }.GetNewClosure()
    # Primary executor publishes the shared receipt while it holds the per-cycle
    # lock (flat data only: the shared library stringifies nested values).
    $onComplete = {
        param($cycleReceipt)
        return (Write-AgcWorkerStopReceipt -Data $cycleReceipt -RequestId $requestId -Outcome $cycleReceipt.outcome -Phase 'cycle')
    }.GetNewClosure()
    $result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash $requestHash -WslPath 'wsl.exe' `
        -Distro $Distro -DataPath $Data -GateCheck $gateCheck -RequestCheck $requestCheck -OnComplete $onComplete
    if ($result.duplicate -eq $true -or $result.reused -eq $true) {
        Write-AgcWorkerLog -Event 'worker_lifecycle_cycle_duplicate' -Data @{request_id=$requestId;reason=$result.reason;outcome=$result.outcome;reused=[string]$result.reused}
        if ($result.reused -eq $true) {
            # Repair a crash-gap shared receipt from the proven local evidence.
            # It carries the same graceful/forced outcome, never a refusal.
            try {
                $repaired = Write-AgcWorkerStopReceipt -Data $result.receipt -RequestId $requestId -Outcome $result.receipt.outcome -Phase 'cycle'
            } catch { $repaired = $false }
            Write-AgcWorkerLog -Event 'worker_lifecycle_cycle_reused' -Data @{request_id=$requestId;outcome=$result.outcome;shared_repair=[string]$repaired}
            exit 0
        }
        exit 1
    }
    Write-AgcWorkerLog -Event 'worker_lifecycle_owner_cycle' -Data @{request_id=$requestId;outcome=$result.outcome;stopped=[string]$result.stopped;verified=[string]$result.verified;duration_ms=$result.receipt.durationMs;launch_latency_ms=$result.receipt.details.launchLatencyMs}
    if ($result.verified -eq $true) { exit 0 }
    exit 1
}

try {
    $deadline = [DateTimeOffset]::Parse([string]$request.deadlineUtc, [Globalization.CultureInfo]::InvariantCulture)
} catch { Write-Unconfirmed $requestId $bootId $phase 'deadline_invalid' }
$now = [DateTimeOffset]::UtcNow
if ($deadline -lt $now -or ($deadline - $now).TotalSeconds -gt 120) { Write-Unconfirmed $requestId $bootId $phase 'deadline_stale' }
# The registry gate and request file must agree. A coordinator-side mismatch is
# never proof that this worker should stop.
$gate = Get-AgcStopRequest
if ($null -eq $gate -or [string]$gate.RequestId -ne $requestId -or [string]$gate.BootId -ne $bootId) {
    Write-Unconfirmed $requestId $bootId $phase 'gate_request_mismatch'
}

# One monotonic budget for the whole phase. No operation may extend the
# coordinator deadline with a floor or a fresh calculation.
$phaseCap = switch ($phase) { 'stop' { 12000 } 'terminate' { 8000 } default { 3000 } }
$budget = [int64]($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds
if ($budget -gt $phaseCap) { $budget = $phaseCap }
$clock = [Diagnostics.Stopwatch]::StartNew()
function Remaining([int]$Cap) {
    $value = [int64]$budget - $clock.ElapsedMilliseconds
    if ($value -lt 0) { $value = 0 }
    if ($value -gt $Cap) { $value = $Cap }
    return [int]$value
}

$started = [DateTimeOffset]::UtcNow
$receipt = @{schema=1;owner='worker';requestId=$requestId;bootId=$bootId;phase=$phase;outcome='unconfirmed';
    stopped=$false;verified=$false;scope='distro';source='owner';
    startedUtc=$started.ToString('o');accountName=$account;accountSid=$sid;distro=$Distro;
    details=@{phase=$phase;deadlineUtc=$deadline.ToString('o')}}
try {
    if ((Remaining 1) -le 0) { throw 'lifecycle_deadline_exhausted' }
    $running = @(Get-AgcWorkerRunningDistros -WslPath 'wsl.exe' -TimeoutMilliseconds (Remaining 3000))
    if ($phase -eq 'stop') {
        if ($running -contains $Distro) {
            # Owner-account stop phase: the guest supervisor checkpoints and
            # terminates only its exact job cgroup. It does not terminate the VM.
            $stop = Invoke-AgcWorkerWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/systemctl','stop','agc-worker.service') `
                -TimeoutMilliseconds (Remaining 10000) -WslPath 'wsl.exe'
            if ($stop.TimedOut -eq $true) { $receipt.details.stopTimedOut = $true }
            if ($null -ne $stop.ExitCode) { $receipt.details.stopExitCode = [int]$stop.ExitCode }
        }
        $receipt.details.stopAttempted = $true
        $receipt.outcome = 'unconfirmed'
    } elseif ($phase -eq 'terminate') {
        if ($running -contains $Distro) {
            $term = Invoke-AgcWorkerWsl -Arguments @('--terminate',$Distro) -TimeoutMilliseconds (Remaining 8000) -WslPath 'wsl.exe'
            if ($term.TimedOut -eq $true) { $receipt.details.terminateTimedOut = $true }
        }
        $after = @(Get-AgcWorkerRunningDistros -WslPath 'wsl.exe' -TimeoutMilliseconds (Remaining 3000))
        if ($after -notcontains $Distro) {
            $receipt.stopped = $true; $receipt.verified = $true; $receipt.outcome = 'forced'
        }
    } else {
        $after = @(Get-AgcWorkerRunningDistros -WslPath 'wsl.exe' -TimeoutMilliseconds (Remaining 3000))
        if ($after -notcontains $Distro) {
            $receipt.stopped = $true; $receipt.verified = $true; $receipt.outcome = 'graceful'
        }
    }
} catch {
    $receipt.details.errorType = $_.Exception.GetType().Name
}
$finished = [DateTimeOffset]::UtcNow
$receipt.finishedUtc = $finished.ToString('o')
$receipt.durationMs = [int]($finished - $started).TotalMilliseconds
$receipt.shared_receipt_written = Write-AgcWorkerStopReceipt -Data $receipt -RequestId $requestId -Outcome $receipt.outcome -Phase $phase
[void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $Data -RequestId $requestId -Phase $phase)
Write-AgcWorkerLog -Event 'worker_lifecycle_stop_phase' -Data @{request_id=$requestId;phase=$phase;outcome=$receipt.outcome;duration_ms=$receipt.durationMs}
if ($phase -eq 'stop') { exit 0 }
if ($receipt.stopped -ne $true) { exit 1 }
exit 0
