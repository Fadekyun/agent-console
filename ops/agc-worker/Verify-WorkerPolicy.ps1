#Requires -RunAsAdministrator
# Seal only the already-completed native checks; this does not remove Linux hold.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$NativeSetupReceipt,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$NativeSetupSourceSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$NativeSetupReceiptSha256,
    [Parameter(Mandatory)][string]$ToolchainSetupReceipt,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ToolchainSetupSourceSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ToolchainSetupReceiptSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$WorkerSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$StaffConfigSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')][string]$StaffBootId,
    [Parameter(Mandatory)][ValidatePattern('^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$')][string]$StaffObservedAt
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Base='C:\ProgramData\AGC-Worker'
$Code=Join-Path $Base 'code'
$Data=Join-Path $Base 'data'
function Assert-AdminProtected([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse point in protected operator path'}
    $acl=Get-Acl -LiteralPath $Path
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if($owner -notin @('S-1-5-18','S-1-5-32-544')){throw 'Operator path owner is not Administrators/SYSTEM'}
    $mutation=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach($rule in $acl.Access){
        if($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $mutation) -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'Operator path has an unsafe ACL'}
    }
}
function Read-ReviewedSetup([string]$Path,[string]$SourceHash,[string]$ReceiptHash) {
    $full=[IO.Path]::GetFullPath($Path)
    if([IO.Path]::GetDirectoryName($full) -ne $Data -or [IO.Path]::GetFileName($full) -notmatch "^setup-[a-z0-9-]{1,40}-$SourceHash\.json$"){throw 'Exact setup receipt path required'}
    $item=Get-Item -LiteralPath $full -Force
    if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576){throw 'Invalid setup receipt file'}
    # Setup receipts are worker-written. The operator pins the exact reviewed
    # bytes; do not discover or trust a mutable wildcard match in that folder.
    $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        if($stream.Length -gt 1048576){throw 'Setup receipt grew beyond its limit'}
        $bytes=New-Object byte[] ([int]$stream.Length)
        $offset=0
        while($offset -lt $bytes.Length){
            $count=$stream.Read($bytes,$offset,$bytes.Length-$offset)
            if($count -eq 0){throw 'Setup receipt changed while reading'}
            $offset+=$count
        }
    }finally{$stream.Dispose()}
    $hasher=[Security.Cryptography.SHA256]::Create()
    try{$actual=([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}
    if($actual -ne $ReceiptHash){throw 'Reviewed setup receipt changed'}
    $receipt=([Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)) | ConvertFrom-Json
    if($receipt.version -ne 1 -or $receipt.state -ne 'succeeded' -or $receipt.exit_code -ne 0 -or $receipt.source_sha256 -ne $SourceHash -or "setup-$($receipt.label)-$SourceHash.json" -ne [IO.Path]::GetFileName($full)){throw 'Reviewed setup did not pass'}
    $source=Join-Path $Code "setup\$SourceHash.sh"
    Assert-AdminProtected (Join-Path $Code 'setup'); Assert-AdminProtected $source
    if((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $SourceHash){throw 'Reviewed setup source changed'}
    return @{path=$full;source_sha256=$SourceHash;receipt_sha256=$ReceiptHash;finished_at=$receipt.finished_at}
}
foreach($path in @($Base,$Code,(Join-Path $Base 'control'),(Join-Path $Base 'control\account.json'))){Assert-AdminProtected $path}
if((Get-Item -LiteralPath $Data -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Worker data is redirected'}
$worker=Get-LocalUser agc-worker
$enrolled=Get-Content (Join-Path $Base 'control\account.json') -Raw | ConvertFrom-Json
if($worker.SID.Value -ne $enrolled.sid -or -not $worker.Enabled){throw 'Worker enrollment changed'}
if(@(Get-LocalGroupMember -SID S-1-5-32-544 | Where-Object {$_.SID -eq $worker.SID}).Count){throw 'Worker has administrator rights'}
$staff='C:\Users\store-admin\.wslconfig'
$observed=[DateTimeOffset]::Parse($StaffObservedAt,[Globalization.CultureInfo]::InvariantCulture)
$age=([DateTimeOffset]::UtcNow-$observed).TotalSeconds
if($age -lt -5 -or $age -gt 300){throw 'Freshly observed staff baseline required'}
if((Get-FileHash -LiteralPath $staff -Algorithm SHA256).Hash.ToLowerInvariant() -ne $StaffConfigSha256){throw 'Staff WSL settings changed'}
$running=((& wsl.exe --list --running --quiet) -replace "`0",'') | ForEach-Object {$_.Trim()}
if($running -notcontains 'Ubuntu-24.04'){throw 'Previously running staff distro unavailable'}
$staffBoot=((& wsl.exe -d Ubuntu-24.04 --exec cat /proc/sys/kernel/random/boot_id) -join '').Trim()
if($LASTEXITCODE -ne 0 -or $staffBoot -ne $StaffBootId){throw 'Staff runtime identity changed'}
$quota=New-Object -ComObject Microsoft.DiskQuota
$quota.Initialize('C:\',$false)
$q=$quota.FindUser("$env:COMPUTERNAME\agc-worker")
if($quota.QuotaState -ne 2 -or $q.QuotaLimit -ne 300000000000 -or $quota.DefaultQuotaLimit -ne -1){throw 'Worker quota changed'}
$native=Read-ReviewedSetup $NativeSetupReceipt $NativeSetupSourceSha256 $NativeSetupReceiptSha256
$toolchains=Read-ReviewedSetup $ToolchainSetupReceipt $ToolchainSetupSourceSha256 $ToolchainSetupReceiptSha256
if($native.path -eq $toolchains.path -or $NativeSetupSourceSha256 -eq $ToolchainSetupSourceSha256){throw 'Separate native and toolchain qualifications required'}
foreach($file in @('Start-Worker.ps1','Dispatch-Worker.ps1','Host-Disk.ps1')){
    Assert-AdminProtected (Join-Path $Code $file)
}
$marker=@{version=2;worker_sid=$worker.SID.Value;worker_sha256=$WorkerSha256;cgroup_verified=$true;quota_verified=$true;separate_vm_verified=$true;gpu_enabled=$false;native_setup=$native;toolchain_setup=$toolchains;staff_config_sha256=$StaffConfigSha256;staff_boot_id=$staffBoot;staff_observed_at=$StaffObservedAt;verified_at=[DateTimeOffset]::UtcNow.ToString('o')}
$destination=Join-Path $Code 'policy-verified.json'
$marker | ConvertTo-Json -Depth 8 | Set-Content "$destination.tmp" -Encoding UTF8
Move-Item "$destination.tmp" $destination -Force
[pscustomobject]@{state='policy_verified';worker_sid=$worker.SID.Value;gpu_enabled=$false;admission='Linux operator hold preserved';staff_boot_id=$staffBoot} | ConvertTo-Json -Compress
