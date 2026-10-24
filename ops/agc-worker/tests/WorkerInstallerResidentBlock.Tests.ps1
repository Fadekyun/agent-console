# Execution-level regression for the installer resident-executor block (#214).
# Extracts the real resident block from Install-LifecycleStopTask.ps1 and runs it
# with mocked ScheduledTasks cmdlets. The mocks behave like the Windows cmdlets:
# every -TaskName must be the exact fixed task-name STRING. The pre-fix defect
# overwrote $ExecutorTask with the CIM task object, so the first
# Disable/Enable-ScheduledTask receivd an object and failed like the live install.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-installer-resident-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
function Assert-Equal($Actual, $Expected, [string]$Name) {
    Assert-True ($Actual -eq $Expected) "$Name (expected=$Expected actual=$Actual)"
}

$installerPath = Join-Path (Join-Path $PSScriptRoot '..') 'Install-LifecycleStopTask.ps1'
$source = Get-Content -LiteralPath $installerPath -Raw
$start = $source.IndexOf('$executorReady = $null')
$end = $source.IndexOf('$receipt = @{')
Assert-True ($start -ge 0 -and $end -gt $start) 'resident installer block found'
$block = $source.Substring($start, $end - $start)
Assert-True ($block -match 'Register-ScheduledTask -TaskName \$ExecutorTask') 'block registers the fixed task name constant'
$receiptStart = $source.IndexOf('$receipt = @{')
$receiptEnd = $source.IndexOf('Save-Json', $receiptStart)
Assert-True ($receiptStart -gt 0 -and $receiptEnd -gt $receiptStart) 'receipt assignment found'
$receiptBlock = $source.Substring($receiptStart, $receiptEnd - $receiptStart)

$fixedTask = 'AGC-Worker-OwnerExecutor'

