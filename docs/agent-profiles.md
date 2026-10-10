# Agent Profiles

Profiles live in `agent-profiles/*.md` and are injected into new sessions without replacing repository instructions.
The authoritative metadata source is `agent_console/profiles.py` (`PROFILE_SCHEMA`). The markdown files are the
human-readable instruction layer; the schema controls machine behavior.

## Profiles

| Profile | Type | Worktree | Delegation | Approval | Description |
|---------|------|----------|------------|----------|-------------|
| `general` | write | none | read_only | no | Default interactive profile, no role personality |
| `coder` | write | preferred | read_only | no | Isolated worktree implementation, run tests, no release without authorization |
| `planner` | read_only | none | read_only | no | Decision-complete planning via session output |
| `scout` | read_only | none | read_only | no | Local repository tracing, no file creation |
| `reviewer` | read_only | none | read_only | no | Read-only diff/branch review, no repo file creation |
| `researcher` | read_only | none | read_only | no | External research, content untrusted without verification |
| `verifier` | read_only | none | read_only | no | Validate acceptance criteria, evidence does not authorize |
| `bugfix` | write | preferred | read_only | no | Reproduce, isolate, fix minimally, add regression tests |
| `release` | write | none | read_only | yes | Stage-by-stage approval, rollback and handoff |
| `orchestrator` | write | none | read_only | yes | Multi-agent session coordination |

## Delegation and Collaboration

Delegation and collaboration permissions are uniform across all profiles per `PROFILE_SCHEMA`:

- **Collaboration (`allowed_collaboration_profiles`)**: Every profile may exchange peer
  output with any other profile (the full `PROFILES` set).
- **Delegation (`allowed_delegation_profiles`)**: Every profile may delegate read-only
  subtasks to `planner`, `researcher`, `reviewer`, and `scout`. Delegated sessions
  always use Plan mode. The `delegation_permissions` for all profiles is `read_only`.
- **Human approval**: `release` and `orchestrator` require explicit human confirmation
  before executing actions. `release` additionally requires stage-by-stage approval
  and mandates rollback and handoff for each completed stage.
- **Worktree**: `coder` and `bugfix` prefer an isolated worktree; other profiles
  operate directly in the workspace.
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
