# Worker-side adapter for the protected AGCLifecycle common library.
#
# The Administrator/SYSTEM-owned library is installed at a fixed path by the
# Windows lifecycle installer. This adapter only binds its public contract to
# worker policy: admission, bounded commands, nonthrowing owner logs and owner
# receipts. It never clears a shared gate and never implements a second gate.
[CmdletBinding()]
param(
    [string]$LibraryPath = 'C:\ProgramData\AGCLifecycle\Lifecycle.ps1',
    [string]$DataPath = 'C:\ProgramData\AGC-Worker\data'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:AgcWorkerLifecyclePath = $LibraryPath
$script:AgcWorkerDataPath = $DataPath
$script:AgcWorkerOwner = 'worker'
$script:AgcWorkerLoaded = $false
# 25 s coordinator total: 12 s parallel graceful owners, 8 s scoped fallback,
# 3 s independent verification. Owners never wait past their remaining budget.
$script:AgcWorkerTotalBudgetMilliseconds = 25000
$script:AgcWorkerGracefulMilliseconds = 12000
$script:AgcWorkerFallbackMilliseconds = 8000
$script:AgcWorkerVerifyMilliseconds = 3000
# Load at script scope so functions remain available after initialization.
if (Test-Path -LiteralPath $script:AgcWorkerLifecyclePath -PathType Leaf) {
    try { . $script:AgcWorkerLifecyclePath } catch { }
    # The real library sets StrictMode 2.0 in this scope; worker scripts use
    # explicit null guards and must not inherit property-throwing behaviour.
    Set-StrictMode -Off
}

function Get-AgcWorkerIdentity {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        return [pscustomobject]@{Name=$identity.Name;Sid=$identity.User.Value}
    } catch {
        # Pure-logic hosts have no Windows principal. Production callers always
        # take the Windows branch; receipts then carry the exact SID.
        return [pscustomobject]@{Name=[Environment]::UserName;Sid=''}
    }
}

function Initialize-AgcWorkerLifecycle {
    [CmdletBinding()]
    param([string]$Path = $script:AgcWorkerLifecyclePath, [switch]$Optional)
    $script:AgcWorkerLoaded = $false
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        if (-not $Optional) { return $false }
        return $false
    }
    try {
        if ($Path -ne $script:AgcWorkerLifecyclePath) { . $Path }
        foreach ($name in @('Test-AgcStartAllowed','Get-AgcStopRequest','Invoke-AgcBoundedProcess',
                            'Write-AgcLifecycleLog','Write-AgcStopReceipt')) {
            if (-not (Get-Command $name -CommandType Function -ErrorAction SilentlyContinue)) {
                return $false
            }
        }
        $script:AgcWorkerLifecyclePath = $Path
        $script:AgcWorkerLoaded = $true
        return $true
    } catch {
        $script:AgcWorkerLoaded = $false
        return $false
    }
}

function Get-AgcWorkerValue {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function Write-AgcWorkerLog {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Event, [hashtable]$Data = @{})
    if (-not $script:AgcWorkerLoaded) { return }
    try {
        Write-AgcLifecycleLog -Stream 'worker' -Event $Event -Data $Data
    } catch { }
}

function Get-AgcWorkerGate {
    [CmdletBinding()]
    param()
    $result = [pscustomobject]@{
        Allowed = $false; StopRequest = $null; Reason = 'gate_unavailable'; Error = $null
        RemainingMilliseconds = 0; ObservedAt = [DateTimeOffset]::UtcNow
    }
    if (-not $script:AgcWorkerLoaded) { return $result }
    try {
        $stop = Get-AgcStopRequest -Owner $script:AgcWorkerOwner
        $result.StopRequest = $stop
        if ($null -ne $stop) {
            $result.Reason = 'stop_requested'
            $result.RemainingMilliseconds = Get-AgcWorkerRemainingMilliseconds -StopRequest $stop
            return $result
        }
        $allowed = Test-AgcStartAllowed -Owner $script:AgcWorkerOwner
        if ($allowed -eq $true) {
            $result.Allowed = $true
            $result.Reason = 'ready'
        } else {
            $result.Reason = 'gate_closed'
        }
    } catch {
        # Lost coordinator or malformed gate is never admission evidence.
        $result.Allowed = $false
        $result.Reason = 'gate_error'
        $result.Error = $_.Exception.GetType().Name
    }
    return $result
}

