# Persistent worker-account stop task. Never starts a stopped distro, including
# outside run days. The fixed task action supplies no request id or executable:
# identity, RequestId and deadline come from the protected AGCLifecycle gate.
[CmdletBinding()]
param(
    [ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$ExpectedSid,
    [ValidatePattern('^[a-f0-9]{32}$')][string]$RequestId
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\agc-worker$') { throw 'Worker stop identity mismatch' }
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($ExpectedSid -and $currentSid -ne $ExpectedSid) { throw 'Worker stop identity mismatch' }
$Data = 'C:\ProgramData\AGC-Worker\data'
. (Join-Path $PSScriptRoot 'WorkerLifecycle.ps1')
$lifecycle = Initialize-AgcWorkerLifecycle
$gate = if ($lifecycle) { Get-AgcWorkerGate } else { $null }
$stopRequest = if ($gate) { $gate.StopRequest } else { $null }
$effectiveRequest = $RequestId
if ($stopRequest -and $stopRequest.RequestId) {
    if ($RequestId -and $RequestId -ne [string]$stopRequest.RequestId) {
        $receipt = @{version=1;request_id=$RequestId;worker_sid=$currentSid;state='unknown';
            termination_confirmed=$false;outcome='request_mismatch';error_type='request_mismatch';
            message='Stop request does not match the protected gate; no worker action taken';
            started_at=[DateTimeOffset]::UtcNow.ToString('o');finished_at=[DateTimeOffset]::UtcNow.ToString('o')}
        [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $Data -RequestId $RequestId)
        exit 1
    }
    $effectiveRequest = [string]$stopRequest.RequestId
}
if (-not $effectiveRequest) {
    $receipt = @{version=1;request_id='none';worker_sid=$currentSid;state='unknown';
        termination_confirmed=$false;outcome='no_stop_request';error_type='no_stop_request';
        message='No protected stop request; no worker action taken';
        started_at=[DateTimeOffset]::UtcNow.ToString('o');finished_at=[DateTimeOffset]::UtcNow.ToString('o')}
    [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $Data -RequestId 'none')
    Write-AgcWorkerLog -Event 'worker_stop_refused' -Data @{reason='no_stop_request'}
    exit 1
}
if (-not $lifecycle) {
    # An explicit operator rollback request may still terminate the exact
    # distro, but no shared receipt can be claimed without the library.
    $receipt = @{version=1;request_id=$effectiveRequest;worker_sid=$currentSid;state='unknown';
        termination_confirmed=$false;outcome='lifecycle_unavailable';error_type='lifecycle_unavailable';
        message='Protected lifecycle library unavailable; no worker action taken';
        started_at=[DateTimeOffset]::UtcNow.ToString('o');finished_at=[DateTimeOffset]::UtcNow.ToString('o')}
    [void](Save-AgcWorkerLocalStopReceipt -Receipt $receipt -DataPath $Data -RequestId $effectiveRequest)
    exit 1
}
$result = Invoke-AgcWorkerScopedTerminate -RequestId $effectiveRequest -ExpectedSid $currentSid `
    -StopRequest $stopRequest -Reason 'fallback_stop_task' -DataPath $Data -Phase 'terminate'
if ($result.termination_confirmed -ne $true) { exit 1 }
exit 0
