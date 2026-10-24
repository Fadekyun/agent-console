# Resident worker owner executor (issue #212).
# Installed at C:\ProgramData\AGCLifecycle\owners\worker-executor.ps1 and started
# at boot by the fixed AGC-Worker-OwnerExecutor task under agc-worker. It is an
# independent whole-boot process: it never runs worker jobs, never keeps WSL
# alive and never launches a task or logs on another owner.
#
# The shared AGCLifecycle loop owns the single-instance guard, shutdown
# priority, readiness record and idle polling. This entrypoint only supplies the
# existing bounded worker cycle as the CycleInvoker.
#Requires -Version 5.1
# AGC_OWNER_RESIDENT_EXECUTOR=1
[CmdletBinding()]
param([switch]$FromTask)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Base = 'C:\ProgramData\AGC-Worker'
$Data = Join-Path $Base 'data'
$Code = Join-Path $Base 'code'
$LifecycleRoot = 'C:\ProgramData\AGCLifecycle'
$Lifecycle = Join-Path $LifecycleRoot 'Lifecycle.ps1'
$RequestPath = Join-Path (Join-Path $LifecycleRoot 'requests\worker') 'current.json'
$Distro = 'AGC-Worker'

if (-not $FromTask) { exit 2 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity -or $identity.Name -notmatch '\\agc-worker$' -or $identity.User.Value -eq 'S-1-5-18') {
    exit 2
}
if (-not (Test-Path -LiteralPath $Lifecycle -PathType Leaf)) { exit 1 }
. $Lifecycle
. (Join-Path $Code 'WorkerLifecycle.ps1')
try { Set-StrictMode -Off } catch { }
if (-not (Initialize-AgcWorkerLifecycle)) { exit 1 }
if (-not (Test-AgcWorkerAdmissionApi)) { exit 1 }

$cycleInvoker = {
    param($request)
    return (Invoke-AgcWorkerOwnerCycleResult -Request $request -RequestPath $RequestPath `
        -WslPath 'wsl.exe' -Distro $Distro -DataPath $Data)
}

try {
    $result = Invoke-AgcExecutorLoop -Owner 'worker' -CycleInvoker $cycleInvoker
} catch {
    try { Write-AgcLifecycleLog -Stream 'worker' -Event 'executor.exit' -Level 'error' -Data @{ reason = 'exception'; error = $_.Exception.GetType().Name } } catch { }
    exit 1
}
if ($null -eq $result) { exit 1 }
try { Write-AgcLifecycleLog -Stream 'worker' -Event 'executor.exit' -Data @{ reason = [string]$result.Reason } } catch { }
switch ([string]$result.Reason) {
    'fatal' { exit 1 }
    default { exit 0 }
}