function Get-AgcWorkerRemainingMilliseconds {
    [CmdletBinding()]
    param($StopRequest, [int]$CapMilliseconds = $script:AgcWorkerTotalBudgetMilliseconds)
    if ($null -eq $StopRequest -or -not $StopRequest.DeadlineUtc) { return $CapMilliseconds }
    try {
        $deadline = [DateTimeOffset]::Parse([string]$StopRequest.DeadlineUtc, [Globalization.CultureInfo]::InvariantCulture)
    } catch { return 0 }
    $remaining = [int64]($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds
    if ($remaining -lt 0) { return 0 }
    if ($remaining -gt $CapMilliseconds) { return $CapMilliseconds }
    return [int]$remaining
}

function Invoke-AgcWorkerBoundedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory)][int]$TimeoutMilliseconds,
        [string]$StandardInput,
        [int]$MaxOutputBytes = 65536
    )
    if (-not $script:AgcWorkerLoaded) { throw 'lifecycle_library_unavailable' }
    if ($TimeoutMilliseconds -le 0) { throw 'lifecycle_deadline_exhausted' }
    return Invoke-AgcBoundedProcess -FilePath $FilePath -ArgumentList $ArgumentList `
        -TimeoutMilliseconds $TimeoutMilliseconds -StandardInput $StandardInput -MaxOutputBytes $MaxOutputBytes
}


function Test-AgcWorkerAdmissionApi {
    [CmdletBinding()]
    param()
    if (-not $script:AgcWorkerLoaded) { return $false }
    if (-not (Get-Command Start-AgcAdmittedProcess -CommandType Function -ErrorAction SilentlyContinue)) { return $false }
    $bounded = Get-Command Invoke-AgcBoundedProcess -CommandType Function -ErrorAction SilentlyContinue
    if ($null -eq $bounded) { return $false }
    # Windows lifecycle adds the bounded admission overload. Ordinary worker
    # WSL commands must not fall back to a check-then-start race.
    return ($bounded.Parameters.ContainsKey('RequireAdmission') -and $bounded.Parameters.ContainsKey('Owner'))
}

function Invoke-AgcWorkerAdmittedWsl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][int]$TimeoutMilliseconds,
        [string]$StandardInput,
        [int]$MaxOutputBytes = 1048576,
        [string]$WslPath = 'wsl.exe'
    )
    if (-not (Test-AgcWorkerAdmissionApi)) { throw 'atomic_admission_unavailable' }
    if ($TimeoutMilliseconds -le 0) { throw 'lifecycle_deadline_exhausted' }
    $result = Invoke-AgcBoundedProcess -FilePath $WslPath -ArgumentList $Arguments `
        -TimeoutMilliseconds $TimeoutMilliseconds -StandardInput $StandardInput `
        -MaxOutputBytes $MaxOutputBytes -RequireAdmission -Owner $script:AgcWorkerOwner
    if ($null -eq $result) { throw 'admission_result_missing' }
    if ($result.Started -ne $true) {
        if ($result.Suppressed -eq $true) { return $result }  # refused, no process
        throw 'admission_refused'
    }
    return $result
}

function Invoke-AgcWorkerWsl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][int]$TimeoutMilliseconds,
        [string]$StandardInput,
        [string]$WslPath = 'wsl.exe'
    )
    if ($TimeoutMilliseconds -le 0) { throw 'lifecycle_deadline_exhausted' }
    $result = Invoke-AgcWorkerBoundedProcess -FilePath $WslPath -ArgumentList $Arguments `
        -TimeoutMilliseconds $TimeoutMilliseconds -StandardInput $StandardInput
    if ($result.Started -ne $true) { throw 'wsl_not_started' }
    return $result
}

function Get-AgcWorkerRunningDistros {
    [CmdletBinding()]
    param([string]$WslPath = 'wsl.exe', [int]$TimeoutMilliseconds = 3000)
    if ($TimeoutMilliseconds -le 0) { throw 'lifecycle_deadline_exhausted' }
    $result = Invoke-AgcWorkerWsl -Arguments @('--list','--running','--quiet') `
        -TimeoutMilliseconds $TimeoutMilliseconds -WslPath $WslPath
    if ($result.Started -ne $true) { throw 'wsl_not_started' }
    if ($result.TimedOut -eq $true) { throw 'wsl_list_timeout' }
    if ($result.OutputTruncated -eq $true) { throw 'wsl_list_truncated' }
    if ($null -eq $result.ExitCode -or [int]$result.ExitCode -ne 0) { throw 'wsl_list_failed' }
    $text = [string]$result.Stdout
    return @(($text -replace "`0",'') -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Stop-AgcWorkerKeepAlive {
    [CmdletBinding()]
    param($Process, [int]$WaitMilliseconds = 3000)
    if ($null -eq $Process) { return $true }
    try {
        if (-not $Process.HasExited) { $Process.Kill() }
        $exited = $Process.WaitForExit($WaitMilliseconds)
        $Process.Dispose()
        if ($exited) { return $true }
        return $false
    } catch { return $true }
}

function Start-AgcWorkerKeepAlive {
    [CmdletBinding()]
    param(
        [string]$WslPath = 'wsl.exe',
        [string]$Distro = 'AGC-Worker',
        [int]$Seconds = 600
    )
    # A bounded finite keepalive is restarted by the watchdog loop. Its native
    # handle is owned so shutdown can kill it without a process-name sweep.
    # Start-AgcAdmittedProcess holds the shared launch mutex and re-checks the
    # gate under it; it returns $null when admission is refused.
    if (-not (Test-AgcWorkerAdmissionApi)) { throw 'atomic_admission_unavailable' }
    return Start-AgcAdmittedProcess -FilePath $WslPath `
        -ArgumentList @('-d',$Distro,'-u','root','--exec','/usr/bin/sleep',[string]$Seconds) `
        -Owner $script:AgcWorkerOwner -PassThru
}

