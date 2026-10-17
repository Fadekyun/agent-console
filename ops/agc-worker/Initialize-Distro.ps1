$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong Windows account' }
$Data = 'C:\ProgramData\AGC-Worker\data'
$Distro = 'AGC-Worker'
$inputSpec = Get-Content (Join-Path $Data 'bootstrap-input.json') -Raw | ConvertFrom-Json
if ((Get-FileHash (Join-Path $Data 'rootfs.tar') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $inputSpec.rootfs_sha256) { throw 'Rootfs changed after staging' }
$names = ((& wsl.exe --list --quiet) -replace "`0",'') | ForEach-Object { $_.Trim() }
if ($names | Where-Object {$_ -and $_ -ne $Distro}) { throw 'Worker Windows account has an unexpected distribution' }
if ($names -notcontains $Distro) {
    & wsl.exe --import $Distro (Join-Path $Data 'distro') (Join-Path $Data 'rootfs.tar') --version 2
    if ($LASTEXITCODE -ne 0) { throw 'WSL import failed' }
}
# Initial import uses root. Copy via stdin; Windows-drive automount is never needed.
$wslConf = [IO.File]::ReadAllText('C:\ProgramData\AGC-Worker\code\wsl.conf')
$wslConf | & wsl.exe -d $Distro -u root --exec /usr/bin/tee /etc/wsl.conf | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'WSL config copy failed' }
& wsl.exe -d $Distro -u root --exec /usr/bin/mkdir -p /opt/agc-worker-bootstrap
foreach ($name in @('worker.py','install-linux.sh','adapters.example.json')) {
    [IO.File]::ReadAllText("C:\ProgramData\AGC-Worker\code\$name") | & wsl.exe -d $Distro -u root --exec /usr/bin/tee "/opt/agc-worker-bootstrap/$name" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Source copy failed: $name" }
}
# Restart only this newly created worker distro to apply its own systemd config.
& wsl.exe --terminate $Distro
if ($LASTEXITCODE -ne 0) { throw 'Worker-only terminate failed' }
& wsl.exe -d $Distro -u root --exec /bin/bash /opt/agc-worker-bootstrap/install-linux.sh /opt/agc-worker-bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Linux worker setup failed' }
$workerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$vhd = Join-Path $Data 'distro\ext4.vhdx'
if ((Get-Acl -LiteralPath $vhd).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $workerSid) { throw 'Worker VHD owner does not match quota SID' }
$receipt = @{version=1;distro=$Distro;identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;rootfs_sha256=$inputSpec.rootfs_sha256;state='held';imported_at=[DateTimeOffset]::UtcNow.ToString('o')}
$receipt | ConvertTo-Json | Set-Content (Join-Path $Data 'distro-ready.json') -Encoding UTF8
Remove-Item -LiteralPath (Join-Path $Data 'rootfs.tar')
