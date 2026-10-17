# Fixed OpenSSH ForceCommand. Never executes SSH_ORIGINAL_COMMAND or caller argv.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'identity' }
    # Do not start the worker WSL VM outside the permitted run days.
    $now = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
    $day = [int]$now.DayOfWeek
    if ($day -in @(1,2) -or ($day -eq 0 -and $now.TimeOfDay -ge [TimeSpan]::FromMinutes(1425))) { throw 'schedule' }
    $buffer = New-Object Text.StringBuilder
    while ($buffer.Length -le 524288) {
        $c = [Console]::Read()
        if ($c -lt 0 -or $c -eq 10) { break }
        [void]$buffer.Append([char]$c)
    }
    if ($buffer.Length -gt 524288) { throw 'size' }
    $request = $buffer.ToString() | ConvertFrom-Json
    if ($request.protocol_version -ne 1 -or $request.action -notin @('status','start','heartbeat','inspect','stop','stage','fetch','ack')) { throw 'protocol' }
    $running = ((& wsl.exe --list --running --quiet) -replace "`0",'') | ForEach-Object {$_.Trim()}
    if ($running -notcontains 'AGC-Worker') { throw 'worker_offline' }
    $buffer.ToString() | & wsl.exe -d AGC-Worker -u root --exec /usr/local/bin/agc-worker rpc
    if ($LASTEXITCODE -ne 0) { throw 'rpc' }
} catch {
    [Console]::WriteLine('{"protocol_version":1,"ok":false,"error":"worker_unavailable"}')
    exit 1
}
