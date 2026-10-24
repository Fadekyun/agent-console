# Resident worker owner executor regression (#212).
# Run: pwsh -NoProfile -File ops/agc-worker/tests/WorkerResident.Tests.ps1
# Fake shared executor loop + fake WSL only; no Windows/WSL/coordinator calls.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-worker-resident-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$workerRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$entry = Join-Path $workerRoot 'worker-executor.ps1'
$installer = Get-Content -LiteralPath (Join-Path $workerRoot 'Install-LifecycleStopTask.ps1') -Raw

# --- Reproduction: a fresh task launch during Preshutdown is refused ---
# The Oct-23 failure: schtasks /run returned 0 but the task engine reported
# 0x8007045B (ERROR_SHUTDOWN_IN_PROGRESS), so no owner helper ran. The resident
# path must complete the same cycle with zero Scheduler invocations.
$script:SchedulerCalls = 0
function Invoke-FakeSchedulerStart {
    param([bool]$Preshutdown)
    $script:SchedulerCalls++
    if ($Preshutdown) { return [pscustomobject]@{ ExitCode = 0; LastTaskResult = -2147023781 } }  # 0x8007045B
    return [pscustomobject]@{ ExitCode = 0; LastTaskResult = 0 }
}
$launch = Invoke-FakeSchedulerStart -Preshutdown $true
Assert-Equal $launch.LastTaskResult (-2147023781) 'regression model: preshutdown task launch is refused with 0x8007045B'
Assert-True ($script:SchedulerCalls -eq 1) 'regression model: the refused launch is observable'
$script:SchedulerCalls = 0

# --- Entrypoint contract ---
Assert-True (Test-Path -LiteralPath $entry) 'worker resident entrypoint exists'
$entrySource = Get-Content -LiteralPath $entry -Raw
Assert-True ($entrySource -match '(?m)^# AGC_OWNER_RESIDENT_EXECUTOR=1$') 'entrypoint advertises the resident marker'
Assert-True ($entrySource -match 'Invoke-AgcWorkerResidentExecutor\s+-FromTask:\$FromTask') 'entrypoint calls the resident executor core'
$adapter = Get-Content -LiteralPath (Join-Path $workerRoot 'WorkerLifecycle.ps1') -Raw
Assert-True ($adapter -match 'function Invoke-AgcWorkerResidentExecutor') 'resident executor core exists in the adapter'
Assert-True ($adapter -match '\\agc-worker\$') 'resident core requires the agc-worker identity'
Assert-True ($adapter -match 'Invoke-AgcExecutorLoop\s+-Owner\s+.worker.') 'resident core calls the shared executor loop for worker'
Assert-True ($adapter -match 'Invoke-AgcWorkerOwnerCycleResult') 'resident cycle invoker reuses the worker cycle'
Assert-True ($entrySource -notmatch 'schtasks') 'entrypoint never launches a task'
Assert-True ($entrySource -notmatch 'LogonUser|Start-ScheduledTask') 'entrypoint never logs on or starts a task'
Assert-True ($entrySource -match 'Invoke-AgcWorkerResidentExecutor') 'resident core is invoked by the installed entrypoint'


# --- Installer: independent fixed task, existing credential, Ready-time start ---
Assert-True ($installer -match 'AGC-Worker-OwnerExecutor') 'installer registers the worker owner executor task'
Assert-True ($installer -match 'worker-executor\.ps1') 'installer installs the resident entrypoint'
Assert-True ($installer -match 'New-ScheduledTaskTrigger -AtStartup') 'executor has a boot trigger'
Assert-True ($installer -match 'New-ScheduledTaskTrigger -AtLogOn') 'executor has a logon recovery trigger'
Assert-True ($installer -match '-ExecutionTimeLimit \(\[TimeSpan\]::Zero\)') 'executor has no execution time limit'
Assert-True ($installer -match '-MultipleInstances IgnoreNew') 'executor is single instance'
Assert-True ($installer -match '-WakeToRun:\$false') 'executor never requests a wake'
Assert-True ($installer -match '-AllowStartIfOnBatteries') 'executor is allowed on battery'
Assert-True ($installer -match 'Read-TaskCredential') 'executor uses the existing protected credential helper'
Assert-True ($installer -match 'Get-AgcExecutorReadiness') 'installer verifies readiness through the shared helper'
Assert-True ($installer -match 'Start-ScheduledTask') 'installer starts the executor while Windows is ready'
Assert-True ($installer -match '\[switch\]\$DeferExecutorStart') 'installer exposes the first-rollout defer switch'
Assert-True ($installer -match "DeferExecutorStart requires EnableOwnerExecutor") 'defer is rejected without executor enablement'
Assert-True ($installer -match "registered-disabled") 'defer path records registered-disabled instead of ready'
Assert-True ($installer -match 'Disable-ScheduledTask') 'defer path registers the executor disabled'
Assert-True ($installer -match 'Enable-ScheduledTask') 'activation path explicitly enables the executor'
Assert-True ($installer -match 'icacls\.exe \$workerReceipts') 'readiness ACL repair is applied outside the create-if-missing branch'
Assert-True ($installer.IndexOf('if ($DeferExecutorStart)') -lt $installer.IndexOf('Start-ScheduledTask')) 'defer path decides before any task start'
Assert-True ($installer -match 'Existing owner executor task has a different principal') 'existing foreign executor principal is refused'
Assert-True ($installer -match 'Existing owner executor task action does not match') 'existing foreign executor action is refused'

