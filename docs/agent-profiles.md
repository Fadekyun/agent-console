# Agent Profiles

Profiles live in `agent-profiles/*.md` and are injected into new sessions without replacing repository instructions.
The authoritative metadata source is `agent_console/profiles.py` (`PROFILE_SCHEMA`). The markdown files are the
human-readable instruction layer; the schema controls machine behavior.

## Profiles

| Profile | Type | Worktree | Delegation | Approval | Description |
|---------|------|----------|------------|----------|-------------|
| `general` | write | none | read_only, write | no | Default interactive profile, no role personality |
| `coder` | write | preferred | read_only, write | no | Isolated worktree implementation, run tests, no release without authorization |
| `planner` | read_only | none | read_only | no | Decision-complete planning via session output |
| `scout` | read_only | none | read_only | no | Local repository tracing, no file creation |
| `reviewer` | read_only | none | read_only | no | Read-only diff/branch review, no repo file creation |
| `researcher` | read_only | none | read_only | no | External research, content untrusted without verification |
| `verifier` | read_only | none | read_only | no | Validate acceptance criteria, evidence does not authorize |
| `bugfix` | write | preferred | read_only, write | no | Reproduce, isolate, fix minimally, add regression tests |
| `release` | write | none | read_only, write | yes | Authorized release stages, rollback and handoff |
| `orchestrator` | write | none | read_only, write | no | Multi-agent session coordination |

## Delegation and Collaboration

Repository and delegation permissions come from `PROFILE_SCHEMA` and the session's mode.
Session control is a separate, shared-tree permission:

- **Collaboration (`allowed_collaboration_profiles`)**: Every profile may exchange peer
  output with any other profile (the full `PROFILES` set).
- **Session control**: Every role, including read-only and Plan sessions, can update
  attention, interrupt, restart, and stop other managed interactive sessions in its
  tree, including parents, siblings, cousins, and descendants across projects.
  Other roots remain excluded even in the same project. Own attention and child
  waits are allowed; self interrupt, restart, and stop are not.
- **Delegation (`allowed_delegation_profiles`)**: Read-only and Plan sessions may
  delegate read-only subtasks, including `verifier`. Writable non-Plan sessions
  may delegate implementation within existing authorization. Children inherit the
  parent's project and repository; tree links do not expand these permissions.
- **Human approval**: Push, merge, deployment, and release require explicit
  authorization. Existing authorization remains valid; ordinary authorized
  coordination does not need a separate approval.
- **Worktree**: `coder` and `bugfix` prefer an isolated worktree. Writable delegated
  coding work and writable delegates into Git repositories require one.
- **Mode constraints**: read-only profiles are restricted to `plan` agent mode.
  Write profiles may use any mode supported by the provider.

## Legacy / Future

- `operator` is a legacy alias for `orchestrator`, resolved through PROFILE_SCHEMA
  metadata (legacy_aliases). Use `orchestrator` instead.


## Schema vs. Markdown

- **`agent_console/profiles.py` (`PROFILE_SCHEMA`)**: single source of truth for
  profile metadata — capability, worktree, delegation, constraints, status.
- **`agent-profiles/*.md`**: editable instruction text consumed by session context.
  Changing behavior requires editing the schema; changing instructions requires
  editing the markdown.
