#Requires -RunAsAdministrator
<#! Provision only the dedicated AGC worker; never changes another user's WSL.
    Run from the existing store-admin SSH connection. Bootstrap remains held.
    Secrets stay in Windows DPAPI-protected administrator-only storage.
!#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Account','Import','Runtime','Disable')][string]$Phase,
    [string]$PublicKeyFile,
    [string]$RootfsFile,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$RootfsSha256,
    [ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$RecoverAccountSid,
    [string]$SourceDirectory = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WorkerName = 'agc-worker'
$Distro = 'AGC-Worker'
$Base = 'C:\ProgramData\AGC-Worker'
$Control = Join-Path $Base 'control'
$Code = Join-Path $Base 'code'
$Data = Join-Path $Base 'data'
$Marker = Join-Path $Control 'account.json'
$CredentialFile = Join-Path $Control 'credential.dpapi'
$TaskName = 'AGC-Worker-Background'
$BootstrapTask = 'AGC-Worker-Bootstrap'

function Native-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Native command failed: $File ($LASTEXITCODE)" }
}
function Save-Json([string]$File, $Value) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath "$File.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$File.tmp" -Destination $File -Force
}
function Protect-Directory([string]$Directory, [string]$WorkerAccess) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    }
    if ($WorkerAccess) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($script:WorkerSid,$WorkerAccess,'ContainerInherit,ObjectInherit','None','Allow')))
    }
    Set-Acl -LiteralPath $Directory -AclObject $acl
}
function Save-TaskCredential([Security.SecureString]$Password) {
    Add-Type -AssemblyName System.Security
    $temporary = [pscredential]::new("$env:COMPUTERNAME\$WorkerName",$Password)
    $bytes = [Text.Encoding]::UTF8.GetBytes($temporary.GetNetworkCredential().Password)
    try {
        $encrypted = [Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        [IO.File]::WriteAllBytes($CredentialFile,$encrypted)
    } finally { [Array]::Clear($bytes,0,$bytes.Length) }
}
function Read-TaskCredential {
    Add-Type -AssemblyName System.Security
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($CredentialFile),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    try {
        $password = ConvertTo-SecureString ([Text.Encoding]::UTF8.GetString($bytes)) -AsPlainText -Force
        return [pscredential]::new("$env:COMPUTERNAME\$WorkerName",$password)
    } finally { [Array]::Clear($bytes,0,$bytes.Length) }
}
function New-TaskPassword {
    $random = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($random)
    return (ConvertTo-SecureString ('Agc!7a' + [Convert]::ToBase64String($random)) -AsPlainText -Force)
}

if ($Phase -eq 'Disable') {
    foreach ($name in @($TaskName,$BootstrapTask)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $name | Out-Null
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        }
    }
    # Disabling the account prevents new SSH starts. Runtime supervisor fencing
    # expires outstanding leases; preserve distro/data/receipts for reconciliation.
    if (Test-Path $Marker) { Disable-LocalUser -Name $WorkerName }
    [pscustomobject]@{phase=$Phase;state='disabled';preserved='account,distro,receipts,staff configuration'} | ConvertTo-Json -Compress
    exit 0
}

