# Local pure-logic tests for the worker lifecycle adapter.
# Run: pwsh -NoProfile -File ops/agc-worker/tests/WorkerLifecycle.Tests.ps1
# These tests use a fake common library and a fake wsl executable; they make no
# Windows, WSL, service or coordinator calls.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-worker-lifecycle-' + [Guid]::NewGuid().ToString('N'))
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
$global:FakeStartAllowed = $true
$global:FakeStop = $null
function Test-AgcStartAllowed { param([string]$Owner) return $global:FakeStartAllowed }
function Get-AgcStopRequest { param([string]$Owner) return $global:FakeStop }
function Invoke-AgcBoundedProcess {
    param([string]$FilePath,[string[]]$ArgumentList=@(),[int]$TimeoutMilliseconds=1000,[string]$StandardInput,[int]$MaxOutputBytes=65536,[switch]$RequireAdmission,[string]$Owner)
    if ($global:FakeSuppressAdmission) { return [pscustomobject]@{Started=$false;Suppressed=$true;ExitCode=0;TimedOut=$false;Stdout='';Stderr='';OutputTruncated=$false;DurationMs=0} }
    if ($global:FakeNotStarted) { return [pscustomobject]@{Started=$false;ExitCode=$null;TimedOut=$false;Stdout='';Stderr='';OutputTruncated=$false;DurationMs=0} }
    if ($global:FakeTruncated) { return [pscustomobject]@{Started=$true;ExitCode=0;TimedOut=$false;Stdout='';Stderr='';OutputTruncated=$true;DurationMs=0} }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $FilePath
    foreach ($argument in $ArgumentList) { $start.ArgumentList.Add($argument) }
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($start)
    if ($null -ne $StandardInput) { $process.StandardInput.Write($StandardInput) }
    $process.StandardInput.Close()
    $timedOut = -not $process.WaitForExit($TimeoutMilliseconds)
    if ($timedOut) { $process.Kill(); [void]$process.WaitForExit(1000) }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $exitCode = if ($timedOut) { $null } else { $process.ExitCode }
    $process.Dispose()
    return [pscustomobject]@{Started=$true;ExitCode=$exitCode;TimedOut=$timedOut;Stdout=$stdout;Stderr=$stderr;OutputTruncated=$false;DurationMs=0}
}
function Start-AgcAdmittedProcess { param([string]$FilePath,[string[]]$ArgumentList=@(),[string]$Owner='',[int]$TimeoutMilliseconds=2000,[switch]$PassThru) if ($global:FakeSuppressAdmission) { return $null }; return (New-Object Diagnostics.Process) }
function Write-AgcLifecycleLog { param([string]$Stream,[string]$Event,[hashtable]$Data=@{}) Add-Content -LiteralPath $script:FakeLog -Value ($Event) }
function Write-AgcStopReceipt { param([string]$Owner,[string]$RequestId,[string]$Outcome,[hashtable]$Data,[string]$Phase='stop') Add-Content -LiteralPath $script:FakeReceipt -Value ((@{requestId=$RequestId;outcome=$Outcome;phase=$Phase;data=$Data} | ConvertTo-Json -Compress -Depth 8)) }
'@ | Set-Content -LiteralPath $library -Encoding UTF8
$script:FakeLog = Join-Path $root 'lifecycle.log'
$script:FakeReceipt = Join-Path $root 'receipt.log'
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null

$fakeWsl = Join-Path $root 'fake-wsl.sh'
@'
#!/bin/sh
state="$FAKE_WSL_STATE"
printf '%s\n' "$*" >> "$state/calls"
if [ "$1" = "--list" ]; then
  if [ -f "$state/list_fail" ]; then exit 2; fi
  [ -f "$state/running" ] && echo AGC-Worker
  exit 0
fi
if [ "$1" = "--terminate" ]; then
  rm -f "$state/running"
  exit 0
fi
if [ "$1" = "-d" ]; then
  case "$*" in
    *"systemctl stop agc-worker.service"*)
      if [ -f "$state/graceful_works" ]; then rm -f "$state/running"; fi
      exit 0 ;;
  esac
fi
exit 0
'@ | Set-Content -LiteralPath $fakeWsl -Encoding UTF8
& chmod +x $fakeWsl
$state = Join-Path $root 'wslstate'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$env:FAKE_WSL_STATE = $state