function Get-AgcWorkerCycleRequest {
    <#
      Validate the immutable per-cycle request (schema 1, phase=cycle).
      Windows coordinator deadlines are absolute and capped at +13 s graceful,
      +21 s terminate and +24 s final (native 25 s / SCM 30 s).
    #>
    [CmdletBinding()]
    param($Request)
    $result = [pscustomobject]@{Valid=$false;Reason='request_binding_invalid';RequestId='';BootId='';
        Issued=$null;GracefulDeadline=$null;TerminateDeadline=$null;Deadline=$null;
        CoordinatorProcessId=0;ReasonText=''}
    if ($null -eq $Request) { return $result }
    $result.RequestId = [string](Get-AgcWorkerValue $Request 'requestId' '')
    $result.BootId = [string](Get-AgcWorkerValue $Request 'bootId' '')
    if ((Get-AgcWorkerValue $Request 'schema' 0) -ne 1 -or (Get-AgcWorkerValue $Request 'owner' '') -ne 'worker' -or
        (Get-AgcWorkerValue $Request 'phase' '') -ne 'cycle') { return $result }
    if ($result.RequestId -notmatch '^[a-f0-9]{32}$') { $result.Reason = 'request_id_invalid'; return $result }
    if ([string]::IsNullOrWhiteSpace($result.BootId)) { return $result }
    $times = @{}
    foreach ($name in @('issuedUtc','gracefulDeadlineUtc','terminateDeadlineUtc','deadlineUtc')) {
        try {
            $times[$name] = [DateTimeOffset]::Parse([string](Get-AgcWorkerValue $Request $name ''),
                [Globalization.CultureInfo]::InvariantCulture)
        } catch { $result.Reason = 'deadline_invalid'; return $result }
    }
    $result.Issued = $times['issuedUtc']
    $result.GracefulDeadline = $times['gracefulDeadlineUtc']
    $result.TerminateDeadline = $times['terminateDeadlineUtc']
    $result.Deadline = $times['deadlineUtc']
    $result.CoordinatorProcessId = [int](Get-AgcWorkerValue $Request 'coordinatorProcessId' 0)
    $result.ReasonText = [string](Get-AgcWorkerValue $Request 'reason' '')
    $graceful = ($result.GracefulDeadline - $result.Issued).TotalMilliseconds
    if ($graceful -le 0 -or $graceful -gt 13000) { $result.Reason = 'graceful_deadline_out_of_bounds'; return $result }
    if (($result.TerminateDeadline - $result.Issued).TotalMilliseconds -gt 21000) { $result.Reason = 'terminate_deadline_out_of_bounds'; return $result }
    if (($result.Deadline - $result.Issued).TotalMilliseconds -gt 24000) { $result.Reason = 'deadline_out_of_bounds'; return $result }
    if ($result.GracefulDeadline -gt $result.TerminateDeadline -or $result.TerminateDeadline -ge $result.Deadline) {
        $result.Reason = 'deadline_order_invalid'; return $result
    }
    $result.Valid = $true
    $result.Reason = 'ok'
    return $result
}

function Get-AgcWorkerCycleReceipt {
    # A completed cycle must never be re-run or overwritten by a queued duplicate.
    [CmdletBinding()]
    param([string]$DataPath = $script:AgcWorkerDataPath, [string]$RequestId, [string]$BootId, [string]$Distro = 'AGC-Worker')
    if ($RequestId -notmatch '^[a-f0-9]{32}$') { return $null }
    $path = Join-Path $DataPath ($RequestId + '.cycle.json')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try { $receipt = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json } catch { return $null }
    if ((Get-AgcWorkerValue $receipt 'schema' 0) -ne 1 -or (Get-AgcWorkerValue $receipt 'owner' '') -ne 'worker' -or
        (Get-AgcWorkerValue $receipt 'phase' '') -ne 'cycle' -or
        [string](Get-AgcWorkerValue $receipt 'requestId' '') -ne $RequestId -or
        [string](Get-AgcWorkerValue $receipt 'bootId' '') -ne $BootId -or
        [string](Get-AgcWorkerValue $receipt 'distro' '') -ne $Distro -or
        (Get-AgcWorkerValue $receipt 'stopped' $false) -ne $true -or (Get-AgcWorkerValue $receipt 'verified' $false) -ne $true -or
        [string](Get-AgcWorkerValue $receipt 'outcome' '') -notin @('graceful','forced') -or
        [string](Get-AgcWorkerValue (Get-AgcWorkerValue $receipt 'details' $null) 'verification' '') -ne 'running-list' -or
        [string](Get-AgcWorkerValue (Get-AgcWorkerValue $receipt 'details' $null) 'verifyComplete' '') -ne 'true') { return $null }
    # Normalize to the same hashtable shape the fresh executor returns so a
    # queued caller can hand it straight back to the receipt writer.
    $source = Get-AgcWorkerValue $receipt 'details' $null
    $details = @{}
    foreach ($key in @('verification','verifyComplete','reason','requestSha256','drained','earlyTerminate',
                       'verificationPhase','launchLatencyMs','issuedUtc','gracefulDeadlineUtc',
                       'terminateDeadlineUtc','deadlineUtc','coordinatorProcessId','reasonText','initialRunning')) {
        $details[$key] = Get-AgcWorkerValue $source $key ''
    }
    return @{schema=1;owner='worker';requestId=$RequestId;bootId=$BootId;phase='cycle';
        outcome=[string](Get-AgcWorkerValue $receipt 'outcome' 'unconfirmed');stopped=$true;verified=$true;
        scope='distro';source='owner';
        startedUtc=[string](Get-AgcWorkerValue $receipt 'startedUtc' '');
        finishedUtc=[string](Get-AgcWorkerValue $receipt 'finishedUtc' '');
        durationMs=[int](Get-AgcWorkerValue $receipt 'durationMs' 0);
        accountName=[string](Get-AgcWorkerValue $receipt 'accountName' '');
        accountSid=[string](Get-AgcWorkerValue $receipt 'accountSid' '');
        distro=$Distro;details=$details}
}

