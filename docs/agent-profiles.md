# Agent Profiles

Profiles live in `agent-profiles/*.md` and are injected into new sessions without replacing repository instructions.
The authoritative metadata source is `agent_console/profiles.py` (`PROFILE_SCHEMA`). The markdown files are the
human-readable instruction layer; the schema controls machine behavior.

## Profiles

| Profile | Type | Worktree | Delegation | Approval | Description |
|---------|------|----------|------------|----------|-------------|
| `general` | write | none | — | no | Default interactive profile, no role personality |
| `coder` | write | preferred | — | no | Bounded implementation, isolated worktree |
| `planner` | read_only | none | read_only | no | Hard read-only planning |
| `scout` | read_only | none | read_only | no | Read-only repository tracing |
| `reviewer` | read_only | none | read_only | no | Read-only diff/branch review |
| `researcher` | read_only | none | read_only | no | Read-only external research |
| `verifier` | read_only | none | read_only | no | Test acceptance criteria without production edits |
| `bugfix` | write | preferred | — | no | Reproduce, isolate, fix minimally, add regression tests |
| `release` | write | none | — | yes | Explicit approval and approved scope only |
| `operator` | write | none | — | yes | Infrastructure operation with rollback |

## Relationship Matrix

```
                     ┌─ can delegate to ──┐
                     │                    ▼
 general, coder, bugfix ──► planner, researcher, reviewer, scout
 release, operator    ──► planner, researcher, reviewer, scout
                     │                    │
                     │         read-only delegation
                     │         plan mode only
                     │                    │
                     └────────────────────┘
                              ▲
                     any profile can
                     collaborate with
                     any other profile
```

- **Collaboration**: all profiles can exchange peer output with any other profile.
- **Delegation**: write-capable profiles may delegate read-only subtasks to planner,
  researcher, reviewer, and scout. Delegated sessions always use Plan mode.
- **Human approval**: `release` and `operator` require explicit human confirmation
  before executing actions. `release` additionally forbids implementation changes
  and mandates approval for push/merge/deploy.
- **Worktree**: `coder` and `bugfix` prefer an isolated worktree; other profiles
  operate directly in the workspace.
- **Mode constraints**: read-only profiles are restricted to `plan` agent mode.
  Write profiles may use any mode supported by the provider.

## Legacy / Future

- `operator` is active. A future rename to `orchestrator` is pending human
  approval; historical `operator` sessions would remain inspectable via
  `legacy_aliases` metadata.
- `verifier` is technically read-only. An explicit-approval write escape hatch
  is pending human approval.

## Schema vs. Markdown

- **`agent_console/profiles.py` (`PROFILE_SCHEMA`)**: single source of truth for
  profile metadata — capability, worktree, delegation, constraints, status.
- **`agent-profiles/*.md`**: editable instruction text consumed by session context.
  Changing behavior requires editing the schema; changing instructions requires
  editing the markdown.