. (Join-Path $PSScriptRoot '..\WorkerLifecycle.ps1') -LibraryPath $library -DataPath $data
Assert-True (Initialize-AgcWorkerLifecycle -Path $library) 'fake library loads'
Assert-True ($null -ne (Get-Command Invoke-AgcWorkerGracefulDrain)) 'adapter functions loaded'
$remaining = Get-AgcWorkerRemainingMilliseconds -StopRequest ([pscustomobject]@{DeadlineUtc=[DateTimeOffset]::UtcNow.AddSeconds(10).ToString('o')})
Assert-True ($remaining -gt 9000 -and $remaining -le 10000) 'remaining milliseconds from deadline'
Assert-Equal (Get-AgcWorkerRemainingMilliseconds -StopRequest ([pscustomobject]@{DeadlineUtc=[DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o')})) 25000 'remaining milliseconds clamped to 25s'

$global:FakeStartAllowed = $false
$gate = Get-AgcWorkerGate
Assert-True (-not $gate.Allowed) 'closed gate refuses start'
Assert-Equal $gate.Reason 'gate_closed' 'closed gate reason'
$global:FakeStartAllowed = $true
$stopObject = [pscustomobject]@{RequestId=('a' * 32);BootId='b20260101T000000Z';DeadlineUtc=[DateTimeOffset]::UtcNow.AddSeconds(12).ToString('o')}
$global:FakeStop = $stopObject
$gate = Get-AgcWorkerGate
Assert-True (-not $gate.Allowed) 'stop request refuses start'
Assert-Equal $gate.StopRequest.RequestId $stopObject.RequestId 'stop request exposed'

# Absent distro is verified stopped without issuing --terminate.
Remove-Item -LiteralPath (Join-Path $state 'running') -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $state 'calls') -ErrorAction SilentlyContinue
$result = Invoke-AgcWorkerScopedTerminate -RequestId ('a' * 32) -ExpectedSid 'S-1-5-21-1' -StopRequest $stopObject -WslPath $fakeWsl -DataPath $data
Assert-True ($result.termination_confirmed -eq $true) 'absent distro confirmed stopped'
$calls = Get-Content -LiteralPath (Join-Path $state 'calls')
Assert-True (($calls -join "`n") -notmatch '--terminate') 'absent distro never receives terminate'
Assert-True (Test-Path (Join-Path $data ('stop-' + ('a' * 32) + '.json'))) 'local stop receipt written'
Assert-True (Test-Path (Join-Path $data (('a' * 32) + '.terminate.json'))) 'phase-bound local receipt written'

# Running distro: scoped terminate, independent verification, forced outcome.
'1' | Set-Content -LiteralPath (Join-Path $state 'running')
Remove-Item -LiteralPath (Join-Path $state 'calls') -ErrorAction SilentlyContinue
$result = Invoke-AgcWorkerScopedTerminate -RequestId ('c' * 32) -ExpectedSid 'S-1-5-21-1' -StopRequest $stopObject -WslPath $fakeWsl -DataPath $data
Assert-True ($result.termination_confirmed -eq $true) 'running distro termination confirmed'
Assert-Equal $result.outcome 'forced_scoped' 'scoped terminate outcome'
Assert-True ((Get-Content -LiteralPath $script:FakeReceipt -Raw) -match 'phase.*terminate') 'phase forwarded to library receipt'
$calls = Get-Content -LiteralPath (Join-Path $state 'calls')
Assert-True (($calls -join "`n") -match '--terminate AGC-Worker') 'scoped terminate exact distro'
Assert-True (($calls -join "`n") -notmatch '-d AGC-Worker') 'fallback never uses -d launch path'

# Graceful drain stops the supervisor before the scoped fallback.
New-Item -ItemType Directory -Path $state -Force | Out-Null
'1' | Set-Content -LiteralPath (Join-Path $state 'running')
'1' | Set-Content -LiteralPath (Join-Path $state 'graceful_works')
Remove-Item -LiteralPath (Join-Path $state 'calls') -ErrorAction SilentlyContinue
$result = Invoke-AgcWorkerGracefulDrain -RequestId ('d' * 32) -ExpectedSid 'S-1-5-21-1' -StopRequest $stopObject -WslPath $fakeWsl -DataPath $data
Assert-True ($result.termination_confirmed -eq $true) 'graceful drain confirmed'
Assert-Equal $result.outcome 'graceful' 'graceful outcome'
Assert-True ((Get-Content -LiteralPath $script:FakeReceipt -Raw) -match 'phase.*stop') 'stop phase forwarded to library receipt'
$calls = @(Get-Content -LiteralPath (Join-Path $state 'calls'))
Assert-True (($calls -join "`n") -match 'systemctl stop agc-worker.service') 'graceful drain stops supervisor'
$firstLaunch = ($calls | Where-Object { $_ -match '^--list|^-d|^--terminate' } | Select-Object -First 1)
Assert-True ($firstLaunch -match '^--list') 'graceful drain observes running state first'

# Graceful failure falls back to exact-distro termination.
'1' | Set-Content -LiteralPath (Join-Path $state 'running')
Remove-Item -LiteralPath (Join-Path $state 'graceful_works') -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $state 'calls') -ErrorAction SilentlyContinue
$result = Invoke-AgcWorkerGracefulDrain -RequestId ('e' * 32) -ExpectedSid 'S-1-5-21-1' -StopRequest $stopObject -WslPath $fakeWsl -DataPath $data
Assert-True ($result.termination_confirmed -eq $true) 'fallback drain confirmed'
Assert-Equal $result.outcome 'forced_scoped' 'fallback drain outcome'
$calls = Get-Content -LiteralPath (Join-Path $state 'calls')
Assert-True (($calls -join "`n") -match '--terminate AGC-Worker') 'fallback uses exact distro termination'

# Missing library is fail-safe; no WSL command is attempted.
Remove-Item -LiteralPath (Join-Path $state 'calls') -ErrorAction SilentlyContinue
$missing = Join-Path $root 'missing.ps1'
. (Join-Path $PSScriptRoot '..\WorkerLifecycle.ps1') -LibraryPath $missing -DataPath $data
Assert-True (-not (Initialize-AgcWorkerLifecycle -Path $missing)) 'missing library fails closed'
$gate = Get-AgcWorkerGate
Assert-Equal $gate.Reason 'gate_unavailable' 'missing library gate reason'
$result = Invoke-AgcWorkerScopedTerminate -RequestId ('f' * 32) -ExpectedSid 'S-1-5-21-1' -WslPath $fakeWsl -DataPath $data
Assert-True ($result.termination_confirmed -ne $true) 'missing library never claims shutdown'
Assert-True (-not (Test-Path (Join-Path $state 'calls'))) 'missing library never launches WSL'

# Strict running-list evidence: an empty result needs Started/ExitCode0/not truncated.
'1' | Set-Content -LiteralPath (Join-Path $state 'list_fail')
$threw = $false
try { [void](Get-AgcWorkerRunningDistros -WslPath $fakeWsl -TimeoutMilliseconds 1000) } catch { $threw = $true }
Assert-True $threw 'non-zero running list is not absence'
Remove-Item -LiteralPath (Join-Path $state 'list_fail') -ErrorAction SilentlyContinue
$threw = $false
try { [void](Get-AgcWorkerRunningDistros -WslPath $fakeWsl -TimeoutMilliseconds 0) } catch { $threw = $true }
Assert-True $threw 'exhausted list timeout throws'
$global:FakeNotStarted = $true
$threw = $false
try { [void](Get-AgcWorkerRunningDistros -WslPath $fakeWsl -TimeoutMilliseconds 1000) } catch { $threw = $true }
Assert-True $threw 'not-started process is not absence'
$global:FakeNotStarted = $false
$global:FakeTruncated = $true
$threw = $false
try { [void](Get-AgcWorkerRunningDistros -WslPath $fakeWsl -TimeoutMilliseconds 1000) } catch { $threw = $true }
Assert-True $threw 'truncated output is not absence'
$global:FakeTruncated = $false

# Atomic admission refusal returns Suppressed without a process.
. $library
[void](Initialize-AgcWorkerLifecycle -Path $library)
$global:FakeSuppressAdmission = $true
$admitted = Invoke-AgcWorkerAdmittedWsl -Arguments @('--list','--running','--quiet') -TimeoutMilliseconds 1000 -WslPath $fakeWsl
Assert-True ($admitted.Started -eq $false -and $admitted.Suppressed -eq $true -and $admitted.ExitCode -eq 0) 'atomic admission refusal is suppressed result'
$global:FakeSuppressAdmission = $false
Assert-True (Test-AgcWorkerAdmissionApi) 'admission API detected'

# Static contract checks (source-level, not native qualification).
$workerDir = Resolve-Path (Join-Path $PSScriptRoot '..')
$start = Get-Content -LiteralPath (Join-Path $workerDir 'Start-Worker.ps1') -Raw
$stop = Get-Content -LiteralPath (Join-Path $workerDir 'Stop-Worker.ps1') -Raw
$dispatch = Get-Content -LiteralPath (Join-Path $workerDir 'Dispatch-Worker.ps1') -Raw
$owner = Get-Content -LiteralPath (Join-Path $workerDir 'worker-stop.ps1') -Raw
$installer = Get-Content -LiteralPath (Join-Path $workerDir 'install-linux.sh') -Raw
Assert-True ($start.IndexOf('Get-AgcWorkerGate') -lt $start.IndexOf('Start-AgcWorkerKeepAlive')) 'start gate precedes first launch'
Assert-True ($start -notmatch '(?m)^\s*&\s*wsl\.exe') 'start has no unbounded wsl invocation'
Assert-True ($stop -notmatch 'WaitForExit\(\s*\)') 'stop has no unbounded WaitForExit'
Assert-True ($stop -notmatch 'wsl\.exe\s+-d') 'stop never uses -d launch path'
Assert-True ($dispatch -match 'Read-BoundedRequest' -and $dispatch -match 'Get-AgcWorkerGate') 'dispatch bounds input and checks gate'
Assert-True ($dispatch -match 'Invoke-AgcWorkerAdmittedWsl' -and $dispatch -notmatch 'Invoke-AgcWorkerWsl') 'dispatch only uses admitted forwarding'
Assert-True ($start -match 'Invoke-AgcWorkerAdmittedWsl' -and $start -notmatch "Invoke-AgcWorkerWsl -Arguments @\('-d'") 'start ordinary launches use admitted forwarding'
Assert-True ($owner -match "stop.*terminate.*verify" -and $owner -match 'owner_token_required') 'owner helper has explicit phases and owner identity'
Assert-True ($installer -match 'managed-mode' -and $installer -match 'journald') 'installer writes managed marker and journald budget'
Assert-True ($installer -notmatch 'systemctl enable agc-worker.service') 'installer never enables the supervisor'

Write-Host ("worker lifecycle tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
