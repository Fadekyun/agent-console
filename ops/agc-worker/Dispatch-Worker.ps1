# Fixed OpenSSH ForceCommand. Never executes SSH_ORIGINAL_COMMAND or caller argv.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1')
function Write-WorkerUnavailable {
    [Console]::WriteLine('{"protocol_version":1,"ok":false,"error":"worker_unavailable"}')
    exit 1
}
function Read-BoundedRequest {
    param([int]$TimeoutSeconds = 20)
    $stream = [Console]::OpenStandardInput()
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $buffer = New-Object byte[] 65536
    $builder = New-Object Text.StringBuilder
    while ($builder.Length -le 524288) {
        $remaining = [int][Math]::Max(0, ($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds)
        if ($remaining -le 0) { throw 'request_timeout' }
        $read = $stream.ReadAsync($buffer, 0, $buffer.Length)
        if (-not $read.Wait($remaining)) { throw 'request_timeout' }
        $count = $read.Result
        if ($count -le 0) { break }
        $chunk = [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
        $newline = $chunk.IndexOf("`n")
        if ($newline -ge 0) {
            [void]$builder.Append($chunk.Substring(0, $newline))
            break
        }
        [void]$builder.Append($chunk)
    }
    if ($builder.Length -gt 524288) { throw 'request_size' }
    return $builder.ToString()
}
try {
    if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'identity' }
    if (-not (Initialize-AgcWorkerLifecycle)) { throw 'lifecycle_unavailable' }
    # Do not start the worker WSL VM outside the permitted run days.
    $now = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
    $day = [int]$now.DayOfWeek
    if ($day -in @(1,2) -or ($day -eq 0 -and $now.TimeOfDay -ge [TimeSpan]::FromMinutes(1425))) { throw 'schedule' }
    $line = Read-BoundedRequest
    $request = $line | ConvertFrom-Json
    if ($request.protocol_version -ne 1 -or $request.action -notin @('status','start','heartbeat','inspect','stop','stage','fetch','ack')) { throw 'protocol' }
    $gate = Get-AgcWorkerGate
    # Any wsl -d forwarding can restart the distro. During a stop or a closed
    # gate the worker is correctly offline to the central queue; only the fixed
    # owner stop helper may act under a bound request/phase deadline.
    if (-not $gate.Allowed) { throw 'worker_stopping' }
    $running = @(Get-AgcWorkerRunningDistros -WslPath 'wsl.exe' -TimeoutMilliseconds 3000)
    if ($running -notcontains 'AGC-Worker') { throw 'worker_offline' }
    $result = Invoke-AgcWorkerAdmittedWsl -Arguments @('-d','AGC-Worker','-u','root','--exec','/usr/local/bin/agc-worker','rpc') -TimeoutMilliseconds 60000 -StandardInput ($line + "`n") -MaxOutputBytes 1048576
    if ($result.Started -ne $true -or $result.TimedOut -eq $true -or [string]::IsNullOrEmpty([string]$result.Stdout)) { throw 'rpc' }
    if ($result.OutputTruncated -eq $true) { throw 'rpc_output_truncated' }
    if ([Text.Encoding]::UTF8.GetByteCount([string]$result.Stdout) -gt 524288) { throw 'rpc_reply_too_large' }
    [Console]::Write([string]$result.Stdout)
    if ($null -ne $result.ExitCode -and [int]$result.ExitCode -ne 0) { exit 1 }
} catch {
    Write-WorkerUnavailable
}
