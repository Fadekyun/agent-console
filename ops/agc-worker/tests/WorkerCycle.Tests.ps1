# Local pure-logic regression for the worker owner-cycle protocol (#210).
# Run: pwsh -NoProfile -File ops/agc-worker/tests/WorkerCycle.Tests.ps1
# Injected clock + fake wsl executable; no Windows, WSL, service or coordinator
# calls are made.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-worker-cycle-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$library = Join-Path $root 'Lifecycle.ps1'
@'
function Test-AgcStartAllowed { param([string]$Owner) return $true }
function Get-AgcStopRequest { param([string]$Owner) return $null }
function Invoke-AgcBoundedProcess {
    param([string]$FilePath,[string[]]$ArgumentList=@(),[int]$TimeoutMilliseconds=1000,[string]$StandardInput,[int]$MaxOutputBytes=65536,[switch]$RequireAdmission,[string]$Owner)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $FilePath
    foreach ($argument in $ArgumentList) { $start.ArgumentList.Add($argument) }
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($start)
    $process.StandardInput.Close()
    $timedOut = -not $process.WaitForExit($TimeoutMilliseconds)
    if ($timedOut) { $process.Kill(); [void]$process.WaitForExit(1000) }
    $stdout = $process.StandardOutput.ReadToEnd()
    [void]$process.StandardError.ReadToEnd()
    $exitCode = if ($timedOut) { $null } else { $process.ExitCode }
    $process.Dispose()
    return [pscustomobject]@{Started=$true;ExitCode=$exitCode;TimedOut=$timedOut;Stdout=$stdout;Stderr='';OutputTruncated=$false;DurationMs=0}
}
function Write-AgcLifecycleLog { param([string]$Stream,[string]$Event,[hashtable]$Data=@{}) }
function Write-AgcStopReceipt {
    param([string]$Owner,[string]$RequestId,[ValidateSet('graceful','forced','unconfirmed')][string]$Outcome,
          [hashtable]$Data,[ValidateSet('stop','terminate','verify','cycle')][string]$Phase='stop')
    Add-Content -LiteralPath $script:FakeReceipt -Value ((@{requestId=$RequestId;outcome=$Outcome;phase=$Phase;data=$Data} | ConvertTo-Json -Compress -Depth 8))
}
'@ | Set-Content -LiteralPath $library -Encoding UTF8
$script:FakeReceipt = Join-Path $root 'receipt.log'
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null

$fakeWsl = Join-Path $root 'fake-wsl.sh'
@'
#!/bin/sh
state="$FAKE_WSL_STATE"
printf '%s\n' "$*" >> "$state/calls"
if [ "$1" = "--list" ]; then
  [ -f "$state/list_fail" ] && exit 2
  [ -f "$state/running" ] && echo AGC-Worker
  exit 0
fi
if [ "$1" = "--terminate" ]; then
  [ -f "$state/terminate_fail" ] && exit 3
  rm -f "$state/running"
  exit 0
fi
if [ "$1" = "-d" ]; then
  case "$*" in
    *"systemctl stop agc-worker.service"*)
      [ -f "$state/stop_fail" ] && exit 1
      [ -f "$state/stop_removes_running" ] && rm -f "$state/running"
      exit 0 ;;
  esac
fi
exit 0
'@ | Set-Content -LiteralPath $fakeWsl -Encoding UTF8
& chmod +x $fakeWsl
$state = Join-Path $root 'wslstate'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$env:FAKE_WSL_STATE = $state
function Reset-State([switch]$Running) {
    Remove-Item -LiteralPath $state -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    if ($Running) { '1' | Set-Content -LiteralPath (Join-Path $state 'running') }
}
function Get-Calls {
    $path = Join-Path $state 'calls'
    if (Test-Path $path) { return @(Get-Content -LiteralPath $path) }
    return @()
}

