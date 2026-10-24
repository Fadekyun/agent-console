# Cross-integration tests against the real Windows Lifecycle.ps1.
# Requires AGC_LIFECYCLE_REAL_LIBRARY=<path> and a Linux/pwsh test host with
# python3. Test mode redirects the gate/receipts root to /tmp; production paths
# are never touched. Native Windows WSL/task/DPAPI behaviour remains root-owned.
$ErrorActionPreference = 'Stop'
$library = $env:AGC_LIFECYCLE_REAL_LIBRARY
if (-not $library -or -not (Test-Path -LiteralPath $library -PathType Leaf)) {
    Write-Host 'real lifecycle tests: SKIP (AGC_LIFECYCLE_REAL_LIBRARY absent)'
    exit 0
}
$root = Join-Path ([IO.Path]::GetTempPath()) ('agc-real-lifecycle-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$gateFile = Join-Path $root 'gate.json'
$env:AGC_LIFECYCLE_TEST_MODE = '1'
$env:AGC_LIFECYCLE_ROOT = $root
$env:AGC_LIFECYCLE_GATE_FILE = $gateFile
$env:AGC_LIFECYCLE_TEST_SERVICE_STATE = 'Running'
$script:Failures = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if ($Condition) { Write-Host "ok $Name" } else { Write-Host "FAIL $Name"; $script:Failures++ }
}
$requestId = '0123456789abcdef0123456789abcdef'
@{state='Ready';bootId='b20260101T000000Z';requestId='';deadlineUtc='';heartbeatUtc=[DateTime]::UtcNow.ToString('o');reason='test'} |
    ConvertTo-Json | Set-Content -LiteralPath $gateFile -Encoding UTF8
. (Join-Path $PSScriptRoot '..\WorkerLifecycle.ps1') -LibraryPath $library -DataPath (Join-Path $root 'data')
Assert-True (Initialize-AgcWorkerLifecycle -Path $library) 'real library loads through adapter'
Assert-True (Test-AgcWorkerAdmissionApi) 'real library exposes required admission API'

$gen = Join-Path $root 'fetch.py'
@'
import base64, json
print(json.dumps({"protocol_version":1,"ok":True,"data":base64.b64encode(b"x"*(256*1024)).decode()}))
'@ | Set-Content -LiteralPath $gen -Encoding UTF8
$large = Invoke-AgcBoundedProcess -FilePath '/usr/bin/python3' -ArgumentList @($gen) -TimeoutMilliseconds 20000 -MaxOutputBytes 1048576
Assert-True ($large.Started -eq $true -and $large.ExitCode -eq 0) 'real bounded process starts'
Assert-True ($large.OutputTruncated -eq $false -and $large.Stdout.Length -gt 300000) 'real library returns large 256KiB-chunk reply'
$parsed = $large.Stdout | ConvertFrom-Json
Assert-True ($parsed.ok -eq $true -and $parsed.data.Length -gt 300000) 'large fetch reply parses as JSON'
$small = Invoke-AgcBoundedProcess -FilePath '/usr/bin/python3' -ArgumentList @($gen) -TimeoutMilliseconds 20000
Assert-True ($small.OutputTruncated -eq $true) 'default 64KiB cap reports truncation'

$stdinHash = Join-Path $root 'stdin-hash.py'
@'
import base64, hashlib, sys
raw = base64.b64decode(sys.stdin.buffer.read().strip(), validate=True)
print(hashlib.sha256(raw).hexdigest())
'@ | Set-Content -LiteralPath $stdinHash -Encoding UTF8
$rawPayload = New-Object byte[] 1500000
for ($i = 0; $i -lt $rawPayload.Length; $i++) { $rawPayload[$i] = 0x41 }
$payload = [Convert]::ToBase64String($rawPayload)
$expected = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($rawPayload)).Replace('-','').ToLowerInvariant()
$stdinResult = Invoke-AgcBoundedProcess -FilePath '/usr/bin/python3' -ArgumentList @($stdinHash) -TimeoutMilliseconds 20000 -StandardInput $payload -MaxOutputBytes 4096
Assert-True ($stdinResult.Started -eq $true -and $stdinResult.ExitCode -eq 0 -and $stdinResult.Stdout.Trim() -eq $expected) 'large setup-style stdin is preserved exactly'

$threw = $false
try { Write-AgcStopReceipt -Owner worker -RequestId $requestId -Outcome 'forced_scoped' -Data @{} -Phase terminate } catch { $threw = $true }
Assert-True $threw 'real ValidateSet rejects unmapped local outcome'

$fakeWsl = Join-Path $root 'fake-wsl.sh'
@'
#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_WSL_STATE/calls"
if [ "$1" = "--list" ]; then [ -f "$FAKE_WSL_STATE/running" ] && echo AGC-Worker; exit 0; fi
if [ "$1" = "--terminate" ]; then rm -f "$FAKE_WSL_STATE/running"; exit 0; fi
exit 0
'@ | Set-Content -LiteralPath $fakeWsl -Encoding UTF8
& chmod +x $fakeWsl
$state = Join-Path $root 'wslstate'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$env:FAKE_WSL_STATE = $state
'1' | Set-Content -LiteralPath (Join-Path $state 'running')
$stop = [pscustomobject]@{RequestId=$requestId;BootId='b20260101T000000Z';DeadlineUtc=[DateTimeOffset]::UtcNow.AddSeconds(15).ToString('o')}
$result = Invoke-AgcWorkerScopedTerminate -RequestId $requestId -ExpectedSid 'S-1-5-21-1' -StopRequest $stop -WslPath $fakeWsl -DataPath (Join-Path $root 'data') -Phase terminate
Assert-True ($result.termination_confirmed -eq $true) 'real shared receipt path confirms scoped stop'
Assert-True ($result.shared_receipt_written -eq $true) 'adapter reports shared receipt written'
$receiptPath = Join-Path $root ('receipts\worker\' + $requestId + '.terminate.json')
Assert-True (Test-Path -LiteralPath $receiptPath) 'real phase-bound shared receipt exists'
if (Test-Path -LiteralPath $receiptPath) {
    $shared = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    Assert-True ($shared.outcome -eq 'forced' -and $shared.phase -eq 'terminate') 'shared receipt outcome/phase mapped'
    Assert-True ($shared.stopped -eq $true -and $shared.verified -eq $true) 'shared receipt stopped/verified'
}

# StrictMode must not leak from the real library into a caller's null-safe path.
Set-StrictMode -Version 2.0
Remove-Item -LiteralPath $gateFile -ErrorAction SilentlyContinue
$gate = Get-AgcWorkerGate
Assert-True ($gate.Allowed -eq $false -and $null -ne $gate.StopRequest -and (Get-AgcWorkerValue $gate.StopRequest 'FailSafe') -eq $true) 'missing gate fails closed under strict mode'
Assert-True ($null -eq (Get-AgcWorkerValue $null 'Anything')) 'null-safe property helper'
Assert-True ($null -eq (Get-AgcWorkerValue ([pscustomobject]@{}) 'Missing')) 'missing property helper'

Write-Host ("real lifecycle tests: " + $(if ($script:Failures -eq 0) { 'PASS' } else { "$script:Failures FAILURE(S)" }))
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failures -ne 0) { exit 1 }
