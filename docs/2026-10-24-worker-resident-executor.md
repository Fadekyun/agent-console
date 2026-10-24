# Resident worker owner executor (#212)

Date: 2026-10-24
Issue: Fadekyun/agent-console #212
Base: `feat/203-laptop-migration` at `6ea98848` (worker owner cycle #211)

## Proven failure

During the real Windows Preshutdown on 2026-10-23 15:54:37Z the coordinator
closed the gate and wrote the stop/cycle request, then launched the fixed owner
tasks. `schtasks /run` returned 0, but the task engine reported
`LastTaskResult = 0x8007045B` (`ERROR_SHUTDOWN_IN_PROGRESS`) and neither owner
helper ran: no process, log, local receipt or shared receipt. A fresh Task
Scheduler launch is not a valid transfer during Preshutdown, even with the
one-process-per-cycle helper from #211.

## Design

One independent, whole-boot resident process runs as the existing `agc-worker`
principal:

| Item | Value |
|---|---|
| Task | `AGC-Worker-OwnerExecutor` |
| Entrypoint | `C:\ProgramData\AGCLifecycle\owners\worker-executor.ps1` (source `ops/agc-worker/worker-executor.ps1`) |
| Action | `powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File <owners>\worker-executor.ps1 -FromTask` |
| Triggers | At startup + logon recovery |
| Settings | hidden, `IgnoreNew`, no execution time limit, `WakeToRun=false`, allowed on battery, restart on failure |
| Principal | `agc-worker`, password/batch, Limited — existing protected credential, never exported or reset |

The entrypoint checks the `agc-worker` identity, loads the shared
`C:\ProgramData\AGCLifecycle\Lifecycle.ps1` and the worker adapter, requires the
atomic admission API, and calls:

```powershell
Invoke-AgcExecutorLoop -Owner 'worker' -CycleInvoker $cycleInvoker
```

The shared loop owns the whole-boot single-instance mutex, shutdown priority,
readiness record, heartbeat and idle polling. The worker supplies only
`Invoke-AgcWorkerOwnerCycleResult`, which reuses the existing bounded
`Invoke-AgcWorkerOwnerCycle` implementation (same process, no per-cycle
PowerShell, no cold `Add-Type`, no Task Scheduler call). It returns the
`Outcome`/`Verified`/`Detail`/`ReceiptPath` contract consumed by the loop.

The cycle keeps the existing absolute +13 s graceful / +21 s terminate / +24 s
proof deadlines, the 25 s native / 30 s SCM bounds, exact-distro operations,
graceful drain first, conditional early termination only after a successful
drain, fresh complete running-list proof, per-cycle lock and duplicate/reuse
protections.

## Readiness and admission

`WorkerLifecycle.ps1` writes `details.executorPid` and
`details.executorStartUtc` into every cycle receipt from the executing process
(`$PID` and its creation time), never from the request or readiness file. The
shared readiness record is `receipts\worker\executor.json` and is validated by
the shared library: schema 1, protocol `cycle`, state `idle|busy`, live PID with
matching start time, owner SID, current boot, freshness ≤10 s and no future
heartbeat beyond 250 ms. Missing/stale/foreign/PID-reused readiness denies
worker guest admission through the shared `Test-AgcStartAllowed` path; a
resident-mode stop then stays `unconfirmed`. There is no task-launch fallback.

## Installer

`Install-LifecycleStopTask.ps1` keeps its existing behaviour (fixed stop task,
credentials, ACLs) and adds `-EnableOwnerExecutor`:

1. Copies and protects `worker-executor.ps1` in the admin-owned owners
   directory (worker read-only) and requires the
   `AGC_OWNER_RESIDENT_EXECUTOR=1` marker.
2. Backs up an existing executor task definition
   (`control\owner-executor-task.before.xml`) before replacing it.
3. Registers `AGC-Worker-OwnerExecutor` with the exact action/triggers/settings
   and the existing DPAPI credential; never disables the account, resets the
   password, exports it or broadens ACLs.
4. Starts the task while Windows is Ready and requires fresh shared readiness
   bound to the enrolled worker SID before claiming success. Failure throws and
   leaves workloads unadmitted.
5. Records executor task/script hashes and the readiness PID/start in
   `control\lifecycle-stop-task.json`.

The first rollout runs while the old B4 shared library is still installed. Use
`-EnableOwnerExecutor -DeferExecutorStart` for that pass: it copies/protects the
entrypoint, backs up and registers the fixed task **disabled** under the
existing protected credential, but does not source the new readiness library,
start the executor or claim readiness (`owner_executor_state=registered-disabled`).
Activation explicitly enables that exact task, starts it and verifies readiness
while Windows is Ready. Root then
installs the shared library/native config and starts both executors while
Windows is Ready, verifies readiness, and only then restores workloads. The
default (no defer) path still starts and verifies readiness in the same run.

The readiness-directory ACL is re-applied idempotently on every run so an
existing admin-only or partially installed `receipts\worker` cannot block the
executor. An existing `AGC-Worker-OwnerExecutor` with a foreign principal or a
different action is rejected before replacement; only its own fixed action may
be updated.

Without `-EnableOwnerExecutor` the installer only maintains the legacy stop
task, so rollback/mixed hosts keep their existing path.

## Verification

- `ops/agc-worker/tests/WorkerResident.Tests.ps1` models the 0x8007045B refused
  launch (no owner process), asserts the resident entrypoint/task contract,
  runs the real cycle invoker against a fake shared library and fake WSL, and
  proves zero Scheduler calls, verified proof, shared receipt path, and
  `executorPid`/`executorStartUtc` binding to the executing process.
- `ops/agc-worker/tests/WorkerResidentEntrypoint.Tests.ps1` executes the
  production `Invoke-AgcWorkerResidentExecutor` with a recording
  `schtasks.exe` shim on PATH, a fake shared loop and fake WSL: the real cycle
  completes, the loop is invoked once, and the shim is never called.
- Existing cycle, entrypoint-extraction and adapter suites remain green.

Linux fixtures cannot prove Windows task/ACL behavior or actual Preshutdown
survival. The real shared readiness/admission regression runs against
`AGC_LIFECYCLE_REAL_LIBRARY` (currently display `823f123`): on Windows it uses
the real owner SID and process-token probe; on Linux it mocks only that narrow
process-owner boundary via `AgcProcessOwnerProbeOverride` while exercising the
real readiness writer, validator and admission gate. It reports SKIP (not PASS)
when the library or a SID is unavailable.
 Root owns the installer run, the zero-launch SCM rehearsal, the single
normal restart, and the acceptance evidence that the preexisting executor PID
produced the bound receipts.

## Rollout and rollback

Deploy the reviewed worker files/installer, run it with `-EnableOwnerExecutor`
while the lifecycle service is Ready, verify readiness, then let root enable the
native `executorTask` for the worker. The one-shot `AGC-Worker-LifecycleStop`
task stays registered for legacy/rollback configurations.

Rollback: restore the prior `worker-stop.ps1`/`WorkerLifecycle.ps1` and the
`control/lifecycle-stop-task.before.xml` / `owner-executor-task.before.xml`
definitions, unregister `AGC-Worker-OwnerExecutor` only after the coordinator no
longer targets it, clear the native `executorTask`, and restore the previous
worker source through the held Setup lane. Preserve receipts, logs, leases and
unknown outcomes; never rewrite the qualification seal. Do not restart
automatically.