. (Join-Path $PSScriptRoot '..\WorkerLifecycle.ps1') -LibraryPath $library -DataPath $data
[void](Initialize-AgcWorkerLifecycle -Path $library)

# Injected monotonic-ish clock: wait advances it without real sleeping.
$script:Clock = [DateTimeOffset]::Parse('2026-10-23T00:00:00.000Z')
$script:Waits = @()
$Now = { $script:Clock }
$Wait = { param([int]$Milliseconds) $script:Waits += $Milliseconds; $script:Clock = $script:Clock.AddMilliseconds($Milliseconds) }
$issued = $script:Clock
$script:ReqSeq = 0
function New-CycleRequest {
    param([DateTimeOffset]$Issued = $issued, [object]$Graceful = $null, [object]$Terminate = $null, [object]$Deadline = $null)
    if ($null -eq $Graceful) { $Graceful = $Issued.AddSeconds(13) }
    if ($null -eq $Terminate) { $Terminate = $Issued.AddSeconds(21) }
    if ($null -eq $Deadline) { $Deadline = $Issued.AddSeconds(24) }
    $script:ReqSeq++
    return [pscustomobject]@{
        schema = 1; owner = 'worker'; requestId = ('{0:x32}' -f $script:ReqSeq); bootId = 'b20261023T000000Z'
        issuedUtc = $Issued.ToString('o'); gracefulDeadlineUtc = $Graceful.ToString('o')
        terminateDeadlineUtc = $Terminate.ToString('o'); deadlineUtc = $Deadline.ToString('o')
        coordinatorProcessId = 4242; reason = 'operator_restart'; phase = 'cycle'
    }
}

# --- Request validation (pure) ---
$valid = Get-AgcWorkerCycleRequest -Request (New-CycleRequest)
Assert-True ($valid.Valid -eq $true) 'valid cycle request accepted'
Assert-True ($valid.GracefulDeadline -gt $valid.Issued) 'graceful deadline parsed'
$bad = New-CycleRequest -Graceful $issued.AddSeconds(13.5)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'graceful_deadline_out_of_bounds' 'graceful beyond +13s rejected'
$bad = New-CycleRequest -Terminate $issued.AddSeconds(21.5)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'terminate_deadline_out_of_bounds' 'terminate beyond +21s rejected'
$bad = New-CycleRequest -Deadline $issued.AddSeconds(24.5)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'deadline_out_of_bounds' 'final deadline beyond +24s rejected'
$bad = New-CycleRequest -Graceful $issued.AddSeconds(14)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'graceful_deadline_out_of_bounds' 'compressed window keeps caps'
$bad = New-CycleRequest -Terminate $issued.AddSeconds(12)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'deadline_order_invalid' 'terminate before graceful rejected'
$bad = New-CycleRequest -Deadline $issued.AddSeconds(20)
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'deadline_order_invalid' 'final before terminate rejected'
$bad = New-CycleRequest; $bad.requestId = 'xyz'
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'request_id_invalid' 'bad request id rejected'
$bad = New-CycleRequest; $bad.schema = 2
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'request_binding_invalid' 'wrong schema rejected'
$bad = New-CycleRequest; $bad.phase = 'stop'
Assert-Equal (Get-AgcWorkerCycleRequest -Request $bad).Reason 'request_binding_invalid' 'non-cycle phase rejected by cycle validator'

$GateOk = { $true }
$RequestOk = { $true }

