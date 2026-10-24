#Requires -RunAsAdministrator
# Install/update the persistent fixed worker lifecycle stop task and, with
# -EnableOwnerExecutor, the independent resident AGC-Worker-OwnerExecutor task.
# Preserves the existing agc-worker account, password principal and DPAPI
# credential; never disables the account, changes its password or exports it.
[CmdletBinding()]
param([string]$SourceDirectory = $PSScriptRoot, [switch]$EnableOwnerExecutor, [switch]$DeferExecutorStart)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($DeferExecutorStart -and -not $EnableOwnerExecutor) { throw 'DeferExecutorStart requires EnableOwnerExecutor' }
$WorkerName = 'agc-worker'
$Base = 'C:\ProgramData\AGC-Worker'
$Control = Join-Path $Base 'control'
$Code = Join-Path $Base 'code'
$Marker = Join-Path $Control 'account.json'
$CredentialFile = Join-Path $Control 'credential.dpapi'
$LifecycleRoot = 'C:\ProgramData\AGCLifecycle'
$OwnerRoot = Join-Path $LifecycleRoot 'owners'
$OwnerScript = Join-Path $OwnerRoot 'worker-stop.ps1'
$TaskName = 'AGC-Worker-LifecycleStop'
$ExpectedArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGCLifecycle\owners\worker-stop.ps1 -FromTask'
$ExecutorTask = 'AGC-Worker-OwnerExecutor'
$ExecutorScript = Join-Path $OwnerRoot 'worker-executor.ps1'
$ExecutorArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGCLifecycle\owners\worker-executor.ps1 -FromTask'

function Save-Json([string]$File, $Value) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath "$File.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$File.tmp" -Destination $File -Force
}
function Protect-Directory([string]$Directory, [string]$WorkerSid) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    }
    if ($WorkerSid) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($WorkerSid)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')))
    }
    Set-Acl -LiteralPath $Directory -AclObject $acl
}
function Protect-OwnerScript([string]$Path) {
    # Owner entrypoints are admin/SYSTEM-owned, worker read-only. The worker may
    # read them but must never be able to replace its own stop/executor helper.
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($rule in @($acl.Access)) { $acl.RemoveAccessRule($rule) | Out-Null }
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            (New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','None','None','Allow')))
    }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
        (New-Object Security.Principal.SecurityIdentifier($enrolled.sid)),'ReadAndExecute','None','None','Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Read-TaskCredential {
    Add-Type -AssemblyName System.Security
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($CredentialFile),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    try {
        $password = ConvertTo-SecureString ([Text.Encoding]::UTF8.GetString($bytes)) -AsPlainText -Force
        return [pscredential]::new("$env:COMPUTERNAME\$WorkerName",$password)
    } finally { [Array]::Clear($bytes,0,$bytes.Length) }
}
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

if (-not (Test-Path -LiteralPath $Marker)) { throw 'No enrolled worker account; refuse unrelated task changes' }
$enrolled = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
$worker = Get-LocalUser -Name $WorkerName
if ($worker.SID.Value -ne $enrolled.sid) { throw 'Worker SID changed; refuse task update' }
if (-not $worker.Enabled) { throw 'Worker account is disabled; enable only through its approved provisioning path' }
if (-not (Test-Path -LiteralPath $CredentialFile)) { throw 'Missing protected task credential; refuse automatic reset' }
if (-not (Test-Path -LiteralPath $OwnerRoot)) { throw 'AGCLifecycle owner directory is not installed; install the Windows lifecycle service first' }

# Preflight before ANY live copy/ACL/task mutation. The resident executor is
# IgnoreNew and would keep the old in-memory WorkerLifecycle/entrypoint after
# those files are replaced, so installs require a quiesced (stopped) executor.
# This applies even without -EnableOwnerExecutor because the base helper files
# always change.
$existingResident = Get-ScheduledTask -TaskName $ExecutorTask -ErrorAction SilentlyContinue
if ($null -ne $existingResident -and [string]$existingResident.State -eq 'Running') {
    throw 'maintenance_required: AGC-Worker-OwnerExecutor is running; stop/quiesce it before installing worker lifecycle files'
}

