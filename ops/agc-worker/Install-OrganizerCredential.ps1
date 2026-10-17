# Runs only in the dedicated limited worker task; no transcript or credential output.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=[Text.UTF8Encoding]::new($false)
if([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$'){throw 'Worker identity required'}
$now=[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
if([int]$now.DayOfWeek -in @(1,2) -or ([int]$now.DayOfWeek -eq 0 -and $now.TimeOfDay -ge [TimeSpan]::FromMinutes(1425))){throw 'Outside worker schedule'}
$running=((& wsl.exe --list --running --quiet) -replace "`0",'') | ForEach-Object {$_.Trim()}
if($running -notcontains 'AGC-Worker'){throw 'Worker must already be running'}
$Base='C:\ProgramData\AGC-Worker'
$source=Join-Path $Base 'code\organizer-input.json'
$receipt=Join-Path $Base 'data\organizer-credential-install.json'
$result=@{version=1;state='failed';finished_at=[DateTimeOffset]::UtcNow.ToString('o')}
try {
    $raw=[IO.File]::ReadAllBytes($source)
    if($raw.Length -lt 1 -or $raw.Length -gt 1048576){throw 'Invalid input length'}
    $decode=@'
import base64,json,os,pathlib,socket,sys,tempfile
root=pathlib.Path('/etc/agc-worker')
assert (root/'hold').is_file(),'Worker must remain held'
with socket.socket(socket.AF_UNIX) as client:
 client.settimeout(10);client.connect('/run/agc-worker/rpc.sock');client.sendall(b'{"protocol_version":1,"action":"status"}\n')
 with client.makefile('rb') as stream:s=json.loads(stream.readline(524288))
 assert s.get('ok') and s.get('active') is None,'Worker must be drained'
raw=base64.b64decode(sys.stdin.buffer.read().strip(),validate=True);assert 0<len(raw)<=1048576
v=json.loads(raw);assert v.get('cookies')==[] and len(v.get('origins',[]))==1
origin=v['origins'][0];assert origin.get('origin')=='https://organizer.en.bushi-navi.com'
entries=origin['localStorage'];assert {x['name'] for x in entries}=={'userName','email','userToken'} and len(entries)==3
assert all(isinstance(x.get('value'),str) and x['value'] for x in entries)
d=root/'credentials';d.mkdir(mode=0o700,exist_ok=True);assert d.stat().st_uid==0 and not d.stat().st_mode&0o077
p=d/'agc-bushi-organizer-state.json'
if p.exists():assert not p.is_symlink() and p.read_bytes()==raw,'Existing credential differs; reconcile before rotation'
else:
 fd,name=tempfile.mkstemp(prefix='.organizer-',dir=d)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 os.chmod(name,0o600);os.replace(name,p)
 fd=os.open(d,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
assert p.stat().st_uid==0 and not p.stat().st_mode&0o077
print('dedicated_organizer_credential_installed')
'@
    # PowerShell 5 native argument passing can strip quotes from a multiline
    # Python program. Encode only the static program, never credential bytes.
    $program=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($decode))
    $launcher="import base64;exec(base64.b64decode('$program'))"
    [Convert]::ToBase64String($raw) | & wsl.exe -d AGC-Worker -u root --exec /usr/bin/python3 -c $launcher
    if($LASTEXITCODE -ne 0){throw 'Credential installation failed'}
    $result.state='installed'
} finally {
    if($raw){[Array]::Clear($raw,0,$raw.Length)}
    $result.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
    $result | ConvertTo-Json | Set-Content "$receipt.tmp" -Encoding UTF8
    Move-Item "$receipt.tmp" $receipt -Force
}
if($result.state -ne 'installed'){exit 1}
