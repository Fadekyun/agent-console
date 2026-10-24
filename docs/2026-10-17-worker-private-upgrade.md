# Held laptop private-job upgrade (#203)

This is a source-only, reviewed candidate for the offline AGC staff laptop.
Root owns reconnect checks, staging, integration and execution. Keep the central
queue owner hold enabled throughout. The target worker is
`c6dacde95e76069672f4ab1a1524e0f997de83388c3700d57af0982ae23018d8`;
the explicitly expected old worker is
`c4e193682e3cdcbce5ce90ede8cb00687550b67908db29b40ef908b9d6782e02`.
No old native receipt, CT102 two-case fixture or historical staff boot ID grants
permission to release this laptop.

## Reproduce and inspect the bundle

From the reviewed Console checkout, run:

```sh
python3 ops/agc-worker/prepare-private-job-upgrade.py \
  --repo /home/agentstage/codex/worktrees/worker-private-upgrade-20261017 \
  --source-revision 57d70994fea0db11cf52ea0725ae779ea48fa631 \
  --old-worker-sha256 c4e193682e3cdcbce5ce90ede8cb00687550b67908db29b40ef908b9d6782e02 \
  --new-worker-sha256 c6dacde95e76069672f4ab1a1524e0f997de83388c3700d57af0982ae23018d8 \
  --output /tmp/agc-worker-private-job-upgrade-20261017-r3
```

The output directory must be new: regeneration never overwrites a reviewed
bundle. The prepared candidate requires `qualification_gate.py` and
`release-qualified-hold.sh` to be **absent** in `/usr/local/lib/agc-worker/`.
Root must verify that on reconnect. If either already exists, preserve it and
regenerate with its exact observed `--old-gate-sha256` / `--old-release-sha256`;
never delete an existing helper to make the candidate pass. Changing those
expectations changes the bundle/Setup hash and requires review of that exact
candidate.

Prepared manifest SHA256:
`ba6e3903fd253f393ffe7c3875208fddd2d9a349679ec6d79e57057b734ea8ac`.

| Bundle file | SHA256 |
| --- | --- |
| `upgrade-linux.sh` | `2e1ecec975fca48cf36239bcb7a2867d02bd3c67aabd4ed6345309ae29054fac` |
| `qualify-native.sh` | `48eef90ec5d385885990674372e09c0941bd385ff5b7cd32bedc52e866e36117` |
| `qualify-toolchains.sh` | `020eca06746c0f6c15a3281e9fe662ed916e5cf0d55b113ad2d8d3d08023b3ec` |
| `Start-Worker.ps1` | `4214a5aa9738f2367c85d76a3b00c48d85e5018f6d123d27c4f491da5ebfd103` |
| `Verify-WorkerPolicy.ps1` | `0559dc332bc39cffa75908303ad125cbc096d2f86e45228186dba201c3929f75` |

`manifest.json` also records every installed old/new hash and the expected
existing `Setup-Worker.ps1`, `Invoke-WorkerSetup.ps1` and `Host-Disk.ps1` hashes.
Check those helper hashes before using them; this upgrade does not justify an
unreviewed replacement helper. The updater payload contains only reviewed code,
never configuration or credential material.

## Reconnect, hold and supervisor upgrade

1. Root checks current laptop reachability, permitted run day/AC and host
   headroom. Do not power on outside the existing schedule. Observe staff
   configuration and already-running staff/worker boot identities without
   starting a staff distribution or changing desktop settings.
2. Run root's existing reviewed `/tmp/agc-hold-private-job-fix.sh` through the
   established Setup route first. Confirm local hold, no active job, and central
   owner hold. Keep heavy setup/qualification serialized. A live/unknown attempt
   or populated job cgroup must be reconciled before this upgrade.
3. Verify old Linux file hashes/absence and protected owner/modes. Transfer the
   bounded bundle to an administrator-protected staging directory and compare
   all bytes with `manifest.json`; no environment/credential files are copied.
4. Invoke the existing reviewed Setup helper from its own protected directory
   (which also contains its matching Invoke/Host-Disk helpers):

```powershell
.\Setup-Worker.ps1 -ScriptFile C:\approved-stage\upgrade-linux.sh `
  -Sha256 2e1ecec975fca48cf36239bcb7a2867d02bd3c67aabd4ed6345309ae29054fac `
  -Label private-job-upgrade -TimeoutSeconds 180
```

