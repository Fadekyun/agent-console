#Requires -RunAsAdministrator
# Seal only the already-completed native checks; this does not remove Linux hold.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Base='C:\ProgramData\AGC-Worker'
$Code=Join-Path $Base 'code'
$Data=Join-Path $Base 'data'
$worker=Get-LocalUser agc-worker
$enrolled=Get-Content (Join-Path $Base 'control\account.json') -Raw | ConvertFrom-Json
if($worker.SID.Value -ne $enrolled.sid -or -not $worker.Enabled){throw 'Worker enrollment changed'}
if(@(Get-LocalGroupMember -SID S-1-5-32-544 | Where-Object {$_.SID -eq $worker.SID}).Count){throw 'Worker has administrator rights'}
$staff='C:\Users\store-admin\.wslconfig'
$staffHash='9FD492796AF08E4278B79B42ABFF1677F508F408B97E839576EE8B33C6EC2752'
if((Get-FileHash $staff).Hash -ne $staffHash){throw 'Staff WSL settings changed'}
$running=((& wsl.exe --list --running --quiet) -replace "`0",'') | ForEach-Object {$_.Trim()}
if($running -notcontains 'Ubuntu-24.04'){throw 'Previously running staff distro unavailable'}
$staffBoot=((& wsl.exe -d Ubuntu-24.04 --exec cat /proc/sys/kernel/random/boot_id) -join '').Trim()
if($LASTEXITCODE -ne 0 -or $staffBoot -ne '566fe9c5-0e96-4b52-97b7-d62f55317ec0'){throw 'Staff runtime identity changed'}
$quota=New-Object -ComObject Microsoft.DiskQuota
$quota.Initialize('C:\',$false)
$q=$quota.FindUser("$env:COMPUTERNAME\agc-worker")
if($quota.QuotaState -ne 2 -or $q.QuotaLimit -ne 300000000000 -or $quota.DefaultQuotaLimit -ne -1){throw 'Worker quota changed'}
$native='e231de0aeeb990f86fedcccf7a0d34aa089c03a6d96b96b376f7f017de49d215'
$check='fcf44f4c16fc9325160d659351ca7976a938385076f5a4dc92c3345396b517d8'
foreach($item in @(@('native-qualification',$native),@('worker-latest',$check))){
    $matches=@(Get-ChildItem -LiteralPath $Data -Filter "setup-*-$($item[1]).json")
    if($matches.Count -ne 1){throw 'Unique reviewed qualification receipt required'}
    $receipt=[IO.File]::ReadAllText($matches[0].FullName) | ConvertFrom-Json
    if($receipt.state -ne 'succeeded' -or $receipt.exit_code -ne 0 -or $receipt.source_sha256 -ne $item[1]){throw 'Native qualification did not pass'}
}
foreach($file in @('Start-Worker.ps1','Dispatch-Worker.ps1','Host-Disk.ps1')){
    $acl=Get-Acl (Join-Path $Code $file)
    foreach($rule in $acl.Access){
        if($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'Worker code has unsafe ACL'}
    }
}
$marker=@{version=1;worker_sid=$worker.SID.Value;cgroup_verified=$true;quota_verified=$true;separate_vm_verified=$true;gpu_enabled=$false;native_qualification_sha256=$native;linux_check_sha256=$check;staff_config_sha256=$staffHash;staff_boot_id=$staffBoot;verified_at=[DateTimeOffset]::UtcNow.ToString('o')}
$destination=Join-Path $Code 'policy-verified.json'
$marker | ConvertTo-Json | Set-Content "$destination.tmp" -Encoding UTF8
Move-Item "$destination.tmp" $destination -Force
[pscustomobject]@{state='policy_verified';worker_sid=$worker.SID.Value;gpu_enabled=$false;admission='Linux operator hold preserved';staff_boot_id=$staffBoot} | ConvertTo-Json -Compress