function Get-AgcWorkerCycleRemaining {
    # Absolute remaining time: the wall clock is bounded by a monotonic anchor
    # captured from the same launch instant. Anchor is the budget measured from
    # the helper's start (not from a later setup instant), so setup time is
    # charged exactly once and a backwards clock correction can never extend it.
    # ElapsedMilliseconds exists only so the arithmetic can be tested without a
    # real delay; production always passes the live Stopwatch.
    [CmdletBinding()]
    param([DateTimeOffset]$Deadline, [int64]$Anchor, [scriptblock]$Now,
          [Diagnostics.Stopwatch]$Clock = $null, [int64]$ElapsedMilliseconds = 0)
    $elapsed = if ($null -ne $Clock) { [int64]$Clock.ElapsedMilliseconds } else { [Math]::Max([int64]0, $ElapsedMilliseconds) }
    $wall = [int64](($Deadline - (& $Now)).TotalMilliseconds)
    $monotonic = [int64]($Anchor - $elapsed)
    $value = [Math]::Min($wall, $monotonic)
    if ($value -lt 0) { return [int64]0 }
    return [int64]$value
}

function Get-AgcWorkerLifecycleConfigPath {
    [CmdletBinding()]
    param()
    if (Get-Command Get-AgcLifecycleConfigPath -CommandType Function -ErrorAction SilentlyContinue) {
        try {
            $path = Get-AgcLifecycleConfigPath
            if ($path) { return [string]$path }
        } catch { }
    }
    $root = 'C:\ProgramData\AGCLifecycle'
    if ($env:AGC_LIFECYCLE_ROOT) { $root = [string]$env:AGC_LIFECYCLE_ROOT }
    return (Join-Path $root 'config\lifecycle.json')
}

function Test-AgcWorkerOwnerSidBinding {
    <#
      Bind the owner cycle to the admin-owned lifecycle configuration, which is
      readable by Users/owners and is the same configuration the native service
      validates. It grants Users RX; C:\ProgramData\AGC-Worker\control (including
      the DPAPI credential) is admin-only and must never be read here.
    #>
    [CmdletBinding()]
    param([string]$ConfigPath, [string]$ExpectedSid, [string]$ExpectedDistro = 'AGC-Worker')
    $result = [pscustomobject]@{Bound=$false;Reason='lifecycle_config_unreadable';AccountSid='';AccountName=''}
    try {
        $item = Get-Item -LiteralPath $ConfigPath -Force -ErrorAction Stop
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 65536) {
            $result.Reason = 'lifecycle_config_invalid'
            return $result
        }
        $raw = Get-Content -LiteralPath $ConfigPath -Raw -ErrorAction Stop
    } catch { return $result }
    try { $config = $raw | ConvertFrom-Json -ErrorAction Stop } catch {
        $result.Reason = 'lifecycle_config_invalid'
        return $result
    }
    $worker = Get-AgcWorkerValue (Get-AgcWorkerValue $config 'owners' $null) 'worker' $null
    if ($null -eq $worker) { $result.Reason = 'owner_sid_missing'; return $result }
    $configured = [string](Get-AgcWorkerValue $worker 'accountSid' '')
    $result.AccountSid = $configured
    $result.AccountName = [string](Get-AgcWorkerValue $worker 'accountName' '')
    if ($configured -notmatch '^S-1-5-21-[0-9-]+$') { $result.Reason = 'owner_sid_missing'; return $result }
    if ($ExpectedSid -and $configured -ne $ExpectedSid) { $result.Reason = 'owner_sid_mismatch'; return $result }
    $configuredDistro = [string](Get-AgcWorkerValue $worker 'distro' '')
    if ($configuredDistro -and $configuredDistro -ne $ExpectedDistro) { $result.Reason = 'owner_distro_mismatch'; return $result }
    $result.Bound = $true
    $result.Reason = 'ok'
    return $result
}

function Get-AgcWorkerCycleSharedData {
    # The shared library flattens Data values to strings; native CycleEvidence
    # reads details.verification and details.verifyComplete from that flattened
    # receipt, so the cycle must pass flat scalars rather than a nested details
    # hashtable (which would serialize as "System.Collections.Hashtable").
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Receipt)
    function Read-CycleValue($Object, [string]$Name, $Default) {
        if ($null -eq $Object) { return $Default }
        if ($Object -is [System.Collections.IDictionary]) {
            if ($Object.Contains($Name) -and $null -ne $Object[$Name]) { return $Object[$Name] }
            return $Default
        }
        return (Get-AgcWorkerValue $Object $Name $Default)
    }
    $details = Read-CycleValue $Receipt 'details' $null
    return @{
        distro = [string](Read-CycleValue $Receipt 'distro' '')
        startedUtc = [string](Read-CycleValue $Receipt 'startedUtc' '')
        durationMs = [int](Read-CycleValue $Receipt 'durationMs' 0)
        reason = [string](Read-CycleValue $details 'reason' '')
        verification = [string](Read-CycleValue $details 'verification' '')
        verifyComplete = [string](Read-CycleValue $details 'verifyComplete' '')
        requestSha256 = [string](Read-CycleValue $details 'requestSha256' '')
        drained = [string](Read-CycleValue $details 'drained' '')
        earlyTerminate = [string](Read-CycleValue $details 'earlyTerminate' '')
        verificationPhase = [string](Read-CycleValue $details 'verificationPhase' '')
    }
}

