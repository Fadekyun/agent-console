# Optional cross-repository regression: real AGCLifecycle readiness/admission
# contract (#212). Runs only when AGC_LIFECYCLE_REAL_LIBRARY points at a shared
# Lifecycle.ps1 that implements the resident readiness helpers; otherwise this
# is a documented skip. No Windows service or WSL call is made.
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
    -not (Get-Command Get-AgcExecutorReadiness -CommandType Function -ErrorAction SilentlyContinue)) {
    Write-Host 'worker resident admission tests: SKIP (shared resident helpers not implemented)'
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$now = [DateTimeOffset]::UtcNow
@{state='Ready';bootId='admission-boot';requestId='';deadlineUtc='';heartbeatUtc=$now.ToString('o');updatedUtc=$now.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath $env:AGC_LIFECYCLE_GATE_FILE -Encoding UTF8
New-Item -ItemType Directory -Path (Join-Path $root 'config') -Force | Out-Null
@{schema=1;owners=@{worker=@{enabled=$true;mode='task';distro='AGC-Worker';executorTask='AGC-Worker-OwnerExecutor';accountSid=$sid;accountName='agc-worker'}}} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'config/lifecycle.json') -Encoding UTF8
New-Item -ItemType Directory -Path (Join-Path $root 'receipts/worker') -Force | Out-Null

Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a missing executor readiness record'
$process = Get-Process -Id $PID
$record = Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $process.StartTime.ToUniversalTime()
Assert-True ($null -ne $record) 'readiness writer returns the bound record'
$ready = Get-AgcExecutorReadiness -Owner 'worker'
Assert-True ($ready.Present -eq $true -and $ready.Fresh -eq $true) 'fresh readiness validates'
Assert-True ([int]$ready.Pid -eq $PID) 'readiness binds the live process id'
Assert-True ([string]$ready.AccountSid -eq $sid -or [string]::IsNullOrEmpty($sid)) 'readiness binds the owner SID'
Assert-True (Test-AgcStartAllowed -Owner 'worker') 'resident admission allows guest launch after fresh readiness'

Write-AgcExecutorReadiness -Owner 'worker' -ProcessId $PID -ProcessStartUtc $process.StartTime.ToUniversalTime() -HeartbeatUtc ([DateTime]::UtcNow.AddSeconds(5)) | Out-Null
Assert-True ((Get-AgcExecutorReadiness -Owner 'worker').Fresh -ne $true) 'future heartbeat is rejected'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a non-fresh readiness record'

Remove-AgcExecutorReadiness -Owner 'worker'
Assert-True (-not (Test-AgcStartAllowed -Owner 'worker')) 'resident admission denies a removed readiness record'

Write-Host ("worker resident admission tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Env:AGC_LIFECYCLE_TEST_MODE,Env:AGC_LIFECYCLE_ROOT,Env:AGC_LIFECYCLE_GATE_FILE -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