Protect-Directory $Code $enrolled.sid
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'worker-stop.ps1') -Destination $OwnerScript -Force
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'WorkerLifecycle.ps1') -Destination (Join-Path $Code 'WorkerLifecycle.ps1') -Force
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'Stop-Worker.ps1') -Destination (Join-Path $Code 'Stop-Worker.ps1') -Force
# The optional cycle protocol is enabled by native coordinator configuration only
# after this helper advertises its exact protocol marker. Legacy phase requests
# keep working on the same fixed task.
$helperText = Get-Content -LiteralPath $OwnerScript -Raw
$cycleProtocolSupported = [bool]($helperText -match '(?m)^# AGC_OWNER_CYCLE_PROTOCOL=1\s*$')
Protect-OwnerScript $OwnerScript

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $existingPrincipal = [string]$existing.Principal.UserId
    $existingSid = ''
    try { $existingSid = (New-Object Security.Principal.NTAccount($existingPrincipal)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { }
    if ($existingPrincipal -and $existingPrincipal -notmatch "(^|\\)$WorkerName$" -and $existingSid -ne $enrolled.sid) {
        throw 'Existing lifecycle stop task has a different principal; inspect before replacing'
    }
    $backup = Join-Path $Control 'lifecycle-stop-task.before.xml'
    if (-not (Test-Path -LiteralPath $backup)) {
        $existing | Export-ScheduledTask | Set-Content -LiteralPath $backup -Encoding UTF8
    }
}
$credential = Read-TaskCredential
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $ExpectedArguments
$settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances Queue -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings `
    -User $credential.UserName -Password $credential.GetNetworkCredential().Password `
    -RunLevel Limited -Force | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
$principal = $task.Principal
$taskSid = (Get-LocalUser -Name $WorkerName).SID.Value
if ($principal.UserId -notmatch "(^|\\)$WorkerName$" -or $principal.RunLevel -ne 'Limited') {
    throw 'Lifecycle stop task principal verification failed'
}
$action = @($task.Actions)[0]
$actionMatchesFixed = ([string]$action.Execute -match '(^|\\)powershell\.exe$' -and [string]$action.Arguments -eq $ExpectedArguments)
if (-not $actionMatchesFixed) {
    throw 'Lifecycle stop task action does not match the fixed owner-cycle helper action'
}
$taskXml = $task | Export-ScheduledTask

# Optional independent resident executor (#212). It is started while Windows is
# Ready and must be verified ready before workloads are restored. Preshutdown
# never registers, starts, or logs on anything.
$executorReady = $null
$executorTaskXml = ''
$executorState = ''
if ($EnableOwnerExecutor) {
    $executorSource = Join-Path $SourceDirectory 'worker-executor.ps1'
    if (-not (Test-Path -LiteralPath $executorSource -PathType Leaf)) { throw 'worker-executor.ps1 is missing from the source directory' }
    $executorText = Get-Content -LiteralPath $executorSource -Raw
    if ($executorText -notmatch '(?m)^# AGC_OWNER_RESIDENT_EXECUTOR=1\s*$') { throw 'worker-executor.ps1 does not advertise AGC_OWNER_RESIDENT_EXECUTOR=1' }
    $existingExecutor = Get-ScheduledTask -TaskName $ExecutorTask -ErrorAction SilentlyContinue
    if ($existingExecutor) {
        $existingExecutorPrincipal = [string]$existingExecutor.Principal.UserId
        $existingExecutorSid = ''
        try { $existingExecutorSid = (New-Object Security.Principal.NTAccount($existingExecutorPrincipal)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { }
        if ($existingExecutorPrincipal -and $existingExecutorPrincipal -notmatch "(^|\\)$WorkerName$" -and $existingExecutorSid -ne $enrolled.sid) {
            throw 'Existing owner executor task has a different principal; inspect before replacing'
        }
        $existingExecutorAction = @($existingExecutor.Actions)[0]
        if ($null -ne $existingExecutorAction -and
            ([string]$existingExecutorAction.Execute -ne 'powershell.exe' -or [string]$existingExecutorAction.Arguments -ne $ExecutorArguments)) {
            throw 'Existing owner executor task action does not match the fixed resident action; inspect before replacing'
        }
        $backupExecutor = Join-Path $Control 'owner-executor-task.before.xml'
        if (-not (Test-Path -LiteralPath $backupExecutor)) {
            $existingExecutor | Export-ScheduledTask | Set-Content -LiteralPath $backupExecutor -Encoding UTF8
        }
    }
    Copy-Item -LiteralPath $executorSource -Destination $ExecutorScript -Force
    Protect-OwnerScript $ExecutorScript
    $executorTriggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn))
    $executorSettings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -WakeToRun:$false -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    # Register the first-rollout task disabled in the initial settings so no
    # boot/logon trigger can launch it between registration and activation.
    $executorSettings.Enabled = -not $DeferExecutorStart
    $executorAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $ExecutorArguments
    Register-ScheduledTask -TaskName $ExecutorTask -Action $executorAction -Trigger $executorTriggers `
        -Settings $executorSettings -User $credential.UserName -Password $credential.GetNetworkCredential().Password `
        -RunLevel Limited -Force | Out-Null
    $executorTask = Get-ScheduledTask -TaskName $ExecutorTask
    $executorPrincipal = $executorTask.Principal
    if ($executorPrincipal.UserId -notmatch "(^|\\)$WorkerName$" -or $executorPrincipal.RunLevel -ne 'Limited') {
        throw 'Owner executor task principal verification failed'
    }
    $executorTaskXml = $executorTask | Export-ScheduledTask
    $workerReceipts = Join-Path $LifecycleRoot 'receipts\worker'
    if (-not (Test-Path -LiteralPath $workerReceipts)) {
        New-Item -ItemType Directory -Path $workerReceipts -Force | Out-Null
    }
    # Idempotent: an existing directory left admin-only by a partial install or
    # fixture must be repaired so the executor can write its readiness record.
    & icacls.exe $workerReceipts /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' ("*" + $enrolled.sid + ":(OI)(CI)M") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to protect the worker executor readiness directory' }
    if ($DeferExecutorStart) {
        # First rollout: the old B4 shared library has no readiness helpers.
        # Register disabled under the existing protected credential; root/native
        # activation installs the shared library/config, then explicitly enables
        # and verifies this exact task before workloads return.
        Disable-ScheduledTask -TaskName $ExecutorTask -ErrorAction Stop | Out-Null
        if ([string](Get-ScheduledTask -TaskName $ExecutorTask -ErrorAction Stop).State -ne 'Disabled') {
            throw 'Deferred owner executor task is not disabled'
        }
        $executorState = 'registered-disabled'
    } else {
        Enable-ScheduledTask -TaskName $ExecutorTask -ErrorAction Stop | Out-Null
        $sharedLibrary = Join-Path $LifecycleRoot 'Lifecycle.ps1'
        if (-not (Test-Path -LiteralPath $sharedLibrary -PathType Leaf)) { throw 'AGCLifecycle shared library is required to verify owner executor readiness' }
        . $sharedLibrary
        Set-StrictMode -Off
        if (-not (Get-Command Get-AgcExecutorReadiness -CommandType Function -ErrorAction SilentlyContinue)) {
            throw 'Shared readiness helper Get-AgcExecutorReadiness is missing; refuse to claim executor readiness'
        }
        Start-ScheduledTask -TaskName $ExecutorTask
        $readyDeadline = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            try { $executorReady = Get-AgcExecutorReadiness -Owner 'worker' } catch { $executorReady = $null }
            if ($null -ne $executorReady -and $executorReady.Present -eq $true -and $executorReady.Fresh -eq $true -and
                [string]$executorReady.AccountSid -eq $enrolled.sid -and [string]$executorReady.Protocol -eq 'cycle') { break }
            $executorReady = $null
        } while ((Get-Date) -lt $readyDeadline)
        if ($null -eq $executorReady) { throw 'AGC-Worker-OwnerExecutor did not publish fresh readiness; workloads must stay unadmitted' }
        $executorState = 'ready'
    }
}

