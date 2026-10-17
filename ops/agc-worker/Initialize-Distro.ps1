$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong Windows account' }
$Data = 'C:\ProgramData\AGC-Worker\data'
$Distro = 'AGC-Worker'
function Copy-WorkerFile([string]$WindowsPath, [string]$LinuxPath) {
    $bytes = [IO.File]::ReadAllBytes($WindowsPath)
    $digest = (Get-FileHash -LiteralPath $WindowsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $decode = "import base64,hashlib,os,sys; data=base64.b64decode(sys.stdin.buffer.read().strip(),validate=True); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; tmp=sys.argv[1]+'.tmp'; f=open(tmp,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,sys.argv[1])"
    [Convert]::ToBase64String($bytes) | & wsl.exe -d $Distro -u root --exec /usr/bin/python3 -c $decode $LinuxPath $digest
    if ($LASTEXITCODE -ne 0) { throw 'Byte-exact worker source copy failed' }
}
$stage = 'preflight'
try {
Start-Transcript -Path (Join-Path $Data 'bootstrap.log') -Force | Out-Null
$inputSpec = Get-Content (Join-Path $Data 'bootstrap-input.json') -Raw | ConvertFrom-Json
if ((Get-FileHash (Join-Path $Data 'rootfs.tar') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $inputSpec.rootfs_sha256) { throw 'Rootfs changed after staging' }
$names = ((& wsl.exe --list --quiet) -replace "`0",'') | ForEach-Object { $_.Trim() }
if ($names | Where-Object {$_ -and $_ -ne $Distro}) { throw 'Worker Windows account has an unexpected distribution' }
if ($names -notcontains $Distro) {
    $stage = 'import'
    & wsl.exe --import $Distro (Join-Path $Data 'distro') (Join-Path $Data 'rootfs.tar') --version 2
    if ($LASTEXITCODE -ne 0) { throw 'WSL import failed' }
}
# Initial import uses root. Copy via stdin; Windows-drive automount is never needed.
$stage = 'worker_configuration'
Copy-WorkerFile 'C:\ProgramData\AGC-Worker\code\wsl.conf' '/etc/wsl.conf'
& wsl.exe -d $Distro -u root --exec /usr/bin/mkdir -p /opt/agc-worker-bootstrap
foreach ($name in @('worker.py','qualification_gate.py','release-qualified-hold.sh','install-linux.sh','adapters.example.json')) {
    Copy-WorkerFile "C:\ProgramData\AGC-Worker\code\$name" "/opt/agc-worker-bootstrap/$name"
}
# Restart only this newly created worker distro to apply its own systemd config.
& wsl.exe --terminate $Distro
if ($LASTEXITCODE -ne 0) { throw 'Worker-only terminate failed' }
$stage = 'linux_install'
& wsl.exe -d $Distro -u root --exec /bin/bash /opt/agc-worker-bootstrap/install-linux.sh /opt/agc-worker-bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Linux worker setup failed' }
$workerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$vhd = Join-Path $Data 'distro\ext4.vhdx'
if ((Get-Acl -LiteralPath $vhd).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $workerSid) { throw 'Worker VHD owner does not match quota SID' }
$receipt = @{version=1;distro=$Distro;identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;rootfs_sha256=$inputSpec.rootfs_sha256;state='held';imported_at=[DateTimeOffset]::UtcNow.ToString('o')}
$receipt | ConvertTo-Json | Set-Content (Join-Path $Data 'distro-ready.json') -Encoding UTF8
Remove-Item -LiteralPath (Join-Path $Data 'rootfs.tar')
if (Test-Path (Join-Path $Data 'bootstrap-failure.json')) { Remove-Item -LiteralPath (Join-Path $Data 'bootstrap-failure.json') }
} catch {
    @{version=1;stage=$stage;exit_code=$LASTEXITCODE;error_type=$_.Exception.GetType().Name;message='Bootstrap failed; inspect the protected bootstrap.log and task result';failed_at=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $Data 'bootstrap-failure.json') -Encoding UTF8
    exit 1
} finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
