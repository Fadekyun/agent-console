#Requires -RunAsAdministrator
# Operator-only bridge from existing store-admin SSH to the separate worker VM.
# The restricted worker SSH/RPC interface never exposes this operation.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ScriptFile,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$Sha256,
    [ValidatePattern('^[a-z0-9-]{1,40}$')][string]$Label = 'setup',
    [ValidateRange(60,3600)][int]$TimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Base = 'C:\ProgramData\AGC-Worker'
$Control = Join-Path $Base 'control'
$Code = Join-Path $Base 'code'
$Data = Join-Path $Base 'data'
$TaskName = 'AGC-Worker-Setup'
$marker = Get-Content (Join-Path $Control 'account.json') -Raw | ConvertFrom-Json
$worker = Get-LocalUser -Name agc-worker
if ($marker.sid -ne $worker.SID.Value -or -not $worker.Enabled) { throw 'Worker identity is not enrolled and enabled' }
if (-not (Test-Path (Join-Path $Data 'distro-ready.json'))) { throw 'Independent worker distro is not initialized' }
if ((Get-FileHash -LiteralPath $ScriptFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) { throw 'Reviewed setup source hash mismatch' }
if ((Get-Item -LiteralPath $ScriptFile).Length -gt 1048576) { throw 'Setup source exceeds 1 MiB' }
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') { throw 'Another operator setup is running; serialize setup work' }
$setupDir = Join-Path $Code 'setup'
New-Item -ItemType Directory -Path $setupDir -Force | Out-Null
$destination = Join-Path $setupDir "$Sha256.sh"
Copy-Item -LiteralPath $ScriptFile -Destination $destination -Force
# Parent code ACL is Administrators/SYSTEM full, worker read only.
$acl = Get-Acl -LiteralPath $destination
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Setup source has a non-administrator write ACL' }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-WorkerSetup.ps1') -Destination $Code -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Host-Disk.ps1') -Destination $Code -Force
Add-Type -AssemblyName System.Security
$bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $Control 'credential.dpapi')),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
try {
    $password = [Text.Encoding]::UTF8.GetString($bytes)
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGC-Worker\code\Invoke-WorkerSetup.ps1 -Sha256 $Sha256 -Label $Label -TimeoutSeconds $TimeoutSeconds"
    $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Seconds ($TimeoutSeconds + 120)) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings -User "$env:COMPUTERNAME\agc-worker" -Password $password -RunLevel Limited -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
} finally {
    [Array]::Clear($bytes,0,$bytes.Length)
    $password = $null
}
[pscustomobject]@{state='setup_requested';task=$TaskName;source_sha256=$Sha256;receipt=(Join-Path $Data "setup-$Label-$Sha256.json");log=(Join-Path $Data "setup-$Label-$Sha256.log")} | ConvertTo-Json -Compress
