# Worker lifecycle gate and bounded shutdown (#206)

Date: 2026-10-22
Issue: Fadekyun/agent-console #206
Base: deployed worker `6bd6bba` (`fix/203-watchdog-handover`)

This change connects the existing independent worker to the owner-approved
Windows `AGCLifecycle` coordinator. Source and local fake-CLI tests only; the
worker remains qualified by its existing hold until root requalifies the changed
`worker.py` hash. No account, credential, quota, task principal or central job
policy is changed here.

## Admission

`Start-Worker.ps1` calls the shared `Test-AgcStartAllowed` through
`WorkerLifecycle.ps1` before creating any WSL process, and repeats the gate
before each telemetry delivery, keepalive restart and supervisor start. A
refused start logs `worker_start_suppressed` and exits `0`. A stop request is
read through `Get-AgcStopRequest`; its `RequestId` and `DeadlineUtc` are bound to
the drain receipt. `Dispatch-Worker.ps1` refuses all RPC forwarding when the gate is closed and
bounds both its stdin read and RPC call. It captures up to 1 MiB, rejects
`OutputTruncated` as `rpc_output_truncated`, and rejects a reply above the
512 KiB protocol maximum as `rpc_reply_too_large`; it never writes partial JSON
to the central client. Host telemetry
uses the existing per-SID quota counters for `worker_disk_used_bytes` instead of
a recursive data-tree scan; an unavailable quota fails closed at the 300 GB
ceiling. Missing-library startup exits without any synchronous fallback log I/O.

The protected library is fixed at
`C:\ProgramData\AGCLifecycle\Lifecycle.ps1`. The adapter restores
`Set-StrictMode -Off` after dot-sourcing the library and uses explicit null-safe
property access, so a real-library StrictMode 2.0 cannot turn an observed
gate/qualification null into `PropertyNotFoundException`. Ordinary `wsl -d`
commands use
`Invoke-AgcBoundedProcess -RequireAdmission -Owner worker`, which holds the same
global launch mutex as the coordinator, rechecks admission under it, starts
under the lock and releases before stdin/output/wait. A refusal returns
`Started=false`/`Suppressed=true`/`ExitCode=0` and creates no process. Only the
fixed owner stop/drain/list/terminate helper omits `RequireAdmission` and works
under its bound request/phase deadline. If the library lacks this API, the
worker fails closed: no WSL launch, no stop verified, and no PASS receipt. The shared library remains owned by the Windows
coder; this repo only consumes its documented interface.

## Stop path

Owner budget fits the coordinator's 25 s internal budget:

| Phase | Budget | Worker action |
|---|---|---|
| Stop / graceful (parallel owners) | 12 s | stop `agc-worker.service` through WSL; the supervisor checkpoints and terminates only its exact job cgroup |
| Scoped fallback | 8 s | `wsl.exe --terminate AGC-Worker` only, after an independent running-list check |
| Verify | 3 s | independent `wsl.exe --list --running --quiet`; only absence is confirmation |

The fallback never uses `wsl -d ... --exec` (which can start a stopped distro),
never `wsl --shutdown`, and never kills `WSLService`, `vmcompute` or `vmmem`.
When `Start-Worker` observes a validated coordinator stop, it releases only its
owned keeper and exits: the fixed `AGC-Worker-LifecycleStop` helper owns the
grace/terminate/verify phases. The launcher never runs `wsl -d` or `--terminate`
for a coordinator stop. For a local schedule/pressure drain while admission is
still open it may run an admitted bounded guest `systemctl stop` and then one
scoped exact-distro fallback. An unhealthy gate with no validated request fails
closed after releasing the keeper and performs no WSL operation.
A running-list result is evidence only with `Started=true`, `ExitCode=0`, no
timeout and no truncated output. `Started=false`, null exit, timeout, truncation
or an exhausted timeout is an error and can never become an empty "stopped"
claim. Each helper uses one monotonic phase budget; no timeout floor extends the
coordinator deadline.
The existing transient `AGC-Worker-Stop` task created by
`Provision-Worker.ps1 -Phase Disable` remains a rollback-only path.

### Persistent owner task

`Install-LifecycleStopTask.ps1` registers/updates
`AGC-Worker-LifecycleStop` with a fixed action:

```
powershell.exe ... -File C:\ProgramData\AGCLifecycle\owners\worker-stop.ps1 -FromTask
```

It uses the existing `agc-worker` password-task principal and the existing
LocalMachine-DPAPI credential. It never enables/disables the account, never
rotates the password and backs up an existing task definition before update.
The owner helper rejects SYSTEM (`S-1-5-18`) and any non-`agc-worker` identity,
so the coordinator may start the fixed task but cannot use the helper as a
SYSTEM fallback. It reads `requests\worker\current.json` (admin-owned) and
validates `schema`, `owner`, `requestId`, `bootId`, `deadlineUtc` and agreement
with the registry gate before any WSL command.

**Task mapping:** Windows coordinator configuration must map `stopTask`,
`terminateTask` and `verifyTask` to the same phase-aware
`AGC-Worker-LifecycleStop` task; the helper selects the phase from the request
file and writes `<requestId>.<phase>.json`.