# --- Owner binding uses the readable lifecycle configuration, not control ---
$configDir = Join-Path $root 'lifecycleconfig'
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$configPath = Join-Path $configDir 'lifecycle.json'
$fakeSid = 'S-1-5-21-1111-2222-3333-1001'
function Write-Config([object]$Worker) {
    @{schema=1;owners=@{worker=$Worker}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
}
Write-Config @{accountSid=$fakeSid;accountName='agc-worker';distro='AGC-Worker'}
$bound = Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid
Assert-True ($bound.Bound -eq $true) 'configured worker SID binds the owner cycle'
Assert-Equal $bound.Reason 'ok' 'binding reason ok'
Write-Config @{accountSid='S-1-5-21-1111-2222-3333-1002';accountName='agc-worker';distro='AGC-Worker'}
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid).Reason 'owner_sid_mismatch' 'foreign configured SID rejected'
Write-Config @{accountName='agc-worker';distro='AGC-Worker'}
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid).Reason 'owner_sid_missing' 'missing configured SID rejected'
Write-Config @{accountSid=$fakeSid;distro='Other-Distro'}
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid).Reason 'owner_distro_mismatch' 'configured distro mismatch rejected'
'{ not json' | Set-Content -LiteralPath $configPath -Encoding UTF8
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid).Reason 'lifecycle_config_invalid' 'malformed configuration rejected'
(' ' * 70000) | Set-Content -LiteralPath $configPath -Encoding UTF8
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath $configPath -ExpectedSid $fakeSid).Reason 'lifecycle_config_invalid' 'oversized configuration rejected'
Assert-Equal (Test-AgcWorkerOwnerSidBinding -ConfigPath (Join-Path $configDir 'missing.json') -ExpectedSid $fakeSid).Reason 'lifecycle_config_unreadable' 'missing configuration rejected'
Write-Config @{accountSid=$fakeSid;accountName='agc-worker';distro='AGC-Worker'}
Assert-True (-not (Test-Path (Join-Path $configDir 'control/account.json'))) 'binding needs no worker control marker'

# --- Fast drain after a 10s cold launch: one process, final fresh proof ---
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_removes_running')
$script:Clock = $issued.AddSeconds(10)
$script:Waits = @()
$request = New-CycleRequest
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'graceful' 'fast drain outcome graceful'
Assert-True ($result.stopped -eq $true -and $result.verified -eq $true) 'fast drain verified'
$calls = Get-Calls
Assert-Equal (@($calls | Where-Object { $_ -match 'systemctl stop agc-worker.service' }).Count) 1 'one graceful stop launch'
Assert-Equal (@($calls | Where-Object { $_ -match '^--terminate AGC-Worker' }).Count) 0 'fast drain needs no termination'
Assert-True ($result.receipt.details.launchLatencyMs -ge 10000) 'launch latency recorded truthfully'
Assert-Equal $result.receipt.phase 'cycle' 'cycle receipt phase'
Assert-Equal $result.receipt.requestId $request.requestId 'cycle receipt request binding'
Assert-True ($result.receipt.details.gracefulDeadlineUtc -and $result.receipt.details.deadlineUtc) 'cycle deadlines recorded'
Assert-Equal $result.details.verification 'running-list' 'native verification marker recorded'
Assert-Equal $result.details.verifyComplete 'true' 'native verifyComplete marker recorded'
$cycleReceiptPath = Join-Path $data ($request.requestId + '.cycle.json')
Assert-True (Test-Path $cycleReceiptPath) 'cycle executor writes the local cycle receipt'
$primaryHash = (Get-FileHash -LiteralPath $cycleReceiptPath -Algorithm SHA256).Hash
$proven = $request

# --- Queued duplicate after success: no guest commands, receipt untouched ---
Reset-State -Running
Remove-Item -LiteralPath (Join-Path $state 'stop_removes_running') -ErrorAction SilentlyContinue
$script:Clock = $issued.AddSeconds(12)
$duplicate = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-True ($duplicate.reused -eq $true) 'completed cycle is reused by a queued duplicate'
Assert-True ($duplicate.outcome -in @('graceful','forced')) 'queued duplicate reports the proven outcome'
Assert-Equal (@(Get-Calls).Count) 0 'queued duplicate launches nothing'
Assert-Equal (Get-FileHash -LiteralPath $cycleReceiptPath -Algorithm SHA256).Hash $primaryHash 'queued duplicate never overwrites the proven receipt'