function Invoke-ResidentBlock([bool]$Defer) {
    $case = Join-Path $root ('case-' + [Guid]::NewGuid().ToString('N'))
    $lifecycle = Join-Path $case 'AGCLifecycle'
    $owners = Join-Path $lifecycle 'owners'
    $control = Join-Path $case 'control'
    $sourceDir = Join-Path $case 'source'
    New-Item -ItemType Directory -Path $owners, $control, $sourceDir, (Join-Path $lifecycle 'receipts/worker') -Force | Out-Null
    @'
# AGC_OWNER_RESIDENT_EXECUTOR=1
# fake resident entrypoint for the installer block regression
'@ | Set-Content -LiteralPath (Join-Path $sourceDir 'worker-executor.ps1') -Encoding UTF8
    # Immediate activation needs the shared readiness helper.
    @'
function Get-AgcExecutorReadiness {
    param([string]$Owner)
    return [pscustomobject]@{ Present=$true; Fresh=$true; Ready=$true; Protocol='cycle'; AccountSid=$global:ResidentTestSid; Pid=$PID; ProcessStartUtc=(Get-Process -Id $PID).StartTime.ToUniversalTime() }
}
'@ | Set-Content -LiteralPath (Join-Path $lifecycle 'Lifecycle.ps1') -Encoding UTF8

    $script:TaskCalls = [Collections.Generic.List[object]]::new()
    $script:Registered = $false
    $script:Settings = $null
    $global:ResidentTestSid = 'S-1-5-21-1111-2222-3333-1001'
    function New-ScheduledTaskTrigger { param([switch]$AtStartup, [switch]$AtLogOn) return [pscustomobject]@{Kind=($(if($AtStartup){'startup'}else{'logon'}))} }
    function New-ScheduledTaskSettingsSet { param([switch]$Hidden,[object]$ExecutionTimeLimit,[object]$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$WakeToRun,[switch]$StartWhenAvailable,[int]$RestartCount,[object]$RestartInterval)
        $script:Settings = [pscustomobject]@{Enabled=$true;Hidden=$Hidden;ExecutionTimeLimit=$ExecutionTimeLimit;MultipleInstances=$MultipleInstances}
        return $script:Settings }
    function New-ScheduledTaskAction { param([string]$Execute,[string]$Argument) return [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
    function Register-ScheduledTask { param([string]$TaskName,[object]$Action,[object[]]$Trigger,[object]$Settings,[string]$User,[string]$Password,[string]$RunLevel,[switch]$Force)
        $script:TaskCalls.Add([pscustomobject]@{Operation='Register';TaskName=$TaskName;SettingsEnabled=$Settings.Enabled})
        $script:Registered = $true
        return [pscustomobject]@{TaskName=$TaskName} }
    $script:TaskState = 'Ready'
    function Get-ScheduledTask { param([string]$TaskName)
        if (-not $script:Registered) { return $null }
        return [pscustomobject]@{TaskName=$TaskName;Principal=[pscustomobject]@{UserId='DESKTOP-TEST\agc-worker';RunLevel='Limited'};State=$script:TaskState} }
    function Disable-ScheduledTask { param([object]$TaskName)
        if ($TaskName -isnot [string]) { throw 'Disable-ScheduledTask: TaskName is not a string (live 0x80070002 equivalent)' }
        $script:TaskState = 'Disabled'
        $script:TaskCalls.Add([pscustomobject]@{Operation='Disable';TaskName=$TaskName}) }
    function Enable-ScheduledTask { param([object]$TaskName)
        if ($TaskName -isnot [string]) { throw 'Enable-ScheduledTask: TaskName is not a string' }
        $script:TaskState = 'Ready'
        $script:TaskCalls.Add([pscustomobject]@{Operation='Enable';TaskName=$TaskName}) }
    function Start-ScheduledTask { param([object]$TaskName)
        if ($TaskName -isnot [string]) { throw 'Start-ScheduledTask: TaskName is not a string' }
        $script:TaskCalls.Add([pscustomobject]@{Operation='Start';TaskName=$TaskName}) }
    function Export-ScheduledTask { param([Parameter(ValueFromPipeline = $true)][object]$Task) process { $script:ExportSeen = $Task; return ('<Task Name="' + [string]$Task.TaskName + '"/>') } }
    # Cross-platform test-only icacls: no real ACLs are touched.
    function icacls.exe { $global:LASTEXITCODE = 0 }
    function Protect-OwnerScript { param([string]$Path) }
    function Hash { param([string]$Path) return 'hash' }
    function Start-Sleep { param([int]$Milliseconds) }

    $EnableOwnerExecutor = $true
    $DeferExecutorStart = $Defer
    $ExecutorTask = $fixedTask
    $ExecutorScript = Join-Path $owners 'worker-executor.ps1'
    $ExecutorArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + $ExecutorScript + ' -FromTask'
    $SourceDirectory = $sourceDir
    $Control = $control
    $LifecycleRoot = $lifecycle
    $OwnerRoot = $owners
    $WorkerName = 'agc-worker'
    $enrolled = [pscustomobject]@{ sid = $global:ResidentTestSid }
    $credential = [pscredential]::new('DESKTOP-TEST\agc-worker', (ConvertTo-SecureString 'unused' -AsPlainText -Force))
    $executorReady = $null
    $executorTaskXml = ''
    $executorState = ''
    $errorMessage = ''
    try {
        . ([scriptblock]::Create($block))
        # Execute the actual receipt assignment (before persistence) so the
        # exact fixed task-name metadata is checked, not only a variable.
        $taskSid = $global:ResidentTestSid
        $principal = [pscustomobject]@{ UserId = 'DESKTOP-TEST\agc-worker'; RunLevel = 'Limited' }
        $actionMatchesFixed = $true
        $cycleProtocolSupported = $true
        $OwnerScript = Join-Path $owners 'worker-stop.ps1'
        $Code = Join-Path $case 'code'
        $TaskName = 'AGC-Worker-LifecycleStop'
        $taskXml = '<Task Name="AGC-Worker-LifecycleStop"/>'
        . ([scriptblock]::Create($receiptBlock))
    } catch { $errorMessage = $_.Exception.Message }
    return [pscustomobject]@{
        Error = $errorMessage
        NameAfter = $ExecutorTask
        State = $executorState
        Ready = $executorReady
        TaskXml = $executorTaskXml
        Calls = @($script:TaskCalls)
        Receipt = $receipt
        RegisteredEnabled = $(if ($null -ne $script:Settings) { [bool]$script:Settings.Enabled } else { $null })
    }
}

# Deferred first rollout: register disabled, no enable/start, exact string names.
$deferred = Invoke-ResidentBlock -Defer $true
Assert-Equal $deferred.Error '' 'deferred block executes without a cmdlet type error'
Assert-Equal $deferred.NameAfter $fixedTask 'fixed task-name constant survives the resident block'
Assert-Equal $deferred.State 'registered-disabled' 'deferred block reports registered-disabled'
Assert-Equal $deferred.RegisteredEnabled $false 'deferred task is disabled in its initial settings'
$deferredOps = @($deferred.Calls | ForEach-Object { $_.Operation })
Assert-True ($deferredOps -contains 'Register' -and $deferredOps -contains 'Disable') 'deferred block registers and confirms disabled'
Assert-True ($deferredOps -notcontains 'Enable' -and $deferredOps -notcontains 'Start') 'deferred block never enables or starts'
foreach ($call in $deferred.Calls) {
    Assert-True ($call.TaskName -is [string] -and [string]$call.TaskName -eq $fixedTask) ('deferred ' + $call.Operation + ' uses the exact fixed task name')
}
Assert-True ($deferred.TaskXml -match [regex]::Escape($fixedTask)) 'deferred task XML retains the fixed task name'

# Immediate activation: enable, start, fresh readiness, exact string names.
$immediate = Invoke-ResidentBlock -Defer $false
Assert-Equal $immediate.Error '' 'immediate block executes without a cmdlet type error'
Assert-Equal $immediate.NameAfter $fixedTask 'fixed task-name constant survives immediate activation'
Assert-Equal $immediate.State 'ready' 'immediate block reports ready'
Assert-Equal $immediate.RegisteredEnabled $true 'immediate task is enabled in its initial settings'
$immediateOps = @($immediate.Calls | ForEach-Object { $_.Operation })
Assert-True ($immediateOps -contains 'Register' -and $immediateOps -contains 'Enable' -and $immediateOps -contains 'Start') 'immediate block enables and starts'
Assert-True ($immediateOps -notcontains 'Disable') 'immediate block does not disable the task'
foreach ($call in $immediate.Calls) {
    Assert-True ($call.TaskName -is [string] -and [string]$call.TaskName -eq $fixedTask) ('immediate ' + $call.Operation + ' uses the exact fixed task name')
}
Assert-True ($null -ne $immediate.Ready -and $immediate.Ready.Ready -eq $true) 'immediate block verifies readiness'
Assert-True ($immediate.TaskXml -match [regex]::Escape($fixedTask)) 'immediate task XML retains the fixed task name'

# The actual receipt assignment ran after the resident block.
Assert-Equal $deferred.Receipt.owner_executor_task $fixedTask 'deferred receipt records the exact fixed task name'
Assert-Equal $deferred.Receipt.owner_executor_state 'registered-disabled' 'deferred receipt records registered-disabled'
Assert-Equal $immediate.Receipt.owner_executor_task $fixedTask 'immediate receipt records the exact fixed task name'
Assert-Equal $immediate.Receipt.owner_executor_state 'ready' 'immediate receipt records ready'
Assert-True ($deferred.Receipt.owner_executor_task -is [string] -and $immediate.Receipt.owner_executor_task -is [string]) 'receipt task metadata remains a string'

Write-Host ("worker installer resident tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
