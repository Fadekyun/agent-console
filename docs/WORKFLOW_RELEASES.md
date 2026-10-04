# Exact-candidate release actions

Results & handoffs → Release actions connects a selected commit to check evidence, an explicit operator authorization, and an observed external outcome. A small task can use its own passing final result and actual checks. A separate reviewer is optional unless the repository/task requires one. A separate evidence result must have consumed this exact candidate through the durable inbox **before** publication. Event sequence order, not wall-clock timestamp equality, proves that order.

Select a final passing result containing exactly one commit snapshot. Select current passing check results, the configured target and one action (`push`, `merge`, `deploy`, or `release`). Preview binds the full commit SHA, immutable artifact hash, evidence IDs/content hashes, action, target and adapter fingerprint. Authorize and run creates a durable grant and one attempt. Authorization alone does not execute anything. Candidate/evidence supersession, failed/stale graph prerequisites, changed adapters and paused/stopped workflows block new execution. A grant cannot substitute the current branch HEAD for its selected commit.

These are explicit operator actions, separate from suggestions/automatic agent growth. An agent reporting capability cannot authorize or run releases. Native next-step tasks retain read/write/test action classes; a role name or an automatic envelope does not authorize a release. The older plan/deployer evidence gate is unchanged and still governs its own canary/user-service actions.

## Configure trusted target adapters

There is no default production target. An operator installs reviewed executable adapters and writes `release-targets.json` in Console's configured config directory. This file and the adapter executable must be regular files, owned by the service user or root, with no group/world write permission. Commands are argument arrays with an absolute executable; the UI cannot submit a shell command. No adapter is installed or executed by importing a skill.

```json
{
  "version": 1,
  "targets": [{
    "id": "project-staging",
    "label": "Project staging",
    "actions": ["deploy"],
    "apply": ["/opt/operator-adapters/project-staging", "apply"],
    "probe": ["/opt/operator-adapters/project-staging", "probe"],
    "timeout_seconds": 120,
    "environment": ["PROJECT_STAGING_TOKEN"]
  }]
}
```

The environment list contains host-local variable **names**, not values. Only those variables and basic PATH/HOME/locale/temp variables reach the worker. Console capability variables and Python/loader injection variables cannot be selected. Missing configured values fail closed. The public target catalog omits commands and environment references. The fingerprint binds this target definition and both executable files. It cannot fingerprint every library/interpreter/remote dependency; operators remain responsible for those trusted programs. Adapters run as the Console service user, not in an additional OS security sandbox.

An adapter receives one extra argument: an owner-only request JSON path. Its fields are `version`, `mode` (`apply` or `probe`), `operation_id`, `attempt_id`, `action`, `target`, `candidate_sha`, `artifact_sha256`, `snapshot_path`, and `response_path`. The snapshot is a private copy of the selected commit archive, verified before invocation. Use the operation ID as the external idempotency key where the target supports it. Do not derive the candidate from a mutable branch. Reject unsupported actions/targets, preserve target-specific preconditions, and keep child processes synchronous in the worker's process group.

The apply command performs only the configured action. Its exit code does not prove success or failure: after it returns, Console invokes the separate **read-only** probe. The probe writes the following bounded JSON to `response_path` and exits zero:

```json
{
  "outcome": "applied",
  "candidate_sha": "FULL_EXACT_COMMIT_SHA",
  "summary": "Observed the expected revision on the authorized target",
  "external_reference": "Non-secret deployment or operation reference"
}
```

Allowed outcomes are `applied`, `not-applied`, and `unknown`. `applied` requires authoritative observation of the requested action and candidate. `not-applied` must prove the operation did not take effect and is safe to retry under the same authorization; merely not seeing a response or seeing another current revision is insufficient. Use `unknown` for eventual consistency, conflicting external changes, partial effects, or inconclusive observations. An adapter must not return credentials or raw command output. Console discards stdout/stderr and exposes only schema/secret-checked observations. This is a trusted adapter contract; Console cannot turn an incorrect external probe into reliable evidence.

## Recovery and workflow controls

Stable request keys deduplicate authorization and attempt submission. A dedicated worker is pinned to its source release, registers its process identity before any adapter runs, and preserves a separate process group across a web restart. Keep its source release installed until it exits. Only one operation can own a target at a time, across actions/grants. Unknown outcomes hold the target until reconciled.

An unknown, interrupted, unacknowledged or missing worker cannot be automatically retried. Check external outcome runs only the read-only probe. If it observes the exact operation applied, the grant is complete; if it proves not-applied, Retry authorized release is a separate explicit action and repeats the freshness/authorization checks. Live orphan adapter processes block probing/retry until they terminate; zombie-only groups do not. Adapter timeouts interrupt the owned process group and leave an unknown outcome.

Pause prevents new release execution while active work continues. Stop requests termination of positively identified release workers for that connected work and preserves an unknown outcome for reconciliation. A stopped workflow cannot execute again, but its external outcome can still be checked. If a worker is gone and process ownership cannot be proved, Console preserves the uncertainty instead of killing an unrelated process. Work overview marks unknown releases as needing attention separately from candidate result success.

## Persistence, API, verification and rollback

Additive companion schema 1 uses `release_schema`, `release_grants` and `release_attempts`. Worker requests/snapshots are under `state/release-attempts/`. Grants, attempts and observations remain after session Stop. Back up the connected-work WAL database with SQLite backup, result objects, release-attempt directories, target config and reviewed adapter programs together. Preserve host-local credentials separately.

Operator-authenticated routes:

- `GET /api/workflow/release-targets`
- `GET /api/workflow/releases/evidence/{candidate_result_id}`
- `POST /api/workflow/releases/preview`
- `POST /api/workflow/releases/authorize` with the preview hash and stable request key
- `GET /api/sessions/{identity}/releases`
- `GET /api/workflow/releases/{grant_id}`
- `POST /api/workflow/releases/{grant_id}/attempts` with mode and stable request key

Unit/integration coverage uses real local adapter processes and Git commit snapshots: matching checks, causal consumption, changed candidate/adapter, failed prerequisites, auth, deduplication, lost apply acknowledgment, unknown/not-applied reconciliation, target serialization, worker loss/orphans, timeout and workflow pause/stop. Browser coverage exercises explicit preview/authorization and unknown-outcome controls at desktop and emulated phone widths. These tests do not certify an arbitrary operator-provided deployment adapter.

Before rollback, pause dispatch and settle or explicitly stop/reconcile active operations. v0.16.3 ignores release tables; it does not stop workers or provide their reconciliation UI. Restore v0.17 to recover the controls. Switching to the separate current console does not move or cancel staging work. Current-console cutover remains a separate authorization.