$cpu = Get-CimInstance Win32_Processor
$ram = [uint64](Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$logical = [int](($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
$drive = [System.IO.DriveInfo]::new('C')
if ($ram -lt 28000000000 -or $logical -lt 12) { throw 'Verified hardware cannot support the approved ceilings and staff reserve' }
if ($drive.DriveFormat -ne 'NTFS' -or $drive.AvailableFreeSpace -lt ([int64]($drive.TotalSize * .15) + 12000000000)) { throw 'NTFS or staff disk headroom preflight failed' }

if ($Phase -eq 'Account') {
    if (-not $PublicKeyFile -or -not (Test-Path $PublicKeyFile)) { throw 'Dedicated worker public key file is required' }
    $publicKey = (Get-Content -LiteralPath $PublicKeyFile -Raw).Trim()
    if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?$') { throw 'Expected one dedicated ed25519 public key' }
    $existing = Get-LocalUser -Name $WorkerName -ErrorAction SilentlyContinue
    if ($existing -and -not (Test-Path $Marker)) {
        if (-not $RecoverAccountSid -or $existing.SID.Value -ne $RecoverAccountSid -or $existing.Enabled -or $existing.Description -ne 'Isolated headless AGC compute worker') {
            throw 'Existing unmanaged agc-worker account: preserve it for manual inspection'
        }
        $script:WorkerSid = $existing.SID
        Protect-Directory $Base 'ReadAndExecute'
        Protect-Directory $Control ''
        Save-Json $Marker @{version=1;sid=$existing.SID.Value;owner='Console #203';recovered_disabled_partial=$true;created_at=[DateTimeOffset]::UtcNow.ToString('o')}
    }
    if (-not $existing) {
        $password = New-TaskPassword
        $existing = New-LocalUser -Name $WorkerName -Password $password -PasswordNeverExpires -UserMayNotChangePassword -Disabled -Description 'Isolated headless AGC compute worker'
        $script:WorkerSid = $existing.SID
        Protect-Directory $Base 'ReadAndExecute'
        Protect-Directory $Control ''
        Save-Json $Marker @{version=1;sid=$existing.SID.Value;owner='Console #203';created_at=[DateTimeOffset]::UtcNow.ToString('o')}
        Save-TaskCredential $password
    }
    if (-not (Test-Path $CredentialFile)) {
        if ($existing.Enabled) { throw 'Missing task credential on enabled worker: refuse automatic password reset' }
        $password = New-TaskPassword
        Set-LocalUser -Name $WorkerName -Password $password
        Save-TaskCredential $password
        # The old user-DPAPI attempt may have left an incomplete encrypted file.
        $partial = Join-Path $Control 'credential.clixml'
        if (Test-Path $partial) { Remove-Item -LiteralPath $partial }
    }
    $script:WorkerSid = $existing.SID
    if ((Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object {$_.SID -eq $WorkerSid})) { throw 'Worker must not be an administrator' }
    if (-not (Get-LocalGroupMember -SID 'S-1-5-32-545' | Where-Object {$_.SID -eq $WorkerSid})) {
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $WorkerName
    }
    if ((Get-Content $Marker -Raw | ConvertFrom-Json).sid -ne $WorkerSid.Value) { throw 'Worker account SID changed' }
    # Create only this user's Windows profile, without interactive desktop login.
    Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class AgcProfile {
 [DllImport("userenv.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern int CreateProfile([MarshalAs(UnmanagedType.LPWStr)] string sid,
  [MarshalAs(UnmanagedType.LPWStr)] string name,
  [Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, uint size);
}
public static class AgcAccountRights {
 [StructLayout(LayoutKind.Sequential)] struct ObjectAttributes {
  public uint Length; public IntPtr RootDirectory; public IntPtr ObjectName;
  public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
 }
 [StructLayout(LayoutKind.Sequential)] struct UnicodeString { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
 [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref ObjectAttributes attrs, uint access, out IntPtr handle);
 [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr handle, IntPtr sid, UnicodeString[] rights, uint count);
 [DllImport("advapi32.dll")] static extern uint LsaNtStatusToWinError(uint status);
 [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
 public static void Add(string sidText, string right) {
  var attrs = new ObjectAttributes(); attrs.Length=(uint)Marshal.SizeOf(attrs);
  IntPtr handle; uint status=LsaOpenPolicy(IntPtr.Zero,ref attrs,0x810,out handle);
  if(status!=0) throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
  IntPtr sid=IntPtr.Zero, text=IntPtr.Zero;
  try {
   var identity=new System.Security.Principal.SecurityIdentifier(sidText);
   var bytes=new byte[identity.BinaryLength]; identity.GetBinaryForm(bytes,0);
   sid=Marshal.AllocHGlobal(bytes.Length); Marshal.Copy(bytes,0,sid,bytes.Length);
   text=Marshal.StringToHGlobalUni(right);
   var entry=new UnicodeString {Length=(ushort)(right.Length*2),MaximumLength=(ushort)(right.Length*2+2),Buffer=text};
   status=LsaAddAccountRights(handle,sid,new[]{entry},1);
   if(status!=0) throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
  } finally { if(sid!=IntPtr.Zero)Marshal.FreeHGlobal(sid); if(text!=IntPtr.Zero)Marshal.FreeHGlobal(text);LsaClose(handle); }
 }
}
'@
    # CreateProfile's RPC contract requires MAX_PATH, not an oversized buffer.
    $profilePath = New-Object Text.StringBuilder 260
    $createResult = [AgcProfile]::CreateProfile($WorkerSid.Value,$WorkerName,$profilePath,260)
    $profile = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$($WorkerSid.Value)" -ErrorAction SilentlyContinue).ProfileImagePath
    if (-not $profile -or -not (Test-Path $profile)) { throw "Worker profile creation failed ($createResult)" }
    # Add only these rights to this new SID; do not replace machine policy or
    # existing account rights. Batch logon and key-only SSH remain available.
    [AgcAccountRights]::Add($WorkerSid.Value,'SeDenyInteractiveLogonRight')
    [AgcAccountRights]::Add($WorkerSid.Value,'SeDenyRemoteInteractiveLogonRight')
    $loginTiles = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
    if (-not (Test-Path $loginTiles)) { New-Item -Path $loginTiles -Force | Out-Null }
    New-ItemProperty -Path $loginTiles -Name $WorkerName -Value 0 -PropertyType DWord -Force | Out-Null
    Protect-Directory $Code 'ReadAndExecute'
    Protect-Directory $Data 'Modify'
    Copy-Item -LiteralPath (Join-Path $SourceDirectory 'wslconfig') -Destination (Join-Path $profile '.wslconfig') -Force
    foreach ($file in @('Dispatch-Worker.ps1','Start-Worker.ps1','Initialize-Distro.ps1','worker.py','install-linux.sh','wsl.conf','adapters.example.json')) {
        if (Test-Path (Join-Path $SourceDirectory $file)) { Copy-Item -LiteralPath (Join-Path $SourceDirectory $file) -Destination $Code -Force }
    }
    # Hard per-SID ceiling. Existing staff defaults/limits must remain unlimited
    # before enabling enforcement on a previously untracked volume.
    $quota = New-Object -ComObject Microsoft.DiskQuota
    $quota.Initialize('C:\',$true)
    if ($quota.QuotaState -ne 2) {
        if ($quota.DefaultQuotaLimit -ne -1) { throw 'Existing finite default quota requires review before enabling enforcement' }
        foreach ($record in $quota) {
            if ($record.QuotaLimit -ne -1 -and $record.LogonName -ne "$env:COMPUTERNAME\$WorkerName") { throw 'Existing finite quota record requires review' }
        }
    }
    $workerQuota = $quota.AddUser("$env:COMPUTERNAME\$WorkerName")
    $quotaBefore = Join-Path $Control 'quota-before.json'
    if (-not (Test-Path $quotaBefore)) {
        Save-Json $quotaBefore @{state=$quota.QuotaState;default_limit=$quota.DefaultQuotaLimit;default_threshold=$quota.DefaultQuotaThreshold;worker_limit=$workerQuota.QuotaLimit;worker_threshold=$workerQuota.QuotaThreshold;worker_sid=$WorkerSid.Value}
    }
    $workerQuota.QuotaLimit = [int64]300000000000
    $workerQuota.QuotaThreshold = [int64]285000000000
    $quota.QuotaState = 2
    if ($workerQuota.QuotaLimit -ne 300000000000 -or $quota.QuotaState -ne 2) { throw 'Worker quota verification failed' }
    # Copy keys into an administrator-owned file; worker cannot replace ForceCommand.
    $keyPath = Join-Path $env:ProgramData 'ssh\agc-worker_authorized_keys'
    ('from="100.117.220.92",no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding ' + $publicKey) | Set-Content -LiteralPath $keyPath -Encoding ascii
    Native-Checked 'icacls.exe' @($keyPath,'/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F',"*$($WorkerSid.Value):R")
    $sshdConfig = Join-Path $env:ProgramData 'ssh\sshd_config'
    $original = [IO.File]::ReadAllText($sshdConfig)
    $updated = $original
    if ($original -notmatch '(?m)^# BEGIN AGC-WORKER #203\s*$') {
        $allow = [regex]::Matches($original,'(?m)^AllowUsers\s+([^\r\n]+)')
        if ($allow.Count -ne 1 -or $allow[0].Groups[1].Value.Trim() -ne 'store-ssh store-admin') { throw 'Existing SSH allowlist differs: inspect before changing it' }
        $updated = $original.Replace($allow[0].Value, $allow[0].Value + ' agc-worker')
        $updated += @'

# BEGIN AGC-WORKER #203
Match User agc-worker
    AuthorizedKeysFile __PROGRAMDATA__/ssh/agc-worker_authorized_keys
    AuthenticationMethods publickey
    PasswordAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    ForceCommand powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/ProgramData/AGC-Worker/code/Dispatch-Worker.ps1
# END AGC-WORKER #203
'@
        Copy-Item -LiteralPath $sshdConfig -Destination (Join-Path $Control 'sshd_config.before')
        [IO.File]::WriteAllText($sshdConfig,$updated,[Text.UTF8Encoding]::new($false))
        try { Native-Checked "$env:WINDIR\System32\OpenSSH\sshd.exe" @('-t','-f',$sshdConfig) }
        catch { [IO.File]::WriteAllText($sshdConfig,$original,[Text.UTF8Encoding]::new($false)); throw }
    }
    $sourceHashes = @{}
    Get-ChildItem -LiteralPath $Code -File | ForEach-Object {$sourceHashes[$_.Name]=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    Save-Json (Join-Path $Control 'account-ready.json') @{sid=$WorkerSid.Value;profile=$profile;ram_bytes=$ram;logical_processors=$logical;disk_quota=300000000000;state='held';wsl_limits='14305 MiB, 12 CPUs, zero swap';source_sha256=$sourceHashes;sshd_before_sha256=(Get-FileHash (Join-Path $Control 'sshd_config.before') -Algorithm SHA256).Hash.ToLowerInvariant()}
    Enable-LocalUser -Name $WorkerName
    if ($updated -ne $original) { Restart-Service sshd }
    [pscustomobject]@{phase=$Phase;state='held';sid=$WorkerSid.Value;profile=$profile;quota_bytes=300000000000} | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path $Marker) -or -not (Test-Path $CredentialFile)) { throw 'Complete Account phase first' }
$credential = Read-TaskCredential
$script:WorkerSid = (Get-LocalUser -Name $WorkerName).SID
if ($Phase -eq 'Import') {
    if (-not $RootfsFile -or -not $RootfsSha256 -or -not (Test-Path $RootfsFile)) { throw 'Supply a separately verified Ubuntu 24.04 rootfs and SHA-256' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $RootfsFile).Hash.ToLowerInvariant() -ne $RootfsSha256) { throw 'Rootfs hash mismatch' }
    $importRootfs = Join-Path $Data 'rootfs.tar'
    if (-not (Test-Path (Join-Path $Data 'distro-ready.json'))) {
        Copy-Item -LiteralPath $RootfsFile -Destination $importRootfs -Force
        $rootfsAcl = Get-Acl -LiteralPath $importRootfs
        $rootfsAcl.SetOwner($WorkerSid)
        Set-Acl -LiteralPath $importRootfs -AclObject $rootfsAcl
        if ((Get-Acl -LiteralPath $importRootfs).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $WorkerSid.Value) { throw 'Staged rootfs is not charged to worker quota' }
        Save-Json (Join-Path $Data 'bootstrap-input.json') @{rootfs_sha256=$RootfsSha256}
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGC-Worker\code\Initialize-Distro.ps1'
        $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $BootstrapTask -Action $action -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
        Start-ScheduledTask -TaskName $BootstrapTask
        [pscustomobject]@{phase=$Phase;state='bootstrap_started';receipt=(Join-Path $Data 'distro-ready.json')} | ConvertTo-Json -Compress
    } else { [pscustomobject]@{phase=$Phase;state='already_imported'} | ConvertTo-Json -Compress }
    exit 0
}

if ($Phase -eq 'Runtime') {
    if (-not (Test-Path (Join-Path $Data 'distro-ready.json'))) { throw 'Import and verify independent distro first' }
    foreach ($file in @('Dispatch-Worker.ps1','Start-Worker.ps1','worker.py','install-linux.sh')) {
        Copy-Item -LiteralPath (Join-Path $SourceDirectory $file) -Destination $Code -Force
    }
    # Startup trigger never wakes or powers on the laptop. Script exits before
    # starting WSL on Monday/Tuesday or battery. Existing power plans untouched.
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGC-Worker\code\Start-Worker.ps1'
    $triggers = @((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday,Thursday,Friday,Saturday,Sunday -At '00:01'))
    $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Days 6) -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    [pscustomobject]@{phase=$Phase;state='runtime_started_but_held';next='verify independent VM/cgroup/host telemetry before explicit hold removal'} | ConvertTo-Json -Compress
}
