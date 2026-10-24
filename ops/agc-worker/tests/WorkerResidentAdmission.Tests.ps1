# Real AGCLifecycle resident readiness/admission regression (#212).
# Runs only when AGC_LIFECYCLE_REAL_LIBRARY points at a shared Lifecycle.ps1
# with the resident helpers. On Windows the real owner SID and real process
# probe are used; on Linux only the narrow process-owner token boundary is
# mocked (AgcProcessOwnerProbeOverride), while the real readiness writer,
# validator and Test-AgcStartAllowed admission code execute.
$ErrorActionPreference = 'Stop'
$library = $env:AGC_LIFECYCLE_REAL_LIBRARY
if ([string]::IsNullOrWhiteSpace($library) -or -not (Test-Path -LiteralPath $library -PathType Leaf)) {
    Write-Host 'worker resident admission tests: SKIP (AGC_LIFECYCLE_REAL_LIBRARY not set)'
    exit 0
}
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-worker-admission-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$env:AGC_LIFECYCLE_TEST_MODE = '1'
$env:AGC_LIFECYCLE_ROOT = $root
$env:AGC_LIFECYCLE_GATE_FILE = Join-Path $root 'gate.json'
. $library
try { Set-StrictMode -Off } catch { }
if (-not (Get-Command Write-AgcExecutorReadiness -CommandType Function -ErrorAction SilentlyContinue) -or
    -not (Get-Command Get-AgcExecutorReadiness -CommandType Function -ErrorAction SilentlyContinue) -or
    -not (Get-Command Test-AgcStartAllowed -CommandType Function -ErrorAction SilentlyContinue)) {
    Write-Host 'worker resident admission tests: SKIP (shared resident helpers not implemented)'
    Remove-Item Env:AGC_LIFECYCLE_TEST_MODE,Env:AGC_LIFECYCLE_ROOT,Env:AGC_LIFECYCLE_GATE_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}

# Real Windows owner SID when available; otherwise a fixed test SID with only
# the native process-token probe mocked. Never accept an empty SID.
$sid = ''
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -ne $identity -and $null -ne $identity.User) { $sid = $identity.User.Value }
} catch { }
if ([string]::IsNullOrEmpty($sid)) {
    $sid = 'S-1-5-21-1111-2222-3333-1001'
    $script:MockOwnerSid = $sid
    $script:AgcProcessOwnerProbeOverride = { param([int]$ProcessId) return [string]$script:MockOwnerSid }.GetNewClosure()
}
$now = [DateTimeOffset]::UtcNow
@{state='Ready';bootId='admission-boot';requestId='';deadlineUtc='';heartbeatUtc=$now.ToString('o');updatedUtc=$now.ToString('o');reason='admission-test'} |
    ConvertTo-Json | Set-Content -LiteralPath $env:AGC_LIFECYCLE_GATE_FILE -Encoding UTF8
New-Item -ItemType Directory -Path (Join-Path $root 'config') -Force | Out-Null
$configPath = Join-Path $root 'config/lifecycle.json'
function Save-OwnerConfig([string]$accountSid) {
    @{schema=1;owners=@{worker=@{enabled=$true;mode='task';distro='AGC-Worker';executorTask='AGC-Worker-OwnerExecutor';accountSid=$accountSid;accountName='agc-worker'}}} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
}
Save-OwnerConfig $sid
New-Item -ItemType Directory -Path (Join-Path $root 'receipts/worker') -Force | Out-Null
$process = Get-Process -Id $PID
$processStart = $process.StartTime.ToUniversalTime()

Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a missing executor readiness record'
$record = Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $processStart
Assert-True ($null -ne $record -and [string]$record.accountSid -eq $sid) 'readiness writer binds the exact owner SID'
$ready = Get-AgcExecutorReadiness -Owner 'worker'
Assert-True ($ready.Present -eq $true -and $ready.Ready -eq $true) 'fresh readiness validates'
Assert-True ([int]$ready.Pid -eq $PID) 'readiness binds the live process id'
Assert-True ([string]$ready.AccountSid -eq $sid) 'readiness binds the exact owner SID'
Assert-True (Test-AgcStartAllowed -Owner 'worker') 'resident admission allows guest launch after fresh readiness'

Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $processStart -HeartbeatUtc ([DateTime]::UtcNow.AddSeconds(5)) | Out-Null
Assert-True ((Get-AgcExecutorReadiness -Owner 'worker').Ready -ne $true) 'future heartbeat readiness is rejected'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a future heartbeat'

Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $processStart -HeartbeatUtc ([DateTime]::UtcNow.AddSeconds(-11)) | Out-Null
Assert-True ((Get-AgcExecutorReadiness -Owner 'worker').Ready -ne $true) 'stale heartbeat readiness is rejected'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a stale heartbeat'

Write-AgcExecutorReadiness -Owner 'worker' -ProcessId 999999 -ProcessStartUtc $processStart | Out-Null
Assert-True ((Get-AgcExecutorReadiness -Owner 'worker').Ready -ne $true) 'exited/reused PID readiness is rejected'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies an exited/reused PID'

Save-OwnerConfig 'S-1-5-21-1111-2222-3333-9999'
Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $processStart | Out-Null
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a foreign configured SID'
Save-OwnerConfig $sid
Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $processStart | Out-Null

Remove-AgcExecutorReadiness -Owner 'worker'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a removed readiness record'

Write-Host ("worker resident admission tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Env:AGC_LIFECYCLE_TEST_MODE,Env:AGC_LIFECYCLE_ROOT,Env:AGC_LIFECYCLE_GATE_FILE -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