# --- Successful stop leaves the VM running: early exact-distro terminate ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(1)
$script:Waits = @()
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'forced' 'slow drain outcome forced'
Assert-True ($result.details.drained -eq $true) 'explicit stop exit 0 is recorded as drain success'
Assert-True ($result.details.earlyTerminate -eq $true) 'early terminate follows an explicit successful drain'
Assert-Equal (@($script:Waits).Count) 0 'early terminate does not burn the graceful window'
$calls = Get-Calls
Assert-Equal (@($calls | Where-Object { $_ -match '^--terminate AGC-Worker' }).Count) 1 'exactly one terminate'
Assert-True ($result.receipt.details.verificationFresh -eq $true) 'fresh verification recorded'

# --- Expired request: no effects, truthful unconfirmed ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(25)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'expired request unconfirmed'
Assert-Equal $result.reason 'cycle_deadline_exhausted' 'expired request reason'
Assert-Equal (@(Get-Calls).Count) 0 'expired request launches nothing'

# --- No budget left after cold launch: unconfirmed, never invented success ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(24.2)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'late launch unconfirmed'
Assert-Equal (@(Get-Calls).Count) 0 'late launch performs no effects'

# --- Foreign/restored gate: no effects ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(1)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck { $false } -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'foreign gate unconfirmed'
Assert-Equal $result.reason 'gate_request_mismatch' 'foreign gate reason'
Assert-Equal (@(Get-Calls).Count) 0 'foreign gate launches nothing'

# --- Request file replaced by a different cycle: revalidated before effects ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(1)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck { $false }
Assert-Equal $result.reason 'cycle_request_replaced' 'replaced request detected'
Assert-Equal (@(Get-Calls).Count) 0 'replaced request launches nothing'

# --- Simultaneous duplicate: a proven cycle is reused, never clobbered ---
Reset-State -Running
$lockPath = Join-Path $data ($proven.requestId + '.cycle.lock')
$lockStream = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $script:Clock = $issued.AddSeconds(12)
    $held = Invoke-AgcWorkerOwnerCycle -Request $proven -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
        -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
    Assert-True ($held.reused -eq $true) 'completed cycle reused even while another handle is open'
    Assert-Equal (@(Get-Calls).Count) 0 'reused cycle launches nothing with the lock held'
    Assert-Equal (Get-FileHash -LiteralPath $cycleReceiptPath -Algorithm SHA256).Hash $primaryHash 'reused cycle never clobbers the proven receipt'
} finally { $lockStream.Dispose() }

# Concurrent duplicate without a completed receipt: refuse, write nothing.
Reset-State -Running
$pending = New-CycleRequest
$pendingReceipt = Join-Path $data ($pending.requestId + '.cycle.json')
$lockStream = [IO.File]::Open((Join-Path $data ($pending.requestId + '.cycle.lock')), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $script:Clock = $issued.AddSeconds(1)
    $result = Invoke-AgcWorkerOwnerCycle -Request $pending -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
        -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
    Assert-Equal $result.outcome 'unconfirmed' 'duplicate executor unconfirmed'
    Assert-Equal $result.reason 'duplicate_executor' 'duplicate executor reason'
    Assert-True ($result.duplicate -eq $true) 'duplicate flagged for exit handling'
    Assert-Equal (@(Get-Calls).Count) 0 'duplicate executor launches nothing'
    Assert-True (-not (Test-Path $pendingReceipt)) 'duplicate executor writes no cycle receipt'
} finally { $lockStream.Dispose() }

# --- Gate closes between drain and termination: no late terminate ---
Reset-State -Running
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(1)
$gateChecks = 0
$GateCloses = { $script:gateChecks++; return ($script:gateChecks -lt 2) }
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateCloses -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'closure before terminate unconfirmed'
Assert-Equal $result.reason 'gate_request_mismatch' 'closure before terminate reason'
Assert-Equal (@(Get-Calls) | Where-Object { $_ -match '^--terminate' } | Measure-Object).Count 0 'no terminate after gate closure'

