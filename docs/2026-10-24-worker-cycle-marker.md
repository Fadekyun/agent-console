# Worker resident cycle-protocol declaration (#216)

Date: 2026-10-24
Issue: Fadekyun/agent-console #216
Base: `feat/203-laptop-migration` at `afa570d9` (resident executor #213/#215)

## Failure

The real Windows installation rejected the worker resident executor before
activation:

```
worker resident executor not enabled: helper does not declare # AGC_OWNER_CYCLE_PROTOCOL=1
```

The display installer's `Test-AgcOwnerCycleCapability` accepts a resident
handler only when its installed entrypoint declares
`# AGC_OWNER_CYCLE_PROTOCOL=1`. The worker entrypoint invoked the same cycle
protocol but declared only `# AGC_OWNER_RESIDENT_EXECUTOR=1`, so
`owners.worker.executorTask` stayed empty. The same install later failed on a
separate Defender detection; the previous installation was restored and no
restart was attempted.

## Correction

`ops/agc-worker/worker-executor.ps1` now declares both:

```
# AGC_OWNER_RESIDENT_EXECUTOR=1
# AGC_OWNER_CYCLE_PROTOCOL=1
```

This is a truthful declaration only. Runtime behavior, protocol, deadlines,
fixed task identity, credentials, privileges, ACLs and the central hold are
unchanged.

## Regression

`ops/agc-worker/tests/WorkerExecutorCapability.Tests.ps1` executes the
**verbatim extracted** display functions `Get-AgcTaskActionTarget` and
`Test-AgcOwnerCycleCapability`
(`tests/fixtures/DisplayOwnerCycleCapability.ps1`, source display installer
`fc3cc85`) against mocked task metadata and the real worker entrypoint:

- the real action parser derives the installed entrypoint path;
- the pre-fix entrypoint (resident marker only) is rejected with the missing
  `AGC_OWNER_CYCLE_PROTOCOL=1` detail;
- the corrected entrypoint is accepted and returns its installed path.

When `AGC_DISPLAY_LIFECYCLE_INSTALLER` points at a display installer, the test
also verifies the extracted capability function matches it, failing on drift.
The fixture is Windows PowerShell 5.1/7 compatible and performs no real task,
ACL or WSL operation.

## Rollback

Restore the prior worker entrypoint; no task/config/credential state is changed
by this declaration fix. Root owns Windows installation and the separate
Defender resolution.
