# Session capabilities and selected skill delivery

Every managed interactive session receives its existing session ID, reporting URL,
and reporting capability. `agentctl` uses `POST /api/agent-sessions` for session
inspection, attention, delegation, interrupt, restart, kill, and child waits.
The caller does not need database write permission. The service authenticates the
capability against the stored session ID, including after a rename. Browser
operator endpoints retain their existing authentication.

All roles can read bounded terminal output, metadata, and saved context for all
recorded Console sessions, including other projects and unrelated trees. Explicit
names and stable IDs select any recorded session; relative selectors and
`tree --current` remain scoped to the caller's tree. Private integration content
remains redacted, and unrecorded tmux sessions are excluded. Unknown
socket status is reported as `running: null`, `live_state: unknown`; it never
counts as successful completion. Saved transcripts and metadata remain readable.
The Linux guarded offline reader remains available outside managed sessions.
Service-backed file reads use descriptor containment checks on Linux and macOS.

Writable, non-Plan roles can update attention, interrupt, restart, or stop their
ancestors and descendants within the current task authorization. Mutations of
siblings and unrelated sessions remain denied; a role cannot interrupt, restart,
or stop itself. Targets and callers must be managed interactive sessions.
Writable roles can delegate any compatible role within the current authorization.
Read-only or Plan parents can delegate read-only roles,
including verifier, but cannot upgrade to writable modes. Read-only callers can
signal their own attention state. Children inherit the project and repository;
coding delegates and writable delegates into Git repositories get isolated worktrees.
Non-coding general/shell work in a non-Git workspace remains supported.
Human Add-session links retain their existing operator authority; agent capability
calls cannot use them to escalate. Creation checks depth (eight),
configured positive per-parent child limits (default `0`, unlimited), and global capacity under the existing admission lock. Only active/reserved children count toward child capacity; stopped and archived history does not.
A child that disappears without `ready_for_review` is a failure; `blocked` or
`needs_input` requires intervention. Ordinary single sessions need no extra stages.

Child waits observe metadata and are available to read-only/Plan callers for any
recorded managed interactive parent. Both scoped and unscoped waits pin the
parent's durable ID on the first response, so renames and reused names cannot
change the target. Every `children` response includes `parent_id`; a new client
rejects an older server that cannot confirm it. No database migration is needed.
Known authorization failures have fixed CLI explanations; unknown response bodies
are never echoed. A failed request never falls back to local state access.

CLI examples:

```sh
agentctl session relatives --current
agentctl session review --relative child --index 1
agentctl delegate verifier --parent "$AGENT_CONSOLE_SESSION_ID" --task 'Verify the acceptance criteria'
agentctl session attention --current --state ready_for_review
```

The capability does not grant push, merge, deployment, release, or environment
management authority. Harness sandbox guarantees still differ; Pi, Hermes, and
shell do not gain filesystem enforcement from these Console API checks.

## Skills and customized roles

Claude's additional-directory skills, OpenCode's configured skill paths, Hermes's
`skills.external_dirs`, and Codex/Pi session homes all point at the immutable
selected snapshot. Assigning a compatible skill no longer blocks Claude,
OpenCode, or Hermes merely because they also discover native sources. User,
project, plugin, and administrator skills may remain active; Console does not
claim to suppress those sources. Exact-version compatibility checks remain in
place for OpenCode and Pi.

Skill preview includes affected running sessions, restart requirements, delivery
capabilities, source policy, and version diagnostics. Running snapshots change
only on explicit restart. Profile listings expose `bundled_update_available` and
a unified `bundled_diff`; installed operator edits are never overwritten by these
changes.

Repeat native discovery checks without credentials or model calls:

```sh
python scripts/probe-skill-delivery.py --opencode /path/to/real/opencode --hermes-root /path/to/hermes-agent
```

The probe uses disposable homes. It verifies OpenCode's native skill listing and
Hermes's native external-directory resolver. Claude launch/configuration is
covered by fixture tests; an authenticated Claude native discovery smoke remains
an operator verification when the CLI is available. These probes do not certify
model authentication or all third-party skills.

## Verification and rollback

Run `test_session_control.py`, `test_profile_schema.py`, `test_cli_inspection.py`,
`test_skill_capabilities.py`, `test_shared_skill_discovery.py`, `test_core.py`, and
`test_workflow_engine.py` with the project Python environment. Socket-based tests
need a host that permits temporary tmux servers and local TestClient sockets.

No database migration is required. Roll back code to the previous release to
remove the API and restore older CLI behavior; existing session records, selected
snapshots, and operator role files remain intact. New launchers need a reachable
reporting service; a failed capability request never falls back to a database
writer.