The updater uses an exclusive operator lock and requires both held/drained RPC
status and empty `agc-job-*` cgroups. It records immutable old file bytes and a
durable intent under `/var/lib/agc-worker-upgrades/<plan-sha256>/`, including
fsynced backup-directory ancestry, before stopping only `agc-worker.service`.
It verifies the stopped supervisor's PID/cgroup state before replacement.
It atomically replaces exactly `worker.py`,
`qualification_gate.py` and `release-qualified-hold.sh`; state/jobs are narrowed
to0700. Configuration and credential metadata, adapter hashes, WSL configuration
and resource unit files are checked unchanged. Credentials are never read into
the bundle/backup/log. The updater starts the same supervisor and requires it
to answer held/drained with empty job cgroups. No full installer, daemon-reload,
unit edit, distribution termination or task/calendar edit is performed.

Save the Setup receipt and Linux upgrade receipt. A completed exact rerun verifies
the installed state and returns `already_installed_held` without another restart.
Setup's repeated touch of the hold file is permitted; changed configuration,
old/new hashes or private state modes are not. Incomplete journals refuse replay.

## Windows files and watchdog handover

Root owns this separately reviewed step after fresh live inspection. Back up
the exact current `Start-Worker.ps1` / `Verify-WorkerPolicy.ps1` bytes and their
owner/ACL metadata under administrator-only control storage. Parse the new
files using PowerShell's parser without executing them; validate their above
hashes. Replace only those two code files using protected same-directory staging
with the approved owner/ACL: Administrators/SYSTEM write, worker read/execute,
no reparse points or nonadministrator write/delete/ACL-change authority. Check
the final owner/ACL and bytes again. No account, task principal, action, trigger,
credential, WSL config, desktop or power setting changes belong to this step.

**The old watchdog's `finally` block stops the worker jobs slice/supervisor and
runs `wsl --terminate AGC-Worker`.** A graceful task/Stop-Worker shutdown is not
a watchdog-only restart. Do not use Provision Runtime/Disable or a generic
process-kill/keepalive helper. Root will inspect the exact live task/PID ownership
and choose a separately reviewed watchdog-only handover that preserves the
worker and staff boot IDs. If those conditions cannot be proven, retain holds
and report the gap. This package neither performs nor assumes that handover.

## Fresh qualification and release gates

After the updated watchdog is running, use the same Setup helper for the
worker-hash-pinned wrappers, one at a time:

```powershell
.\Setup-Worker.ps1 -ScriptFile C:\approved-stage\qualify-native.sh `
  -Sha256 48eef90ec5d385885990674372e09c0941bd385ff5b7cd32bedc52e866e36117 `
  -Label private-native -TimeoutSeconds 360
.\Setup-Worker.ps1 -ScriptFile C:\approved-stage\qualify-toolchains.sh `
  -Sha256 020eca06746c0f6c15a3281e9fe662ed916e5cf0d55b113ad2d8d3d08023b3ec `
  -Label private-toolchains -TimeoutSeconds 240
```

Wait for and inspect each result before launching the next command. Preserve
the exact Windows Setup receipt bytes and Linux native/toolchain receipt paths
and hashes. Native proof must contain all five current cases, production0077,
private job paths, denied host-state/metadata access, atomic readable lease
replacement, duplicate rejection and empty cgroups. Both target toolchains must
report the same current worker hash and private namespace proof.

Then follow `ops/agc-worker/README.md`'s explicit qualification-receipt procedure:
freshly observe staff config/boot/time, seal exact reviewed Setup receipts, verify
fresh `qualified_worker_sha256` telemetry, and invoke the installed Linux gate
with those exact Linux receipt hashes. The central owner hold remains independent;
production job/domain acceptance and root approval precede dispatch release.
No historical receipt names or boot IDs are supplied by this bundle.

## Failure and rollback

Any failed/partial update retains the operator hold. A second-preflight failure
records `failed_before_install` and leaves the original supervisor untouched.
After installation begins, cleanup records `failed_held` only after confirming
the supervisor has stopped; failed/uncertain cleanup records
`failed_stop_unconfirmed`, preserving the original failure and cleanup evidence.
Root must reconcile that uncertain process state before further work.
Preserve its backup, receipt, current file hashes and job receipts;
do not delete a journal to retry. Root reconciles the exact partial state and
prepares a new explicit before/after plan if repair is needed. Backups preserve
old metadata for inspection, but the former c4e launcher is not a working private
path rollback: do not run it or restore0711 traversal as a workaround. The safe
operational fallback is held/disabled laptop dispatch with the always-on services
unchanged. Windows code rollback also requires root's exact watchdog-only
handover review and retained holds, never a blind task/WSL restart.

Source verification: eight bounded tests cover exact file/hash/mode guards,
explicit absent helpers, deterministic packaging, preservation metadata without
reading credentials, hold-touch idempotence, generated Python syntax, durable
backup ordering and interrupted updates with confirmed/unconfirmed cleanup.
Bash syntax checks pass. Independent source review verified both backup
durability and stop-confirmation corrections. No laptop/CT action, PowerShell
execution or native workload qualification was performed for this candidate.
