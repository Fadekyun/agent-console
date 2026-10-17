# Limited worker-account stop task. Never starts a stopped distro, including
# outside run days. No caller-supplied executable, distro or shell is accepted.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$ExpectedSid,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$RequestId
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $ExpectedSid) { throw 'Worker stop identity mismatch' }
$Data='C:\ProgramData\AGC-Worker\data'
$Receipt=Join-Path $Data "stop-$RequestId.json"
$result=@{version=1;request_id=$RequestId;worker_sid=$ExpectedSid;state='stopping';termination_confirmed=$false;started_at=[DateTimeOffset]::UtcNow.ToString('o')}
$sequence=0
function Invoke-BoundedWsl([string[]]$Arguments,[int]$TimeoutSeconds=20) {
    $script:sequence++
    $out=Join-Path $Data "stop-$RequestId-$sequence.out"
    $err=Join-Path $Data "stop-$RequestId-$sequence.err"
    $process=Start-Process -FilePath wsl.exe -ArgumentList $Arguments -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    try {
        if (-not $process.WaitForExit($TimeoutSeconds*1000)) {
            $process.Kill()
            throw 'Worker stop command timeout; termination remains unconfirmed'
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw 'Worker stop command failed; termination remains unconfirmed' }
        return (([IO.File]::ReadAllText($out) -replace "`0",'') -split "`r?`n" | ForEach-Object {$_.Trim()})
    } finally { $process.Dispose() }
}
try {
    $running=@(Invoke-BoundedWsl -Arguments @('--list','--running','--quiet'))
    if ($running -contains 'AGC-Worker') {
        try {
            # Do not use `wsl -d ... --exec` here: a concurrent watchdog stop
            # after the running-list check could make that command restart WSL
            # on a closed day. Direct termination can only stop this distro.
            Invoke-BoundedWsl -Arguments @('--terminate','AGC-Worker') -TimeoutSeconds 25 | Out-Null
        } catch {
            # A racing normal shutdown may make terminate report failure.
            # Only the subsequent independent running-list check proves stop.
            $result.terminate_command_error=$true
        }
    }
    $remaining=@(Invoke-BoundedWsl -Arguments @('--list','--running','--quiet'))
    if ($remaining -contains 'AGC-Worker') { throw 'Worker distro still running' }
    $result.state='stopped';$result.termination_confirmed=$true
} catch {
    $result.state='unknown';$result.error_type=$_.Exception.GetType().Name
    $result.message='Worker shutdown not confirmed; retain queue reservations and inspect stop task'
} finally {
    $result.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
    $result|ConvertTo-Json|Set-Content -LiteralPath "$Receipt.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$Receipt.tmp" -Destination $Receipt -Force
}
if (-not $result.termination_confirmed) { exit 1 }
