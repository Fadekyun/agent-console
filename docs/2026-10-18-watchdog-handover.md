# Held Windows watchdog handover (#203)

This source-only operator helper covers the narrow gap between the old Windows
watchdog and the three-file Linux upgrade. Root reviews and deploys it. It does
not release work, run qualifiers, change task triggers/actions/principals, enter a
staff distribution, change an account, or start/terminate a distribution as an
intended operation. The only existing runtime code replaced is
`code/Start-Worker.ps1`; the new policy verifier stays in the protected operator
stage. No assumptions are made about a verifier in `code/`.

The old watchdog's `finally` stops worker units and terminates `AGC-Worker`.
The `host-sample` command writes a file directly; it is not an RPC. Its writer
does not create the parent directory, and systemd removes `RuntimeDirectory`
on stop. Consequently a Linux supervisor restart while either watchdog is
running can fail telemetry delivery and enter that cleanup. Keep the Windows
watchdog stopped throughout the existing Linux upgrade.

## Review and stage

Files are `ops/agc-worker/Handover-WorkerWatchdog.ps1`,
`Invoke-WatchdogHandoverGuard.ps1`, and `watchdog_handover_guard.py`. Transfer only
these reviewed sources to an administrator-protected child of
`C:\ProgramData\AGC-Worker-Provision-20261017\source`. Preserve the existing
protected Setup/Invoke/Host-Disk files. Parse both new PowerShell files with the
native parser, compare their source hashes, and separately compile/review the
embedded handle wrapper before execution. The helper also parses the new Start
and guard launcher before mutation. Source tests are not native qualification.

The helper pins the old Start hash
`af163166235c9221843b2807dcf355c3090874cd4dce7e7600072239ff999225`, new Start
`4214a5aa9738f2367c85d76a3b00c48d85e5018f6d123d27c4f491da5ebfd103`, and final
worker `c6dacde95e76069672f4ab1a1524e0f997de83388c3700d57af0982ae23018d8`.
The existing private-upgrade bundle supplies Start and the separately staged
verifier `0559dc332bc39cffa75908303ad125cbc096d2f86e45228186dba201c3929f75`.
Nothing copies credentials out of their existing Windows protected store.

Fresh observations must include:

- Worker SID and task principal resolved to that exact SID; task enabled,
  limited/password logon, one exact action and complete XML.
- Hidden running COM task: `Schedule.Service.GetRunningTasks(1)`, exact
  `InstanceGuid` and `EnginePID`. Flag0 hides this task. The engine PID here is
  the PowerShell action PID, **not its parent**.
- Native `Get-Process -Id <pid>` StartTime, rendered as
  `.StartTime.ToUniversalTime().ToString('o')`, and its `.ToFileTimeUtc()`.
  Do not use CIM CreationDate: the observed CIM timestamp truncated 100ns and
  would correctly fail the native handle comparison. CIM supplies command line
  and parent metadata only. Native handle supplies owner SID, image and time.
- Worker boot, local/central holds and no active/unknown work or populated job
  cgroups. Staff boot/config must be separately observed by root without
  starting a stopped staff distribution. The helper checks the config hash and
  records the supplied fresh boot attestation; it does not claim to have read
  the staff boot itself.
- Permitted day, AC, sufficient staff headroom, and a free serialized Setup
  lane. Budget ten minutes for **upgrade and finalization**, not for the later
  native/toolchain qualifications.

## Prepare, upgrade, finalize

Use a fresh 32-lowercase-hex transaction and exact live pins, never the historical
PID/time/boot values in old reports. Call the staged helper with `-Phase Prepare`,
`-Transaction`, `-ExpectedPid`, `-ExpectedCreationUtc`, `-ExpectedWorkerSid`,
`-ExpectedTaskInstance`, `-WorkerBootId`, `-StaffBootId`,
`-StaffConfigSha256`, `-StaffObservedAt`, `-NewStartFile`, `-GuardSha256` and
`-GuestGuardSha256`. Staff observations must be no older than120seconds.

Prepare does the following:

1. Takes a protected operator lock; validates exact source, ACL ancestry,
   process and task identity; saves old Start bytes/SDDL and full task XML under
   `control/watchdog-handover/<transaction>/`. Existing journals, guard tasks,
   source directories or receipts require reconciliation and refuse replay.
2. Stages fixed guard source and a protected plan under `code/handover/`.
   Registers `AGC-Worker-HandoverGuard` using the existing DPAPI credential, as
   the limited worker account: hidden, no triggers, no retries,600second limit.
   It neither replaces nor occupies `AGC-Worker-Setup`.