function Invoke-AgcWorkerOwnerCycle {
    <#
      One bounded owner process per shutdown cycle: graceful stop, optional early
      exact-distro termination only after an explicit successful drain, otherwise
      wait to the graceful cutoff, then exactly one terminate and a fresh
      complete running-list proof. One monotonic absolute budget (wall clock and
      an anchored stopwatch, so a clock rollback can never extend it); no phase
      spawns an extra PowerShell. An unknown outcome is never reported stopped.
    #>
    [CmdletBinding()]
    param(
        $Request,
        [string]$RequestHash = '',
        [string]$WslPath = 'wsl.exe',
        [string]$Distro = 'AGC-Worker',
        [string]$DataPath = $script:AgcWorkerDataPath,
        [scriptblock]$Now = { [DateTimeOffset]::UtcNow },
        [scriptblock]$Wait = { param([int]$Milliseconds) Start-Sleep -Milliseconds $Milliseconds },
        [scriptblock]$GateCheck = { $true },
        [scriptblock]$RequestCheck = { $true },
        [scriptblock]$OnComplete = $null,
        [int]$VerifyReserveMilliseconds = 2500
    )
    $identity = Get-AgcWorkerIdentity
    $validation = Get-AgcWorkerCycleRequest -Request $Request
    $started = & $Now
    $clock = [Diagnostics.Stopwatch]::StartNew()
    # Budgets are anchored to the single launch instant, before any setup, so
    # remaining time is never charged twice for validation/lock/receipt reads.
    $anchorDeadline = [int64](($validation.Deadline - $started).TotalMilliseconds)
    $anchorGraceful = [int64](($validation.GracefulDeadline - $started).TotalMilliseconds)
    $anchorTerminate = [int64](($validation.TerminateDeadline - $started).TotalMilliseconds)
    $receipt = @{schema=1;owner='worker';requestId=$validation.RequestId;bootId=$validation.BootId;
        phase='cycle';outcome='unconfirmed';stopped=$false;verified=$false;scope='distro';source='owner';
        startedUtc=$started.ToString('o');accountName=$identity.Name;accountSid=$identity.Sid;distro=$Distro
        details=@{phase='cycle';reason=$validation.Reason;requestSha256=$RequestHash;
            issuedUtc=$(if($validation.Issued){$validation.Issued.ToString('o')}else{''});
            gracefulDeadlineUtc=$(if($validation.GracefulDeadline){$validation.GracefulDeadline.ToString('o')}else{''});
            terminateDeadlineUtc=$(if($validation.TerminateDeadline){$validation.TerminateDeadline.ToString('o')}else{''});
            deadlineUtc=$(if($validation.Deadline){$validation.Deadline.ToString('o')}else{''});
            coordinatorProcessId=$validation.CoordinatorProcessId;reasonText=$validation.ReasonText}}
    $finish = {
        param([string]$Outcome, [string]$Reason)
        $receipt.outcome = $Outcome
        $receipt.details.reason = $Reason
        $done = & $Now
        $receipt.finishedUtc = $done.ToString('o')
        $receipt.durationMs = [int]($done - $started).TotalMilliseconds
        [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $DataPath -RequestId $(if($validation.RequestId){$validation.RequestId}else{'unknown'}) -Phase 'cycle')
        if ($null -ne $OnComplete) {
            # The primary executor publishes the shared receipt while it still
            # holds the per-cycle lock, so a queued duplicate never races it.
            try { $receipt.shared_receipt_written = [bool](& $OnComplete $receipt) }
            catch { $receipt.shared_receipt_written = $false; $receipt.sharedReceiptError = $_.Exception.GetType().Name }
            [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $DataPath -RequestId $(if($validation.RequestId){$validation.RequestId}else{'unknown'}) -Phase 'cycle')
        }
        return [pscustomobject]@{outcome=$Outcome;stopped=[bool]$receipt.stopped;verified=[bool]$receipt.verified;
            reused=$false;duplicate=$false;reason=$Reason;receipt=$receipt;details=$receipt.details}
    }
    if ($Distro -ne 'AGC-Worker') { return (& $finish 'unconfirmed' 'distro_not_owned') }
    if (-not $validation.Valid) { return (& $finish 'unconfirmed' $validation.Reason) }
    # A request issued in the future is not a valid absolute budget: only a small
    # clock-skew tolerance is accepted, matching the legacy future-deadline cap.
    if (($validation.Issued - $started).TotalMilliseconds -gt 250) {
        return (& $finish 'unconfirmed' 'cycle_request_future')
    }
    $existing = Get-AgcWorkerCycleReceipt -DataPath $DataPath -RequestId $validation.RequestId -BootId $validation.BootId -Distro $Distro
    if ($null -ne $existing) {
        return [pscustomobject]@{outcome=[string]$existing.outcome;stopped=$true;verified=$true;reused=$true;
            duplicate=$false;reason='cycle_receipt_reused';receipt=$existing;details=$existing.details;duplicateReason=''}
    }
    $stream = $null
    try { $stream = [IO.File]::Open((Join-Path $DataPath ($validation.RequestId + '.cycle.lock')), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { return [pscustomobject]@{outcome='unconfirmed';stopped=$false;verified=$false;reused=$false;
        duplicate=$true;reason='duplicate_executor';receipt=$receipt;details=$receipt.details;duplicateReason='duplicate_executor'} }
    try {
        $existing = Get-AgcWorkerCycleReceipt -DataPath $DataPath -RequestId $validation.RequestId -BootId $validation.BootId -Distro $Distro
        if ($null -ne $existing) {
            return [pscustomobject]@{outcome=[string]$existing.outcome;stopped=$true;verified=$true;reused=$true;
                duplicate=$true;reason='cycle_receipt_reused';receipt=$existing;details=$existing.details;duplicateReason=''}
        }
        $publicationReserve = 400
        $receipt.details.launchLatencyMs = [int64]($started - $validation.Issued).TotalMilliseconds
        $receipt.details.publicationReserveMs = $publicationReserve
        # Absolute remaining time: the wall clock is bounded by an anchored
        # stopwatch so a backwards clock correction cannot extend the budget.
        $remaining = {
            param([DateTimeOffset]$Deadline, [int64]$Anchor)
            return (Get-AgcWorkerCycleRemaining -Deadline $Deadline -Anchor $Anchor -Now $Now -Clock $clock)
        }
        if ((& $remaining $validation.Deadline $anchorDeadline) -le 0) { return (& $finish 'unconfirmed' 'cycle_deadline_exhausted') }
        if (-not (& $GateCheck)) { return (& $finish 'unconfirmed' 'gate_request_mismatch') }
        if (-not (& $RequestCheck)) { return (& $finish 'unconfirmed' 'cycle_request_replaced') }
        $listBudget = [Math]::Min([int64]3000, (& $remaining $validation.Deadline $anchorDeadline))
        if ($listBudget -le 0) { return (& $finish 'unconfirmed' 'cycle_deadline_exhausted') }
        try { $running = @(Get-AgcWorkerRunningDistros -WslPath $WslPath -TimeoutMilliseconds ([int]$listBudget)) }
        catch { return (& $finish 'unconfirmed' 'running_list_unverified') }
        $receipt.details.initialRunning = ($running -contains $Distro)
        $drained = $false
        if ($running -notcontains $Distro) {
            $receipt.stopped = $true; $receipt.verified = $true
            $receipt.details.verification = 'running-list'; $receipt.details.verifyComplete = 'true'
            $receipt.details.verificationFresh = $true; $receipt.details.verificationPhase = 'before_stop'
            return (& $finish 'graceful' 'absent_before_stop')
        }
        $gracefulBudget = [Math]::Min([int64]10000, (& $remaining $validation.GracefulDeadline $anchorGraceful))
        if ($gracefulBudget -gt 0) {
            if (-not (& $GateCheck)) { return (& $finish 'unconfirmed' 'gate_request_mismatch') }
            if (-not (& $RequestCheck)) { return (& $finish 'unconfirmed' 'cycle_request_replaced') }
            try {
                $stop = Invoke-AgcWorkerWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/systemctl','stop','agc-worker.service') `
                    -TimeoutMilliseconds ([int]$gracefulBudget) -WslPath $WslPath
                $receipt.details.stopTimedOut = [bool]($stop.TimedOut -eq $true)
                $receipt.details.stopExitCode = $(if ($null -ne $stop.ExitCode) { [int]$stop.ExitCode } else { -1 })
                $drained = ($stop.TimedOut -ne $true -and $null -ne $stop.ExitCode -and [int]$stop.ExitCode -eq 0)
            } catch { $receipt.details.stopError = $_.Exception.GetType().Name }
        }
        $receipt.details.drained = $drained
        if ($drained) {
            $verifyBudget = [Math]::Min([int64]3000, (& $remaining $validation.Deadline $anchorDeadline)) - $publicationReserve
            if ($verifyBudget -gt 0) {
                try { $afterStop = @(Get-AgcWorkerRunningDistros -WslPath $WslPath -TimeoutMilliseconds ([int]$verifyBudget)) }
                catch { $afterStop = $null }
                if ($null -ne $afterStop -and $afterStop -notcontains $Distro) {
                    $receipt.stopped = $true; $receipt.verified = $true
                    $receipt.details.verification = 'running-list'; $receipt.details.verifyComplete = 'true'
                    $receipt.details.verificationFresh = $true; $receipt.details.verificationPhase = 'post_drain'
                    return (& $finish 'graceful' 'drained_and_absent')
                }
            }
            $receipt.details.earlyTerminate = $true
        } else {
            $untilGraceful = (& $remaining $validation.GracefulDeadline $anchorGraceful)
            if ($untilGraceful -gt 0) { & $Wait ([int]$untilGraceful); $receipt.details.waitedForGracefulMs = [int]$untilGraceful }
        }
        if (-not (& $GateCheck)) { return (& $finish 'unconfirmed' 'gate_request_mismatch') }
        if (-not (& $RequestCheck)) { return (& $finish 'unconfirmed' 'cycle_request_replaced') }
        $terminateBudget = [Math]::Min([int64]8000, (& $remaining $validation.TerminateDeadline $anchorTerminate))
        # Leave the verification allowance plus a receipt-publication margin so
        # the helper can still finish inside the native 24 s cutoff.
        $finalRoom = (& $remaining $validation.Deadline $anchorDeadline) - ($VerifyReserveMilliseconds + $publicationReserve)
        $terminateBudget = [Math]::Min($terminateBudget, $finalRoom)
        if ($terminateBudget -gt 0) {
            try {
                $terminate = Invoke-AgcWorkerWsl -Arguments @('--terminate',$Distro) -TimeoutMilliseconds ([int]$terminateBudget) -WslPath $WslPath
                $receipt.details.terminateTimedOut = [bool]($terminate.TimedOut -eq $true)
                $receipt.details.terminateExitCode = $(if ($null -ne $terminate.ExitCode) { [int]$terminate.ExitCode } else { -1 })
            } catch { $receipt.details.terminateError = $_.Exception.GetType().Name }
        } else {
            # No room to terminate safely: skip the command but still spend any
            # remaining verification time on a fresh final query.
            $receipt.details.terminateSkipped = 'budget'
        }
        $verifyBudget = [Math]::Min([int64]3000, (& $remaining $validation.Deadline $anchorDeadline)) - $publicationReserve
        if ($verifyBudget -le 0) { return (& $finish 'unconfirmed' 'verification_deadline_exhausted') }
        try { $after = @(Get-AgcWorkerRunningDistros -WslPath $WslPath -TimeoutMilliseconds ([int]$verifyBudget)) }
        catch { return (& $finish 'unconfirmed' 'verification_unverified') }
        $receipt.details.verificationFresh = $true
        $receipt.details.verificationPhase = 'post_terminate'
        if ($after -contains $Distro) {
            if ($receipt.details.ContainsKey('terminateSkipped')) {
                return (& $finish 'unconfirmed' 'terminate_deadline_exhausted')
            }
            return (& $finish 'unconfirmed' 'distro_still_running')
        }
        $receipt.stopped = $true; $receipt.verified = $true
        $receipt.details.verification = 'running-list'; $receipt.details.verifyComplete = 'true'
        if ($receipt.details.ContainsKey('terminateExitCode')) { return (& $finish 'forced' 'terminated_and_absent') }
        return (& $finish 'graceful' 'absent_and_verified')
    } catch {
        $receipt.details.errorType = $_.Exception.GetType().Name
        return (& $finish 'unconfirmed' 'cycle_error')
    } finally {
        if ($null -ne $stream) { try { $stream.Dispose() } catch { } }
    }
}

function Write-AgcWorkerStopReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Data,
        [string]$RequestId = 'local',
        [string]$Outcome = 'unconfirmed',
        [ValidateSet('stop','terminate','verify','cycle')][string]$Phase = 'stop'
    )
    # The shared library stringifies every Data value, so a nested details
    # hashtable would appear as "System.Collections.Hashtable". A cycle receipt
    # is flattened to the scalar keys the native CycleEvidence parser reads.
    $sharedData = if ($Phase -eq 'cycle') { Get-AgcWorkerCycleSharedData -Receipt $Data } else { $Data }
    $sharedData['phase'] = $Phase
    # The shared library only accepts graceful/forced/unconfirmed. Keep the
    # richer local wording in details and never let a binding exception look
    # like a successful shared receipt.
    $sharedOutcome = switch ($Outcome) {
        'forced_scoped' { 'forced' }
        'stopped_absent' { 'graceful' }
        'graceful' { 'graceful' }
        'forced' { 'forced' }
        default { 'unconfirmed' }
    }
    $sharedData['outcomeDetail'] = $Outcome
    $written = $false
    if ($script:AgcWorkerLoaded) {
        try {
            $null = Write-AgcStopReceipt -Owner $script:AgcWorkerOwner -RequestId $RequestId `
                -Outcome $sharedOutcome -Data $sharedData -Phase $Phase
            $written = $true
        } catch {
            $Data['sharedReceiptError'] = $_.Exception.GetType().Name + ': ' + $_.Exception.Message
        }
    } else {
        $Data['sharedReceiptError'] = 'lifecycle_library_unavailable'
    }
    return $written
}

function Save-AgcWorkerLocalStopReceipt {
    [CmdletBinding()]
    param([hashtable]$Receipt, [string]$DataPath = $script:AgcWorkerDataPath,
          [string]$RequestId = 'local', [ValidateSet('stop','terminate','verify','cycle')][string]$Phase = 'stop')
    try {
        $json = $Receipt | ConvertTo-Json -Depth 10
        foreach ($name in @("$RequestId.$Phase.json", "stop-$RequestId.json")) {
            $path = Join-Path $DataPath $name
            $temporary = "$path.tmp"
            $json | Set-Content -LiteralPath $temporary -Encoding UTF8
            Move-Item -LiteralPath $temporary -Destination $path -Force
        }
        return $true
    } catch { return $false }
}


function Assert-AgcWorkerGateOpen {
    [CmdletBinding()]
    param()
    $gate = Get-AgcWorkerGate
    if (-not $gate.Allowed) { throw 'lifecycle_gate_closed' }
    return $gate
}

function Start-AgcWorkerAdmittedProcessInfo {
    <#
      Atomic admission for a caller-built ProcessStartInfo (the handover keeper).
      The shared launch mutex is held only for gate recheck + process creation;
      the caller writes stdin/reads output after this function returns.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Diagnostics.ProcessStartInfo]$StartInfo,
        [int]$TimeoutMilliseconds = 2000
    )
    if (-not (Test-AgcWorkerAdmissionApi)) { throw 'atomic_admission_unavailable' }
    if ($TimeoutMilliseconds -lt 100) { $TimeoutMilliseconds = 100 }
    try { $mutex = Get-AgcLaunchMutex } catch { return $null }
    $acquired = $false
    try {
        try {
            $acquired = $mutex.WaitOne($TimeoutMilliseconds)
        } catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) { return $null }
        if (-not (Test-AgcStartAllowed -Owner $script:AgcWorkerOwner)) { return $null }
        return [Diagnostics.Process]::Start($StartInfo)
    } finally {
        if ($acquired) { try { $mutex.ReleaseMutex() } catch { } }
        try { $mutex.Dispose() } catch { }
    }
}

function Invoke-AgcWorkerScopedTerminate {
    [CmdletBinding()]
    param(
        [string]$RequestId = 'local',
        [string]$ExpectedSid = '',
        [object]$StopRequest = $null,
        [string]$Reason = 'lifecycle_stop',
        [int]$TotalBudgetMilliseconds = -1,
        [string]$WslPath = 'wsl.exe',
        [string]$Distro = 'AGC-Worker',
        [string]$DataPath = $script:AgcWorkerDataPath,
        [ValidateSet('stop','terminate','verify')][string]$Phase = 'terminate'
    )
    if ($TotalBudgetMilliseconds -lt 0) {
        $TotalBudgetMilliseconds = Get-AgcWorkerRemainingMilliseconds -StopRequest $StopRequest
    }
    $budgetExhausted = ($TotalBudgetMilliseconds -eq 0)
    $started = [DateTimeOffset]::UtcNow
    $deadline = $started.AddMilliseconds($TotalBudgetMilliseconds)
    $identity = Get-AgcWorkerIdentity
    $account = $identity.Name
    $receipt = @{
        version = 1; request_id = $RequestId; worker_sid = $(if($ExpectedSid){$ExpectedSid}else{$identity.Sid}); owner = 'worker'
        account = $account; distro = $Distro; reason = $Reason; state = 'stopping'
        termination_confirmed = $false; outcome = 'unconfirmed'
        started_at = $started.ToString('o')
    }
    function Remaining([int]$Cap) {
        $value = [int64]($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds
        if ($value -lt 0) { $value = 0 }
        if ($value -gt $Cap) { $value = $Cap }
        return [int]$value
    }
    try {
        if ($budgetExhausted) { throw 'lifecycle_deadline_exhausted' }
        $running = @(Get-AgcWorkerRunningDistros -WslPath $WslPath -TimeoutMilliseconds (Remaining 3000))
        if ($running -notcontains $Distro) {
            $receipt.state = 'stopped'; $receipt.termination_confirmed = $true; $receipt.outcome = 'stopped_absent'
        } else {
            # Direct termination is the only WSL command used here: unlike
            # `wsl -d ... --exec`, it cannot start a stopped distro after a
            # concurrent owner stop.
            $terminate = Invoke-AgcWorkerWsl -Arguments @('--terminate',$Distro) `
                -TimeoutMilliseconds (Remaining 8000) -WslPath $WslPath
            if ($terminate.TimedOut -eq $true) { $receipt.terminate_timed_out = $true }
            if ($null -ne $terminate.ExitCode -and [int]$terminate.ExitCode -ne 0) {
                $receipt.terminate_exit_code = [int]$terminate.ExitCode
            }
            $remaining = @(Get-AgcWorkerRunningDistros -WslPath $WslPath -TimeoutMilliseconds (Remaining 3000))
            if ($remaining -contains $Distro) {
                throw 'worker_distro_still_running'
            }
            $receipt.state = 'stopped'; $receipt.termination_confirmed = $true; $receipt.outcome = 'forced_scoped'
        }
    } catch {
        $receipt.state = 'unknown'
        $receipt.error_type = $_.Exception.GetType().Name
        $receipt.message = 'Worker shutdown not confirmed; retain queue reservations and inspect the stop receipt'
        Write-AgcWorkerLog -Event 'worker_stop_unconfirmed' -Data @{request_id = $RequestId; error = $receipt.error_type}
    }
    $finished = [DateTimeOffset]::UtcNow
    $receipt.finished_at = $finished.ToString('o')
    $receipt.duration_ms = [int]($finished - $started).TotalMilliseconds
    $receipt.phase = $Phase
    $receipt.shared_receipt_written = Write-AgcWorkerStopReceipt -Data $receipt -RequestId $RequestId -Outcome $receipt.outcome -Phase $Phase
    [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $DataPath -RequestId $RequestId -Phase $Phase)
    if ($receipt.termination_confirmed -eq $true) {
        Write-AgcWorkerLog -Event 'worker_stopped' -Data @{request_id = $RequestId; outcome = $receipt.outcome; duration_ms = $receipt.duration_ms}
    }
    return [pscustomobject]$receipt
}