# --- Failed stop still waits to cutoff and terminates once ---
Reset-State -Running
$request = New-CycleRequest
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_fail')
$script:Clock = $issued.AddSeconds(2)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'forced' 'failed stop falls back to forced'
Assert-True ($result.details.drained -eq $false) 'failed stop not claimed as drained'

# --- Unverifiable running list: never claims stopped, still no late command ---
Reset-State -Running
$request = New-CycleRequest
'1' | Set-Content -LiteralPath (Join-Path $state 'list_fail')
$script:Clock = $issued.AddSeconds(1)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'unverifiable list unconfirmed'
Assert-True ($result.stopped -ne $true) 'unverifiable list never claims stopped'
Assert-Equal (@(Get-Calls) | Where-Object { $_ -match '^--terminate' } | Measure-Object).Count 0 'no terminate without initial evidence'

# --- Shared receipt data is flat (native CycleEvidence reads strings) ---
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_removes_running')
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(2)
$script:SharedData = $null
$OnComplete = { param($cycleReceipt) $script:SharedData = Get-AgcWorkerCycleSharedData -Receipt $cycleReceipt; return $true }
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk -OnComplete $OnComplete
Assert-True ($result.receipt.shared_receipt_written -eq $true) 'primary publishes the shared receipt while holding the lock'
Assert-Equal $script:SharedData.verification 'running-list' 'shared data carries the flat verification marker'
Assert-Equal $script:SharedData.verifyComplete 'true' 'shared data carries the flat verifyComplete marker'
Assert-True (@($script:SharedData.Values | Where-Object { $_ -match 'System.Collections.Hashtable' }).Count -eq 0) 'shared data has no nested details hashtable'
Assert-Equal $script:SharedData.distro 'AGC-Worker' 'shared data binds the exact distro'

# --- Reused receipt can still be published (hashtable shape, no nested details) ---
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_removes_running')
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(3)
$first = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
$script:Clock = $issued.AddSeconds(4)
$reused = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-True ($reused.reused -eq $true) 'reused receipt returned from disk'
Write-AgcWorkerStopReceipt -Data $reused.receipt -RequestId $request.requestId -Outcome $reused.receipt.outcome -Phase cycle | Out-Null
$reusedShared = Get-Content -LiteralPath $script:FakeReceipt -Raw | ConvertFrom-Json
Assert-Equal $reusedShared.phase 'cycle' 'reused receipt still publishes phase cycle'
Assert-Equal $reusedShared.data.verification 'running-list' 'reused receipt publishes flat verification evidence'

# --- No terminate room: skip the command but still perform a fresh final query ---
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_fail')
$request = New-CycleRequest -Graceful $issued.AddSeconds(1) -Terminate $issued.AddSeconds(2) -Deadline $issued.AddSeconds(3)
$script:Clock = $issued.AddSeconds(0)
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'skipped terminate stays unconfirmed while running'
Assert-Equal $result.reason 'terminate_deadline_exhausted' 'skipped terminate keeps a truthful reason'
Assert-Equal (@(Get-Calls) | Where-Object { $_ -match '^--terminate' } | Measure-Object).Count 0 'no terminate without verification room'
Assert-True ((@(Get-Calls) | Where-Object { $_ -match '^--list' }).Count -ge 2) 'final running-list query still happens'