**Coordinator interface dependency:** the request file must include a `phase`
field with `stop`, `terminate` or `verify`. The helper defaults an absent or
unknown phase to `verify`; it never force-terminates before the `stop` phase
has been attempted. The request schema uses `phase=stop|terminate|verify`.
Owner receipts are phase-bound: `receipts\worker\<requestId>.<phase>.json`, with
top-level `phase` plus `schema`, `owner`, `requestId`, `bootId`, `outcome`,
`stopped`, `verified`, `scope`, `source`, account name/SID, distro and details.
The adapter maps local `forced_scoped`/`stopped_absent` to the library's
`forced`/`graceful` vocabulary and preserves the local word in `details`; a
failed shared-receipt write sets `shared_receipt_written=false` and a visible
`sharedReceiptError` in the local receipt.
The helper forwards the explicit `-Phase` to `Write-AgcStopReceipt`; it does not
write an unphased receipt that the coordinator would reject.

## Guest shutdown

- `SIGTERM` closes admission through a lock-free `threading.Event` before any
  lock acquisition. A watchdog that is hashing artifacts therefore cannot block
  admission closure.
- Shutdown terminates an active job with `defer_result=True`: the supervisor
  stops the exact unit, preserves the receipt and output, and records
  `result_deferred`/`result_deferred_reason` for central reconciliation. It does
  not claim an inventory it did not collect.
- Ordinary job completion keeps the existing full inventory contract; there is
  no global scan deadline. An in-progress scan observes shutdown cancellation
  between files/chunks.
- `LEASE_STOP_SECONDS = 75` remains lease-loss detection only. The supervisor's
  constrained shutdown budget is 11 s: 1 s watchdog join + 1 s state-lock wait
  (original value, not increased) + 2 s checkpoint signal + 5 s bounded job
  `systemctl stop` + 2 s empty check, inside the 12 s host stop phase. A stop command timeout is not treated
  as proof; `unit_empty` still decides and the coordinator records an
  unconfirmed phase rather than extending its deadline. Actual Windows timing
  remains root-owned; the arithmetic is a bound, not a measured guarantee.
- Guest fencing, generation checks, duplicate-start receipts and unknown
  external-write handling are unchanged.

## Logs and managed mode

`install-linux.sh` creates `/etc/agc-worker/managed-mode`, disables independent
boot enablement of `agc-worker.service` (the Windows gated launcher starts it
manually), writes a 128 MiB / 7-day journald drop-in with 8 MiB files, stops a
duplicate rsyslog copy in this dedicated VM, and vacuums once (the active
journal segment may survive until rotation). `worker.py` queues low-volume JSONL transitions to a daemon writer for
`/var/log/agc-worker/worker.log` with 1 MiB rotation, seven backups plus the active file (8 MiB total cap)
and 7-day age pruning at startup and hourly. A stalled disk or full queue can
drop diagnostic records but cannot block admission or job termination; receipts,
objects, jobs, leases and data are never retention candidates.

## Verification performed locally

- `python -m pytest -q tests/ops` -> 78 passed (includes new shutdown,
  cancellation, lock-free admission, stalled-logger, fencing and log-retention
  regressions plus source contract checks).
- `pwsh -NoProfile -File ops/agc-worker/tests/WorkerLifecycle.Tests.ps1` ->
  PASS with a fake `Lifecycle.ps1` and fake `wsl` executable: gate refusal,
  stop-request binding, remaining-deadline clamp, absent/running distro
  receipts, graceful-then-scoped fallback ordering, missing-library fail-closed,
  and static bound/identity checks.
- PowerShell parser passed for every changed `.ps1` under PowerShell 7.4.6.
  Windows PowerShell 5.1 parsing, C# compilation, scheduled-task registration,
  live WSL termination and the physical shutdown remain parent/root-owned.

## Rollout (root-owned)

1. Hold central dispatch and drain the worker.
2. Install the reviewed Windows lifecycle library/service and owner directory.
3. Copy `worker.py`, `WorkerLifecycle.ps1` and `Stop-Worker.ps1` to protected
   `C:\ProgramData\AGC-Worker\code`; run `Install-LifecycleStopTask.ps1` from the
   administrator context. The existing account remains enabled with its current
   password/DPAPI credential.
4. Deploy the changed guest files through the bounded Setup lane; the installer
   leaves `/etc/agc-worker/hold` in place and does not release qualification.
5. Re-run native/toolchain qualification for the new `worker.py` hash, then
   release only the Linux hold through the existing explicit gate. The central
   hold and workload cutovers remain separate.
6. Run an attended stop/restart test. Do not claim sleep/shutdown behavior from
   source tests.

## Rollback

1. Hold dispatch and preserve receipts, objects, leases and unknown outcomes.
2. Restore the previous protected `code` files and task definition from the
   installer backup (`control/lifecycle-stop-task.before.xml`); unregister the
   `AGC-Worker-LifecycleStop` task only after the coordinator no longer invokes
   it.
3. Reinstall the previous worker source through the same held Setup lane. The
   prior worker hash must be requalified or kept held; never rewrite the policy
   seal to skip tests.
4. Restore account/task state only from the recorded original; do not delete the
   distro, receipts, artifacts or staff configuration.

## Unverified limits

- Windows PowerShell 5.1 `ScheduledTasks`/DPAPI execution and task ACL behavior.
- Real coordinator request-file `phase` integration (schema addition pending).
- Real guest systemd stop timing, WSL termination races, and whether a full
  Windows shutdown completes inside the 25 s budget.
- Physical sleep/power behavior and staff-desktop impact.
- Actual `journalctl --vacuum-*` duration on a full disk; the command is bounded
  by `timeout 30` but active-segment retention is not guaranteed.
