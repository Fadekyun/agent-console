#Requires -RunAsAdministrator
# Install/update the persistent fixed worker lifecycle stop task.
# Preserves the existing agc-worker account, password principal and DPAPI
# credential; never disables the account or changes its password.
[CmdletBinding()]
param([string]$SourceDirectory = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
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

Protect-Directory $Code $enrolled.sid
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'worker-stop.ps1') -Destination $OwnerScript -Force
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'WorkerLifecycle.ps1') -Destination (Join-Path $Code 'WorkerLifecycle.ps1') -Force
Copy-Item -LiteralPath (Join-Path $SourceDirectory 'Stop-Worker.ps1') -Destination (Join-Path $Code 'Stop-Worker.ps1') -Force
# The optional cycle protocol is enabled by native coordinator configuration only
# after this helper advertises its exact protocol marker. Legacy phase requests
# keep working on the same fixed task.
$helperText = Get-Content -LiteralPath $OwnerScript -Raw
$cycleProtocolSupported = [bool]($helperText -match '(?m)^# AGC_OWNER_CYCLE_PROTOCOL=1\s*$')
# Owner entrypoint is admin/SYSTEM-owned, worker read-only. The worker only
# needs to read it; it must never be able to replace its own stop helper.
$ownerAcl = Get-Acl -LiteralPath $OwnerScript
$ownerAcl.SetAccessRuleProtection($true,$false)
$ownerAcl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
foreach ($rule in @($ownerAcl.Access)) { $ownerAcl.RemoveAccessRule($rule) | Out-Null }
foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
    $ownerAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
        (New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','None','None','Allow')))
}
$ownerAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
    (New-Object Security.Principal.SecurityIdentifier($enrolled.sid)),'ReadAndExecute','None','None','Allow')))
Set-Acl -LiteralPath $OwnerScript -AclObject $ownerAcl

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
$receipt = @{
    version = 1; task = $TaskName; state = 'installed'; worker_sid = $taskSid
    principal = $principal.UserId; run_level = [string]$principal.RunLevel
    arguments = $ExpectedArguments; account_enabled = $true
    owner_script = $OwnerScript; owner_script_sha256 = (Hash $OwnerScript)
    worker_lifecycle_sha256 = (Hash (Join-Path $Code 'WorkerLifecycle.ps1'))
    cycle_protocol_supported = $cycleProtocolSupported
    task_action_matches_fixed = $actionMatchesFixed
    stop_worker_sha256 = (Hash (Join-Path $Code 'Stop-Worker.ps1'))
    task_definition_sha256 = ([BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($taskXml)))).Replace('-','').ToLowerInvariant()
    installed_at = [DateTimeOffset]::UtcNow.ToString('o')
}
Save-Json (Join-Path $Control 'lifecycle-stop-task.json') $receipt
[pscustomobject]@{state='installed';task=$TaskName;principal=$principal.UserId;account_disabled=$false;receipt=(Join-Path $Control 'lifecycle-stop-task.json')} | ConvertTo-Json -Compress