3. While the old pinned watchdog is still alive, the guard checks the running
   list and establishes **one** WSL client running the bounded Python monitor.
   A list check alone cannot remove the WSL attach/start race; a same-boot,
   held/idle/empty handshake is mandatory before destructive actions. The guard
   never reconnects if that client exits. Its Python process uses both wall and
   monotonic deadlines and only reads hold/worker/cgroup/status information.
4. Journals intent, disables **only Background's enabled bit**, verifies every
   other task setting unchanged, replaces Start preserving exact ACL, and
   terminates only the retained old PowerShell handle after rechecking its
   creation/owner/image/CIM metadata and exact hidden task instance. It waits
   for that handle to signal, bypassing the old PowerShell cleanup.
5. Before terminating the old PowerShell, retains native handles for its exact
   System32 WSL client and `C:\Program Files\WSL\wsl.exe` shim, requiring the
   existing sleep-infinity chain and capturing any visible fixed host-sample
   clients. Owner SID, image, full native creation time, parent identity and
   exact action are journaled. CIM's timestamp is only cross-checked within its
   microsecond precision; native handles remain the authority.
6. Only after confirmed PowerShell death, disabled scheduling, fresh guard and
   absence of a replacement instance, stops the same old COM instance if it
   remains. It then terminates only surviving retained WSL client handles,
   deepest-first, and waits for every handle to signal. A disappearing COM
   instance alone is not proof that clients died. Never kill conhost, wslhost,
   an arbitrary PID tree, task engine parent, or worker/staff VM. An uncaptured
   late-born client is observed for bounded natural exit; it is never promoted
   to a PID-based kill target. Any remainder refuses success. Same held boot is
   required after cleanup; all retained clients must be confirmed dead.
7. Returns `awaiting_linux_upgrade`, leaving at least400seconds on the guard.
   Save its exact deadline and journal. Background is disabled and stopped.

Root now runs only the reviewed `upgrade-linux.sh` through existing Setup,
with its180second timeout. Inspect the protected Setup/Linux receipts. Do not
run qualification builds in this guard window. The monitor can report status
unavailable during a supervisor restart without inventing successful readiness.
The original local hold is never removed.

After successful upgrade, freshly observe staff boot/config again. Invoke the
same helper `-Phase Finalize -Transaction ... -StaffBootId ...
-StaffConfigSha256 ... -StaffObservedAt ...`. It requires at least90seconds of
fresh guard time, no running Setup/Background, the same boot, protected new
Start, exact new Linux worker, held/idle status and empty job cgroups. It records
intent, restores the original enabled bit and starts/adopts the one existing
Background task. It requires the new native handle and correct unique keeper,
at least8seconds alive, and a **fresh** host sample newer than that process
containing `qualified_worker_sha256` (null is valid before fresh policy sealing).
An old sample or keeper appearance alone cannot pass.

`completed_held_guard_expires` means the new watchdog has taken over under the
same worker boot and root's matching staff attestation. The independent guard
expires naturally by its deadline. Preserve its final receipt and confirm its
task exits without affecting either boot. Root can then use Setup for fresh
native/toolchain qualification and the separate policy-sealing/release gates.
This helper grants no admission release.

## Failure and rollback

Preparation before the disable intent leaves the old watchdog unchanged; preserve
its failed journal and any bounded guard. After mutation begins, failures persist
`failed_requires_reconciliation` and attempt to leave Background disabled.
Disable alone does **not** stop a running new watchdog. A finalization failure
therefore needs exact process/boot inspection, not a generic Stop-ScheduledTask.
The guard expires even if the operator disconnects; it never runs the old
termination cleanup. No automatic code rollback, re-enable, distro start or
new guard is attempted. The system may naturally stop when all keeper clients
end; this is not permission to restart it or ignore a changed boot.

Do not edit/delete a journal to replay Prepare/Finalize, extend a deadline, or
restore old code and start it. An incomplete operation needs root to compare
intent, files/ACL, enabled bit, native handles, COM instances, guard state and
both boot observations, then approve a new exact repair. The preserved old Start
is evidence, not an automatic runnable fallback. Keep central and local holds.
Guard-task/source/journal cleanup is a separate reviewed operation after receipt
capture; never unregister a running guard to tidy up.

Verification performed in the source worktree: eight bounded Python fixtures
cover changed boot/missing hold, populated job cgroups, unknown source hash,
clock rollback/deadline expiry, absence of reconnect/mutation, destructive action
ordering, retained-client cleanup and host-sample finalization guards. Whitespace checks pass. Native
PowerShell parsing, COM behavior, ACL preservation and handover are still
operator acceptance checks; no laptop action was performed by these tests.
