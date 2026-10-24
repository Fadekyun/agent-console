# Real compatibility regression (#216): display Test-AgcOwnerCycleCapability
# against the actual worker resident entrypoint.
#
# The fixture contains verbatim extracted display functions. The pre-fix worker
# entrypoint (resident marker only) must be rejected; the corrected entrypoint
# (also declaring # AGC_OWNER_CYCLE_PROTOCOL=1) must be accepted. Task metadata
# is mocked; no real task, ACL or WSL operation is used.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-capability-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$workerRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$entrypoint = Join-Path $workerRoot 'worker-executor.ps1'
$fixture = Join-Path (Join-Path $PSScriptRoot 'fixtures') 'DisplayOwnerCycleCapability.ps1'
. $fixture

# Optional drift check against the real display installer when supplied.
if (-not [string]::IsNullOrWhiteSpace($env:AGC_DISPLAY_LIFECYCLE_INSTALLER) -and
    (Test-Path -LiteralPath $env:AGC_DISPLAY_LIFECYCLE_INSTALLER -PathType Leaf)) {
    $live = [IO.File]::ReadAllText($env:AGC_DISPLAY_LIFECYCLE_INSTALLER)
    $fixtureSource = [IO.File]::ReadAllText($fixture)
    $normalize = { param([string]$Text) ($Text -replace "`r", '').Trim() }
    foreach ($functionName in @('Get-AgcTaskActionTarget', 'Test-AgcOwnerCycleCapability')) {
        $pattern = '(?ms)^function ' + [regex]::Escape($functionName) + ' \{.*?^\}'
        $liveText = [regex]::Match($live, $pattern).Value
        $fixtureText = [regex]::Match($fixtureSource, $pattern).Value
        Assert-True (-not [string]::IsNullOrEmpty($liveText) -and (& $normalize $liveText) -eq (& $normalize $fixtureText)) ('extracted ' + $functionName + ' matches the supplied display installer')
    }
}

$sid = 'S-1-5-21-1111-2222-3333-1001'
function Get-ScheduledTask {
    param([string]$TaskName)
    return [pscustomobject]@{
        TaskName = $TaskName
        Actions = @([pscustomobject]@{
            Execute = 'powershell.exe'
            Arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $global:CapabilityScriptPath + '" -FromTask'
        })
        Principal = [pscustomobject]@{ UserId = $sid }
    }
}
function Invoke-Capability([string]$ScriptPath) {
    $global:CapabilityScriptPath = $ScriptPath
    return (Test-AgcOwnerCycleCapability -TaskName 'AGC-Worker-OwnerExecutor' -Owner 'worker' -ExpectedScript $ScriptPath -ExpectedSid $sid)
}

# The actual pre-fix helper content (resident marker only) is derived from the
# real entrypoint by removing the cycle marker; the corrected file must declare it.
$fixedText = [IO.File]::ReadAllText($entrypoint)
$oldPath = Join-Path $root 'worker-executor-old.ps1'
[IO.File]::WriteAllText($oldPath, ($fixedText -replace '(?m)^\s*#\s*AGC_OWNER_CYCLE_PROTOCOL=1\s*\r?\n', ''))
$global:CapabilityScriptPath = $entrypoint
$action = Get-AgcTaskActionTarget -TaskName 'AGC-Worker-OwnerExecutor'
Assert-True ($action.Ok -eq $true -and $action.Script -eq $entrypoint) 'real action-target parser extracts the worker entrypoint'
Assert-True ($action.Arguments -match '(?i)(^|\s)-FromTask(\s|$)') 'mocked action keeps the fixed -FromTask contract'

$old = Invoke-Capability $oldPath
Assert-Equal $old.Ok $false 'pre-fix entrypoint is rejected by the real display capability check'
Assert-True ($old.Detail -match 'AGC_OWNER_CYCLE_PROTOCOL=1') 'pre-fix rejection names the missing cycle protocol marker'

$fixed = Invoke-Capability $entrypoint
Assert-Equal $fixed.Ok $true 'corrected entrypoint is accepted by the real display capability check'
Assert-Equal $fixed.Script $entrypoint 'accepted capability returns the installed worker entrypoint'

Write-Host ("worker executor capability tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
