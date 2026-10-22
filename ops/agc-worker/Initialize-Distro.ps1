$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Wrong Windows account' }
$Data = 'C:\ProgramData\AGC-Worker\data'
$Distro = 'AGC-Worker'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1'))) { throw 'lifecycle_library_unavailable' }
. (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1') -DataPath $Data
if (-not (Initialize-AgcWorkerLifecycle)) { throw 'lifecycle_library_unavailable' }
function Assert-Gate { Assert-AgcWorkerGateOpen | Out-Null }
function Copy-WorkerFile([string]$WindowsPath, [string]$LinuxPath) {
    $bytes = [IO.File]::ReadAllBytes($WindowsPath)
    $digest = (Get-FileHash -LiteralPath $WindowsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $decode = "import base64,hashlib,os,sys; data=base64.b64decode(sys.stdin.buffer.read().strip(),validate=True); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; tmp=sys.argv[1]+'.tmp'; f=open(tmp,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,sys.argv[1])"
    $encoded = [Convert]::ToBase64String($bytes)
    $copy = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/python3','-c',$decode,$LinuxPath,$digest) -TimeoutMilliseconds 120000 -StandardInput $encoded
    if ($copy.Started -ne $true -or $copy.OutputTruncated -eq $true -or $copy.TimedOut -eq $true -or $copy.ExitCode -ne 0) { throw 'Byte-exact worker source copy failed' }
}
$stage = 'lifecycle_gate'
try {
    Start-Transcript -Path (Join-Path $Data 'bootstrap.log') -Force | Out-Null
    Assert-Gate
    $inputSpec = Get-Content (Join-Path $Data 'bootstrap-input.json') -Raw | ConvertFrom-Json
    if ((Get-FileHash (Join-Path $Data 'rootfs.tar') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $inputSpec.rootfs_sha256) { throw 'Rootfs changed after staging' }
    $listing = Invoke-AgcWorkerWsl -Arguments @('--list','--quiet') -TimeoutMilliseconds 10000
    if ($listing.Started -ne $true -or $listing.TimedOut -eq $true -or $listing.ExitCode -ne 0) { throw 'WSL listing unavailable' }
    $names = (([string]$listing.Stdout) -replace "`0",'') -split "`r?`n" | ForEach-Object { $_.Trim() }
    if ($names | Where-Object {$_ -and $_ -ne $Distro}) { throw 'Worker Windows account has an unexpected distribution' }
    if ($names -notcontains $Distro) {
        $stage = 'import'
        Assert-Gate
        $import = Invoke-AgcWorkerAdmittedWsl -Arguments @('--import',$Distro,(Join-Path $Data 'distro'),(Join-Path $Data 'rootfs.tar'),'--version','2') -TimeoutMilliseconds 1000000
        if ($import.Started -ne $true -or $import.TimedOut -eq $true -or $import.ExitCode -ne 0) { throw 'WSL import failed' }
    }
    # Initial import uses root. Copy via stdin; Windows-drive automount is never needed.
    $stage = 'worker_configuration'
    Assert-Gate
    Copy-WorkerFile 'C:\ProgramData\AGC-Worker\code\wsl.conf' '/etc/wsl.conf'
    Assert-Gate
    $mkdir = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/usr/bin/mkdir','-p','/opt/agc-worker-bootstrap') -TimeoutMilliseconds 20000
    if ($mkdir.Started -ne $true -or $mkdir.TimedOut -eq $true -or $mkdir.ExitCode -ne 0) { throw 'Worker bootstrap directory failed' }
    foreach ($name in @('worker.py','qualification_gate.py','release-qualified-hold.sh','install-linux.sh','adapters.example.json')) {
        Assert-Gate
        Copy-WorkerFile "C:\ProgramData\AGC-Worker\code\$name" "/opt/agc-worker-bootstrap/$name"
    }
    # Restart only this newly created worker distro to apply its own systemd config.
    # This is a non-launch terminate; it never starts a stopped distro.
    $term = Invoke-AgcWorkerWsl -Arguments @('--terminate',$Distro) -TimeoutMilliseconds 20000
    if ($term.Started -ne $true -or $term.TimedOut -eq $true -or $term.ExitCode -ne 0) { throw 'Worker-only terminate failed' }
    $stage = 'linux_install'
    Assert-Gate
    $install = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d',$Distro,'-u','root','--exec','/bin/bash','/opt/agc-worker-bootstrap/install-linux.sh','/opt/agc-worker-bootstrap') -TimeoutMilliseconds 600000
    if ($install.Started -ne $true -or $install.TimedOut -eq $true -or $install.OutputTruncated -eq $true -or $install.ExitCode -ne 0) { throw 'Linux worker setup failed' }
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