$receipt = @{
    version = 1; task = $TaskName; state = 'installed'; worker_sid = $taskSid
    principal = $principal.UserId; run_level = [string]$principal.RunLevel
    arguments = $ExpectedArguments; account_enabled = $true
    owner_script = $OwnerScript; owner_script_sha256 = (Hash $OwnerScript)
    worker_lifecycle_sha256 = (Hash (Join-Path $Code 'WorkerLifecycle.ps1'))
    cycle_protocol_supported = $cycleProtocolSupported
    task_action_matches_fixed = $actionMatchesFixed
    owner_executor_task = $(if ($EnableOwnerExecutor) { $ExecutorTask } else { '' })
    owner_executor_script = $(if ($EnableOwnerExecutor) { $ExecutorScript } else { '' })
    owner_executor_sha256 = $(if ($EnableOwnerExecutor) { Hash $ExecutorScript } else { '' })
    owner_executor_task_definition_sha256 = $(if ($EnableOwnerExecutor) {
        [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($executorTaskXml))).Replace('-','').ToLowerInvariant()
    } else { '' })
    owner_executor_state = $executorState
    owner_executor_ready_pid = $(if ($null -ne $executorReady) { [int]$executorReady.Pid } else { 0 })
    owner_executor_ready_start = $(if ($null -ne $executorReady) { [string]$executorReady.ProcessStartUtc } else { '' })
    stop_worker_sha256 = (Hash (Join-Path $Code 'Stop-Worker.ps1'))
    task_definition_sha256 = ([BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($taskXml)))).Replace('-','').ToLowerInvariant()
    installed_at = [DateTimeOffset]::UtcNow.ToString('o')
}
Save-Json (Join-Path $Control 'lifecycle-stop-task.json') $receipt
[pscustomobject]@{state='installed';task=$TaskName;principal=$principal.UserId;account_disabled=$false;owner_executor_state=$executorState;receipt=(Join-Path $Control 'lifecycle-stop-task.json')} | ConvertTo-Json -Compress
