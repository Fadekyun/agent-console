# Verbatim display AGCLifecycle owner-cycle compatibility helpers.
# Source: deploy/windows/lifecycle/install-lifecycle.ps1
# Display source commit: fc3cc85 (file sha256 ff7eb3eccf1c314f78f35aaafc7bd780ca17a8849db8139a79ee8539806ba7e0)
# Extracted only; do not edit. The capability regression executes these exact
# functions against a mocked worker task and the real worker entrypoint file.

function Get-AgcTaskActionTarget {
    param([string]$TaskName)
    $result = [pscustomobject]@{ Ok = $false; Detail = ''; Script = ''; Arguments = '' }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { $result.Detail = 'task is not registered'; return $result }
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { $result.Detail = 'task must have exactly one action'; return $result }
    $execute = [string]$actions[0].Execute
    $arguments = [string]$actions[0].Arguments
    if ($execute -notmatch '(?i)(^|\\)powershell\.exe$') { $result.Detail = 'action must run powershell.exe'; return $result }
    # Fixed -File <script> only: an inline -Command or a phase argument would not
    # be the reviewed helper contract.
    $match = [regex]::Match($arguments, '(?i)-File\s+(?:"([^"]+)"|(\S+))')
    if (-not $match.Success) { $result.Detail = 'action must pass -File <script>'; return $result }
    $result.Script = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
    $result.Arguments = $arguments
    $result.Ok = $true
    return $result
}

function Test-AgcOwnerCycleCapability {
    <# A single-cycle helper may only be enabled when the fixed task action and
       the installed helper itself are verified. The helper declares
       `# AGC_OWNER_CYCLE_PROTOCOL=1`; legacy per-phase helpers do not, so a
       mixed-version rollout keeps the phase fallback. #>
    param([string]$TaskName, [string]$Owner, [string]$ExpectedScript = '', [string]$ExpectedSid = '')
    $capability = [pscustomobject]@{ Ok = $false; Detail = ''; Script = '' }
    $action = Get-AgcTaskActionTarget -TaskName $TaskName
    if (-not $action.Ok) { $capability.Detail = $TaskName + ': ' + $action.Detail; return $capability }
    if (-not [string]::IsNullOrEmpty($ExpectedScript)) {
        if ([IO.Path]::GetFullPath($action.Script) -ne [IO.Path]::GetFullPath($ExpectedScript)) {
            $capability.Detail = 'task action script is not the installed cycle helper; expected ' + $ExpectedScript
            return $capability
        }
    }
    if (-not [string]::IsNullOrEmpty($ExpectedSid)) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $principal = [string]$task.Principal.UserId
        $taskSid = ''
        if ($principal -match '^S-1-') { $taskSid = $principal } else { $taskSid = ([Security.Principal.NTAccount]::new($principal)).Translate([Security.Principal.SecurityIdentifier]).Value }
        if ($taskSid -ne $ExpectedSid) { $capability.Detail = 'task principal is not the expected owner SID'; return $capability }
    }
    if ($action.Arguments -notmatch '(?i)(^|\s)-FromTask(\s|$)') { $capability.Detail = 'task action must use the fixed -FromTask arguments'; return $capability }
    if ($action.Arguments -match '(?i)(^|\s)--?phase(\s|=|$)') { $capability.Detail = 'task action must read the phase from the request, not an argument'; return $capability }
    if (-not (Test-Path -LiteralPath $action.Script)) { $capability.Detail = 'task action script is missing: ' + $action.Script; return $capability }
    $content = [IO.File]::ReadAllText($action.Script)
    if ($content -notmatch '(?m)^\s*#\s*AGC_OWNER_CYCLE_PROTOCOL=1\s*$') { $capability.Detail = 'helper does not declare # AGC_OWNER_CYCLE_PROTOCOL=1'; return $capability }
    $capability.Script = $action.Script
    $capability.Ok = $true
    return $capability
}
