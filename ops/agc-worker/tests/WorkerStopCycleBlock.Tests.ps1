# Bounded extracted-cycle-block regression for the worker owner entrypoint (#210).
# Run: pwsh -NoProfile -File ops/agc-worker/tests/WorkerStopCycleBlock.Tests.ps1
#
# This extracts the real `if ($phase -eq 'cycle') { ... }` block from
# worker-stop.ps1 and executes it against a fake shared library, fake gate,
# fake configuration and fake bounded-process API. It exists because the real
# entrypoint requires the Windows agc-worker token and the live AGCLifecycle
# ACLs; root's live Windows owner rehearsal remains the runtime proof.
# `exit N` is rewritten to `return N` so the host test process survives; the
# block is otherwise unmodified.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-stop-block-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$workerRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$helperText = Get-Content -LiteralPath (Join-Path $workerRoot 'worker-stop.ps1') -Raw
$startNeedle = 'if ($phase -eq ''cycle'') {'
$startIndex = $helperText.IndexOf($startNeedle)
Assert-True ($startIndex -ge 0) 'real cycle block found in worker-stop.ps1'
$endIndex = $helperText.IndexOf("`n`$deadline = ConvertTo-AgcWorkerTime", $startIndex)
Assert-True ($endIndex -gt $startIndex) 'cycle block end marker found'
$block = $helperText.Substring($startIndex, $endIndex - $startIndex)
$block = $block -replace '\bexit 0\b', 'return 0' -replace '\bexit 1\b', 'return 1'
Assert-True ($block -match 'Test-AgcWorkerOwnerSidBinding') 'extracted block binds the owner SID'
Assert-True ($block -notmatch 'account\.json') 'extracted block never reads the admin-only marker'
$cycleCode = [scriptblock]::Create($block)

$fakeSid = 'S-1-5-21-5555-6666-7777-1001'
$fakeLib = Join-Path $root 'Lifecycle.ps1'
@'
function Get-AgcLifecycleConfigPath { return $env:FAKE_CONFIG }
function Get-AgcStopRequest { param([string]$Owner) if (Test-Path -LiteralPath $env:FAKE_GATE) { return (Get-Content -LiteralPath $env:FAKE_GATE -Raw | ConvertFrom-Json) }; return $null }
function Test-AgcStartAllowed { param([string]$Owner) return $true }
function Invoke-AgcBoundedProcess {
    param([string]$FilePath,[string[]]$ArgumentList=@(),[int]$TimeoutMilliseconds=1000,[string]$StandardInput,[int]$MaxOutputBytes=65536,[switch]$RequireAdmission,[string]$Owner)
    Add-Content -LiteralPath (Join-Path $env:FAKE_STATE 'calls') -Value ($FilePath + '|' + ($ArgumentList -join ' '))
    $running = Join-Path $env:FAKE_STATE 'running'
    if ($ArgumentList -contains '--list') {
        $stdout = if (Test-Path -LiteralPath $running) { "AGC-Worker`n" } else { '' }
        return [pscustomobject]@{Started=$true;ExitCode=0;TimedOut=$false;Stdout=$stdout;Stderr='';OutputTruncated=$false;DurationMs=0}
    }
    if ($ArgumentList -contains '--terminate') { Remove-Item -LiteralPath $running -ErrorAction SilentlyContinue }
    if ($ArgumentList -contains '-d') { Remove-Item -LiteralPath $running -ErrorAction SilentlyContinue }
    return [pscustomobject]@{Started=$true;ExitCode=0;TimedOut=$false;Stdout='';Stderr='';OutputTruncated=$false;DurationMs=0}
}
function Write-AgcLifecycleLog { param([string]$Stream,[string]$Event,[hashtable]$Data=@{}) }
function Write-AgcStopReceipt {
    param([string]$Owner,[string]$RequestId,[ValidateSet('graceful','forced','unconfirmed')][string]$Outcome,
          [hashtable]$Data,[ValidateSet('stop','terminate','verify','cycle')][string]$Phase='stop')
    Add-Content -LiteralPath $env:FAKE_RECEIPTS -Value ((@{requestId=$RequestId;outcome=$Outcome;phase=$Phase;data=$Data} | ConvertTo-Json -Compress -Depth 8))
}
'@ | Set-Content -LiteralPath $fakeLib -Encoding UTF8

