# Execution-level resident entrypoint regression (#212).
# Executes the production Invoke-AgcWorkerResidentExecutor core with the real
# worker cycle, a fake shared executor loop and fake WSL. Only the OS boundaries
# (owner identity and WSL process API) are mocked; no task launch is possible:
# a schtasks.exe shim on PATH records any call, and the fake loop fails if one
# occurs.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-resident-entry-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$workerRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
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
function Start-AgcAdmittedProcess { param([string]$FilePath,[string[]]$ArgumentList=@(),[string]$Owner='',[int]$TimeoutMilliseconds=2000,[switch]$PassThru) return (New-Object Diagnostics.Process) }
function Write-AgcLifecycleLog { param([string]$Stream,[string]$Event,[hashtable]$Data=@{}) }
function Write-AgcStopReceipt {
    param([string]$Owner,[string]$RequestId,[ValidateSet('graceful','forced','unconfirmed')][string]$Outcome,
          [hashtable]$Data,[ValidateSet('stop','terminate','verify','cycle')][string]$Phase='stop')
    Add-Content -LiteralPath $env:FAKE_SHARED -Value ((@{requestId=$RequestId;outcome=$Outcome;phase=$Phase;data=$Data} | ConvertTo-Json -Compress -Depth 8))
}
function Invoke-AgcExecutorLoop {
    param([string]$Owner, [scriptblock]$CycleInvoker)
    if (Test-Path -LiteralPath $env:FAKE_SCHEDULER_CALLED) { throw 'scheduler was invoked' }
    $script:LoopCalls = [int]$script:LoopCalls + 1
    $request = Get-Content -LiteralPath $env:FAKE_REQUEST -Raw | ConvertFrom-Json
    $cycle = & $CycleInvoker $request
    $script:LastOutcome = [string]$cycle.Outcome
    return [pscustomobject]@{ Reason='test-complete'; Cycles=1; LastRequestId=[string]$request.requestId; LastOutcome=[string]$cycle.Outcome }
}
'@ | Set-Content -LiteralPath $library -Encoding UTF8

$env:FAKE_STATE = Join-Path $root 'state'
$env:FAKE_SHARED = Join-Path $root 'shared.log'
$env:FAKE_GATE = Join-Path $root 'gate.json'
$env:FAKE_REQUEST = Join-Path $root 'request.json'
$env:FAKE_SCHEDULER_CALLED = Join-Path $root 'scheduler-called'
New-Item -ItemType Directory -Path $env:FAKE_STATE -Force | Out-Null
'1' | Set-Content -LiteralPath (Join-Path $env:FAKE_STATE 'running')
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$shim = Join-Path $bin 'schtasks.exe'
@'
#!/bin/sh
echo "$@" >> "$FAKE_SCHEDULER_CALLED"
exit 0
'@ | Set-Content -LiteralPath $shim -Encoding UTF8
& chmod +x $shim
$originalPath = $env:PATH
$env:PATH = $bin + [IO.Path]::PathSeparator + $originalPath

$now = [DateTimeOffset]::UtcNow
$request = [pscustomobject]@{schema=1;owner='worker';phase='cycle';requestId=('c' * 32);bootId='entry-boot';
    issuedUtc=$now.ToString('o');gracefulDeadlineUtc=$now.AddSeconds(13).ToString('o');
    terminateDeadlineUtc=$now.AddSeconds(21).ToString('o');deadlineUtc=$now.AddSeconds(24).ToString('o');
    coordinatorProcessId=4242;reason='entrypoint execution regression'}
$request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $env:FAKE_REQUEST -Encoding UTF8
@{requestId=$request.requestId;bootId=$request.bootId} | ConvertTo-Json | Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null

. (Join-Path $workerRoot 'WorkerLifecycle.ps1') -LibraryPath $library -DataPath $data
# Narrow OS-boundary mock: the real WindowsIdentity is unavailable on Linux.
# This is the same object shape the production helper returns on Windows.
function Get-AgcWorkerIdentity { return [pscustomobject]@{ Name = 'DESKTOP-TEST\agc-worker'; Sid = 'S-1-5-21-1001' } }
$script:LoopCalls = 0
$script:LastOutcome = ''
try {
    $exit = Invoke-AgcWorkerResidentExecutor -FromTask -DataPath $data -RequestPath $env:FAKE_REQUEST -Distro 'AGC-Worker'
    Assert-Equal $exit 0 'resident entrypoint core exits success'
    Assert-Equal $script:LoopCalls 1 'shared executor loop invoked exactly once'
    Assert-True ($script:LastOutcome -in @('graceful','forced')) 'entrypoint runs the real bounded cycle'
    Assert-True (-not (Test-Path -LiteralPath $env:FAKE_SCHEDULER_CALLED)) 'zero Scheduler invocations during the resident cycle'
    $calls = @(Get-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls'))
    Assert-True (($calls | Where-Object { $_ -match 'systemctl stop agc-worker.service' }).Count -eq 1) 'entrypoint performed one guest drain'
    Assert-Equal (@($calls | Where-Object { $_ -match '--terminate' }).Count) 0 'fast drain needed no termination'

    $script:LoopCalls = 0
    $exit = Invoke-AgcWorkerResidentExecutor -DataPath $data -RequestPath $env:FAKE_REQUEST -Distro 'AGC-Worker'
    Assert-Equal $exit 2 'entrypoint core refuses without -FromTask'
    Assert-Equal $script:LoopCalls 0 'refused entrypoint never enters the loop'
} finally {
    $env:PATH = $originalPath
    Remove-Item Env:FAKE_STATE,Env:FAKE_SHARED,Env:FAKE_GATE,Env:FAKE_REQUEST,Env:FAKE_SCHEDULER_CALLED -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ("worker resident entrypoint tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
if ($script:Failures -ne 0) { exit 1 }