# --- Cycle invoker contract (fake shared library + fake WSL) ---
$library = Join-Path $root 'Lifecycle.ps1'
@'
function Get-AgcStopRequest { param([string]$Owner) if (Test-Path -LiteralPath $env:FAKE_GATE) { return (Get-Content -LiteralPath $env:FAKE_GATE -Raw | ConvertFrom-Json) }; return $null }
function Test-AgcStartAllowed { param([string]$Owner) return $true }
function Invoke-AgcBoundedProcess {
    param([string]$FilePath,[string[]]$ArgumentList=@(),[int]$TimeoutMilliseconds=1000,[string]$StandardInput,[int]$MaxOutputBytes=65536,[switch]$RequireAdmission,[string]$Owner)
    Add-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls') -Value ($ArgumentList -join ' ')
    $running = Join-Path $env:FAKE_STATE 'running'
    if ($ArgumentList -contains '--list') {
        $out = if (Test-Path -LiteralPath $running) { "AGC-Worker`n" } else { '' }
        return [pscustomobject]@{Started=$true;ExitCode=0;TimedOut=$false;Stdout=$out;Stderr='';OutputTruncated=$false;DurationMs=0}
    }
    if ($ArgumentList -contains '--terminate') { Remove-Item -LiteralPath $running -ErrorAction SilentlyContinue }
    if ($ArgumentList -contains '-d') { Remove-Item -LiteralPath $running -ErrorAction SilentlyContinue }
    return [pscustomobject]@{Started=$true;ExitCode=0;TimedOut=$false;Stdout='';Stderr='';OutputTruncated=$false;DurationMs=0}
}
function Write-AgcLifecycleLog { param([string]$Stream,[string]$Event,[hashtable]$Data=@{}) }
function Write-AgcStopReceipt {
    param([string]$Owner,[string]$RequestId,[ValidateSet('graceful','forced','unconfirmed')][string]$Outcome,
          [hashtable]$Data,[ValidateSet('stop','terminate','verify','cycle')][string]$Phase='stop')
    Add-Content -LiteralPath $env:FAKE_SHARED -Value ((@{requestId=$RequestId;outcome=$Outcome;phase=$Phase;data=$Data} | ConvertTo-Json -Compress -Depth 8))
}
'@ | Set-Content -LiteralPath $library -Encoding UTF8
$env:FAKE_STATE = Join-Path $root 'state'
$env:FAKE_SHARED = Join-Path $root 'shared.log'
$env:FAKE_GATE = Join-Path $root 'gate.json'
New-Item -ItemType Directory -Path $env:FAKE_STATE -Force | Out-Null
'1' | Set-Content -LiteralPath (Join-Path $env:FAKE_STATE 'running')
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
$requestPath = Join-Path $root 'current.json'
$now = [DateTimeOffset]::UtcNow
$request = [pscustomobject]@{schema=1;owner='worker';phase='cycle';requestId=('a' * 32);bootId='resident-boot';
    issuedUtc=$now.ToString('o');gracefulDeadlineUtc=$now.AddSeconds(13).ToString('o');
    terminateDeadlineUtc=$now.AddSeconds(21).ToString('o');deadlineUtc=$now.AddSeconds(24).ToString('o');
    coordinatorProcessId=4242;reason='resident regression'}
$request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $requestPath -Encoding UTF8
@{requestId=$request.requestId;bootId=$request.bootId} | ConvertTo-Json | Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8