$env:FAKE_STATE = Join-Path $root 'state'
$env:FAKE_RECEIPTS = Join-Path $root 'shared-receipts.log'
$env:FAKE_CONFIG = Join-Path $root 'lifecycle.json'
$env:FAKE_GATE = Join-Path $root 'gate.json'
New-Item -ItemType Directory -Path $env:FAKE_STATE -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $root 'data') -Force | Out-Null

# Fake library replacement: the cycle function in WorkerLifecycle.ps1 resolves
# Get-AgcStopRequest directly, so the adapter must load this fake library.
. (Join-Path $workerRoot 'WorkerLifecycle.ps1') -LibraryPath $fakeLib -DataPath (Join-Path $root 'data')
[void](Initialize-AgcWorkerLifecycle -Path $fakeLib)

$script:UnconfirmedReason = $null
function Write-Unconfirmed {
    param([string]$RequestId, [string]$BootId, [string]$Phase, [string]$Reason)
    $script:UnconfirmedReason = $Reason
    throw [OperationCanceledException]::new('unconfirmed')
}

function Reset-Case([string]$Id, [string]$BootId, [string]$Sid) {
    $caseRoot = Join-Path $root ('case-' + [Guid]::NewGuid().ToString('N'))
    $caseData = Join-Path $caseRoot 'data'
    New-Item -ItemType Directory -Path $caseData -Force | Out-Null
    New-Item -ItemType Directory -Path $env:FAKE_STATE -Force | Out-Null
    Remove-Item -LiteralPath (Join-Path $env:FAKE_STATE 'calls') -ErrorAction SilentlyContinue
    '1' | Set-Content -LiteralPath (Join-Path $env:FAKE_STATE 'running')
    Remove-Item -LiteralPath $env:FAKE_RECEIPTS -ErrorAction SilentlyContinue
    @{schema=1;owners=@{worker=@{accountSid=$Sid;accountName='agc-worker';distro='AGC-Worker'}}} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $env:FAKE_CONFIG -Encoding UTF8
    @{requestId=$Id;bootId=$BootId} | ConvertTo-Json | Set-Content -LiteralPath $env:FAKE_GATE -Encoding UTF8
    $script:UnconfirmedReason = $null
    return [pscustomobject]@{Data=$caseData;Id=$Id;BootId=$BootId}
}

function New-Request([string]$Id, [string]$BootId) {
    $now = [DateTimeOffset]::UtcNow
    return [pscustomobject]@{schema=1;owner='worker';phase='cycle';requestId=$Id;bootId=$BootId;
        issuedUtc=$now.ToString('o');gracefulDeadlineUtc=$now.AddSeconds(13).ToString('o');
        terminateDeadlineUtc=$now.AddSeconds(21).ToString('o');deadlineUtc=$now.AddSeconds(24).ToString('o');
        coordinatorProcessId=4242;reason='entrypoint extraction regression'}
}
function Invoke-Block([object]$Case, [string]$Sid) {
    $phase = 'cycle'
    $request = New-Request $Case.Id $Case.BootId
    $requestId = $Case.Id
    $bootId = $Case.BootId
    $sid = $Sid
    $Data = $Case.Data
    $Distro = 'AGC-Worker'
    $RequestFile = Join-Path $root 'requests/worker/current.json'
    New-Item -ItemType Directory -Path (Split-Path $RequestFile) -Force | Out-Null
    $request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $RequestFile -Encoding UTF8
    $requestHash = (Get-FileHash -LiteralPath $RequestFile -Algorithm SHA256).Hash.ToLowerInvariant()
    try { return (. $cycleCode) } catch { $script:BlockError = $_.Exception.Message; return 1 }
}
function Get-Calls {
    $path = Join-Path $env:FAKE_STATE 'calls'
    if (Test-Path -LiteralPath $path) { return @(Get-Content -LiteralPath $path) }
    return @()
}
function Get-LocalReceipt([string]$Data, [string]$Id) {
    $path = Join-Path $Data ($Id + '.cycle.json')
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
}

