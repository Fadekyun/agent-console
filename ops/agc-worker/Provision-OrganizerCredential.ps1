#Requires -RunAsAdministrator
# Reads the existing dedicated automation session only from stdin, never argv/logs.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Base='C:\ProgramData\AGC-Worker'
$Code=Join-Path $Base 'code'
$Control=Join-Path $Base 'control'
$Data=Join-Path $Base 'data'
$task='AGC-Worker-Credential'
if((Get-ScheduledTask $task -ErrorAction SilentlyContinue).State -eq 'Running'){throw 'Credential task already running'}
$raw=[Console]::In.ReadToEnd()
if($raw.Length -lt 1 -or $raw.Length -gt 1048576){throw 'Invalid input length'}
$value=$raw | ConvertFrom-Json
if(@($value.cookies).Count -ne 0 -or @($value.origins).Count -ne 1 -or $value.origins[0].origin -ne 'https://organizer.en.bushi-navi.com'){throw 'Dedicated automation origin required'}
$account=Get-LocalUser agc-worker
$enrolled=Get-Content (Join-Path $Control 'account.json') -Raw | ConvertFrom-Json
if($account.SID.Value -ne $enrolled.sid -or -not $account.Enabled){throw 'Worker enrollment changed'}
$source=Join-Path $Code 'organizer-input.json'
if(Test-Path $source){throw 'Prior credential staging requires reconciliation'}
$receipt=Join-Path $Data 'organizer-credential-install.json'
if(Test-Path $receipt){throw 'Existing credential receipt requires reconciliation'}
Copy-Item (Join-Path $PSScriptRoot 'Install-OrganizerCredential.ps1') $Code -Force
[IO.File]::WriteAllText($source,$raw,[Text.UTF8Encoding]::new($false))
$raw=$null;$value=$null
Add-Type -AssemblyName System.Security
$bytes=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $Control 'credential.dpapi')),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
try {
    $password=[Text.Encoding]::UTF8.GetString($bytes)
    $action=New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGC-Worker\code\Install-OrganizerCredential.ps1'
    $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -User "$env:COMPUTERNAME\agc-worker" -Password $password -RunLevel Limited -Force | Out-Null
    Start-ScheduledTask $task
} finally {
    [Array]::Clear($bytes,0,$bytes.Length);$password=$null
}
[pscustomobject]@{state='requested';task=$task;receipt=$receipt;cleanup_required=$true} | ConvertTo-Json -Compress
