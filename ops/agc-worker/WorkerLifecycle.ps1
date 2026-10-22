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

function Write-AgcWorkerStopReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Data,
        [string]$RequestId = 'local',
        [string]$Outcome = 'unconfirmed',
        [ValidateSet('stop','terminate','verify')][string]$Phase = 'stop'
    )
    $Data['phase'] = $Phase
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
    $Data['outcomeDetail'] = $Outcome
    $written = $false
    if ($script:AgcWorkerLoaded) {
        try {
            $null = Write-AgcStopReceipt -Owner $script:AgcWorkerOwner -RequestId $RequestId `
                -Outcome $sharedOutcome -Data $Data -Phase $Phase
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
          [string]$RequestId = 'local', [ValidateSet('stop','terminate','verify')][string]$Phase = 'stop')
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