# 1) Matching configured SID runs one cycle and publishes phase=cycle.
$case = Reset-Case ('a' * 32) 'fixture-boot' $fakeSid
$exit = Invoke-Block $case $fakeSid
Assert-Equal $exit 0 'matching lifecycle SID reaches a verified cycle'
Assert-Equal $script:UnconfirmedReason $null 'no unconfirmed refusal for a matching SID'
$local = Get-LocalReceipt $case.Data $case.Id
Assert-True ($null -ne $local -and $local.phase -eq 'cycle' -and $local.verified -eq $true) 'entrypoint wrote a verified cycle receipt'
Assert-Equal $local.requestId $case.Id 'receipt binds the exact request'
Assert-Equal $local.bootId $case.BootId 'receipt binds the exact boot'
Assert-Equal $local.distro 'AGC-Worker' 'receipt binds the exact distro'
$shared = Get-Content -LiteralPath $env:FAKE_RECEIPTS -Raw | ConvertFrom-Json
Assert-Equal $shared.phase 'cycle' 'shared receipt phase cycle'
Assert-Equal $shared.data.verification 'running-list' 'shared receipt carries flat verification evidence'
Assert-Equal $shared.data.verifyComplete 'true' 'shared receipt carries flat verifyComplete evidence'
$calls = Get-Calls
Assert-Equal (@($calls | Where-Object { $_ -match '--list' }).Count) 2 'list before and after guest stop'
Assert-Equal (@($calls | Where-Object { $_ -match 'systemctl stop agc-worker\.service' }).Count) 1 'one guest stop'
Assert-Equal (@($calls | Where-Object { $_ -match '--terminate' }).Count) 0 'fast drain needs no terminate'

# 2) Foreign configured SID refuses before any effect.
$case = Reset-Case ('b' * 32) 'fixture-boot' 'S-1-5-21-5555-6666-7777-1002'
$exit = Invoke-Block $case $fakeSid
Assert-Equal $exit 1 'foreign lifecycle SID exits non-zero'
Assert-Equal $script:UnconfirmedReason 'owner_sid_mismatch' 'foreign lifecycle SID refuses with owner_sid_mismatch'
Assert-Equal (@(Get-Calls).Count) 0 'foreign SID launches nothing'

# 3) Unreadable configuration refuses before any effect.
$case = Reset-Case ('c' * 32) 'fixture-boot' $fakeSid
Remove-Item -LiteralPath $env:FAKE_CONFIG -Force
$exit = Invoke-Block $case $fakeSid
Assert-Equal $exit 1 'unreadable lifecycle config exits non-zero'
Assert-Equal $script:UnconfirmedReason 'lifecycle_config_unreadable' 'unreadable config reason'
Assert-Equal (@(Get-Calls).Count) 0 'unreadable config launches nothing'

# 4) Concurrent duplicate refuses without writing a receipt or running WSL.
$case = Reset-Case ('d' * 32) 'fixture-boot' $fakeSid
$lock = [IO.File]::Open((Join-Path $case.Data ($case.Id + '.cycle.lock')), 'OpenOrCreate', 'ReadWrite', 'None')
try { $exit = Invoke-Block $case $fakeSid } finally { $lock.Dispose() }
Assert-Equal $exit 1 'concurrent duplicate exits non-zero'
Assert-Equal (@(Get-Calls).Count) 0 'concurrent duplicate launches nothing'
Assert-True (-not (Test-Path -LiteralPath $env:FAKE_RECEIPTS)) 'concurrent duplicate writes no shared receipt'

# 5) Completed cycle is reused: exit 0, no guest commands, shared repair only.
$case = Reset-Case ('e' * 32) 'fixture-boot' $fakeSid
@{schema=1;owner='worker';requestId=$case.Id;bootId=$case.BootId;phase='cycle';outcome='graceful';
    stopped=$true;verified=$true;scope='distro';source='owner';startedUtc=[DateTimeOffset]::UtcNow.ToString('o');
    finishedUtc=[DateTimeOffset]::UtcNow.ToString('o');durationMs=5;accountName='TEST\agc-worker';
    accountSid=$fakeSid;distro='AGC-Worker';
    details=@{phase='cycle';reason='drained_and_absent';verification='running-list';verifyComplete='true'}} |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $case.Data ($case.Id + '.cycle.json')) -Encoding UTF8
$exit = Invoke-Block $case $fakeSid
Assert-Equal $exit 0 'reused cycle exits zero'
Assert-Equal (@(Get-Calls).Count) 0 'reused cycle launches nothing'
$shared = Get-Content -LiteralPath $env:FAKE_RECEIPTS -Raw | ConvertFrom-Json
Assert-Equal $shared.phase 'cycle' 'reused cycle republishes the proven phase'
Assert-Equal $shared.data.verification 'running-list' 'reused cycle republishes flat evidence'

Write-Host ("worker stop cycle block tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
