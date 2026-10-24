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
# AGC_OWNER_CYCLE_PROTOCOL=1
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

if (-not (Test-Path -LiteralPath $Lifecycle -PathType Leaf)) { exit 1 }
. $Lifecycle
. (Join-Path $Code 'WorkerLifecycle.ps1')
try { Set-StrictMode -Off } catch { }
exit (Invoke-AgcWorkerResidentExecutor -FromTask:$FromTask -DataPath $Data -RequestPath $RequestPath -Distro $Distro)