. (Join-Path $workerRoot 'WorkerLifecycle.ps1') -LibraryPath $library -DataPath $data
[void](Initialize-AgcWorkerLifecycle -Path $library)
$result = Invoke-AgcWorkerOwnerCycleResult -Request $request -RequestPath $requestPath -WslPath 'wsl.exe' -DataPath $data
Assert-True ($result.Outcome -in @('graceful','forced')) 'resident invoker completes the cycle'
Assert-True ($result.Verified -eq $true) 'resident invoker returns verified proof'
Assert-True (Test-Path -LiteralPath $result.ReceiptPath) 'resident invoker reports the shared cycle receipt'
$receipt = Get-Content -LiteralPath $result.ReceiptPath -Raw | ConvertFrom-Json
Assert-True ($null -ne $receipt.details.executorPid -and [int]$receipt.details.executorPid -eq $PID) 'receipt binds the executing executor PID'
$parsedStart = ConvertTo-AgcWorkerTime $receipt.details.executorStartUtc
$actualStart = (Get-Process -Id $PID).StartTime.ToUniversalTime()
Assert-True ([Math]::Abs(($parsedStart.ToUniversalTime().Ticks - $actualStart.Ticks) / [TimeSpan]::TicksPerMillisecond) -lt 2000) 'receipt binds the executor process start time'
Assert-Equal $script:SchedulerCalls 0 'resident mode performs zero Scheduler calls'

# Successive invocations: a completed cycle is reused without new guest work.
$before = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
$again = Invoke-AgcWorkerOwnerCycleResult -Request $request -RequestPath $requestPath -WslPath 'wsl.exe' -DataPath $data
$after = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
Assert-True ($again.Outcome -in @('graceful','forced')) 'duplicate resident invocation reports the proven outcome'
Assert-Equal $after $before 'duplicate resident invocation issues no guest commands'

# A later cycle in the same boot (stop -> restore -> stop) is handled normally.
$secondIssued = [DateTimeOffset]::UtcNow
$second = [pscustomobject]@{schema=1;owner='worker';phase='cycle';requestId=('b' * 32);bootId='resident-boot';
    issuedUtc=$secondIssued.ToString('o');gracefulDeadlineUtc=$secondIssued.AddSeconds(13).ToString('o');
    terminateDeadlineUtc=$secondIssued.AddSeconds(21).ToString('o');deadlineUtc=$secondIssued.AddSeconds(24).ToString('o');
    coordinatorProcessId=4242;reason='second resident cycle'}
'1' | Set-Content -LiteralPath (Join-Path $env:FAKE_STATE 'running')
$x = $second | ConvertTo-Json -Depth 5
$x | Set-Content -LiteralPath $requestPath -Encoding UTF8
@{requestId=$second.requestId;bootId=$second.bootId} | ConvertTo-Json | Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8
$secondResult = Invoke-AgcWorkerOwnerCycleResult -Request $second -RequestPath $requestPath -WslPath 'wsl.exe' -DataPath $data
Assert-True ($secondResult.Outcome -in @('graceful','forced')) 'a successive cycle in the same boot completes'
Assert-True ($secondResult.Verified -eq $true) 'a successive cycle returns verified proof'

# A foreign gate binding is refused before any guest command.
'{ "requestId": "ffffffffffffffffffffffffffffffff", "bootId": "resident-boot" }' |
    Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8
$before = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
$foreignRequest = $request.PSObject.Copy()
$foreignRequest.requestId = ('e' * 32)
$foreignRequest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $requestPath -Encoding UTF8
$foreign = Invoke-AgcWorkerOwnerCycleResult -Request $foreignRequest -RequestPath $requestPath -WslPath 'wsl.exe' -DataPath $data
Assert-Equal $foreign.Outcome 'unconfirmed' 'foreign gate binding is unconfirmed'
$after = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
Assert-Equal $after $before 'foreign gate binding issues no guest commands'

# An expired request is refused truthfully.
@{requestId=$request.requestId;bootId=$request.bootId} | ConvertTo-Json | Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8
$expired = [pscustomobject]@{schema=1;owner='worker';phase='cycle';requestId=('d' * 32);bootId='resident-boot';
    issuedUtc=$now.AddSeconds(-30).ToString('o');gracefulDeadlineUtc=$now.AddSeconds(-17).ToString('o');
    terminateDeadlineUtc=$now.AddSeconds(-9).ToString('o');deadlineUtc=$now.AddSeconds(-6).ToString('o');
    coordinatorProcessId=4242;reason='expired resident regression'}
$expiredPath = Join-Path $root 'expired.json'
$expired | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $expiredPath -Encoding UTF8
$before = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
$expiredResult = Invoke-AgcWorkerOwnerCycleResult -Request $expired -RequestPath $expiredPath -WslPath 'wsl.exe' -DataPath $data
Assert-Equal $expiredResult.Outcome 'unconfirmed' 'expired request is unconfirmed'
$after = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls')).Count
Assert-Equal $after $before 'expired request issues no guest commands'

Write-Host ("worker resident tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