# --- Setup delay is charged once: no early terminate after a failed drain ---
$t0 = [DateTimeOffset]::Parse('2026-10-23T00:00:00.000Z')
$arithmeticNow = { $t0.AddSeconds(8) }
Assert-Equal (Get-AgcWorkerCycleRemaining -Deadline $t0.AddSeconds(13) -Anchor 13000 -Now $arithmeticNow -ElapsedMilliseconds 8000) 5000 'setup time is not charged twice'
$rolledBackNow = { $t0.AddSeconds(2) }
Assert-Equal (Get-AgcWorkerCycleRemaining -Deadline $t0.AddSeconds(13) -Anchor 13000 -Now $rolledBackNow -ElapsedMilliseconds 8000) 5000 'clock rollback cannot extend the anchored budget'
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_fail')
$request = New-CycleRequest
$script:Clock = $issued.AddSeconds(8)
$script:Waits = @()
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'forced' 'setup delay still reaches forced termination'
$waited = [int64](($script:Waits | Measure-Object -Sum).Sum)
Assert-True ($waited -ge 4800 -and $waited -le 5200) 'failed drain still waits to the absolute graceful cutoff'
Assert-Equal (@(Get-Calls) | Where-Object { $_ -match '^--terminate' } | Measure-Object).Count 1 'terminate follows the full graceful wait'

# --- Future-dated request: only a small clock-skew tolerance is accepted ---
Reset-State -Running
$script:Clock = $issued
$futureIssued = $issued.AddSeconds(5)
$futureRequest = New-CycleRequest -Issued $futureIssued
$result = Invoke-AgcWorkerOwnerCycle -Request $futureRequest -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.outcome 'unconfirmed' 'future-dated request unconfirmed'
Assert-Equal $result.reason 'cycle_request_future' 'future-dated request reason'
Assert-Equal (@(Get-Calls).Count) 0 'future-dated request launches nothing'
$nearIssued = $issued.AddMilliseconds(200)
$nearRequest = New-CycleRequest -Issued $nearIssued
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_removes_running')
$result = Invoke-AgcWorkerOwnerCycle -Request $nearRequest -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-True ($result.reason -ne 'cycle_request_future') 'small clock skew is tolerated'

# --- Publication margin: no final query when it would cross the 24 s cutoff ---
Reset-State -Running
'1' | Set-Content -LiteralPath (Join-Path $state 'stop_fail')
$request = New-CycleRequest -Graceful $issued.AddSeconds(1) -Terminate $issued.AddSeconds(1.2) -Deadline $issued.AddSeconds(1.35)
$script:Clock = $issued
$result = Invoke-AgcWorkerOwnerCycle -Request $request -RequestHash 'f' -WslPath $fakeWsl -Distro 'AGC-Worker' `
    -DataPath $data -Now $Now -Wait $Wait -GateCheck $GateOk -RequestCheck $RequestOk
Assert-Equal $result.reason 'verification_deadline_exhausted' 'publication margin stops a late final query'
Assert-Equal (@(Get-Calls) | Where-Object { $_ -match '^--list' } | Measure-Object).Count 1 'only the initial list ran'
Assert-True ($result.details.publicationReserveMs -ge 300 -and $result.details.publicationReserveMs -le 500) 'publication margin recorded'

# --- Receipt writers accept the cycle phase ---
Remove-Item -LiteralPath $script:FakeReceipt -ErrorAction SilentlyContinue
$receipt = @{schema=1;owner='worker';requestId=('a' * 32);bootId='b1';phase='cycle';outcome='graceful';
    stopped=$true;verified=$true;scope='distro';source='owner';startedUtc=$issued.ToString('o');
    durationMs=1;accountName='host\agc-worker';accountSid='S-1-5-21-1';distro='AGC-Worker';details=@{reason='test'}}
$written = Write-AgcWorkerStopReceipt -Data $receipt -RequestId ('a' * 32) -Outcome graceful -Phase cycle
Assert-True ($written -eq $true) 'shared cycle receipt written'
$shared = Get-Content -LiteralPath $script:FakeReceipt -Raw | ConvertFrom-Json
Assert-Equal $shared.phase 'cycle' 'shared receipt carries cycle phase'
[void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $data -RequestId ('a' * 32) -Phase cycle)
Assert-True (Test-Path (Join-Path $data (('a' * 32) + '.cycle.json'))) 'local cycle receipt written'

Write-Host ("worker cycle tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
