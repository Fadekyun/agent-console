# Agent Profiles

Profiles live in `<workspace>/agent-profiles` and are injected into new sessions without replacing repository instructions.

- `general`: normal interactive work
- `coder`: bounded implementation, usually in a worktree
- `planner`: hard read-only planning
- `scout`: read-only repository tracing
- `reviewer`: read-only review
- `verifier`: tests acceptance criteria without production edits
- `bugfix`: reproduce, isolate, fix minimally, add regression coverage
- `researcher`: read-only external research
- `release`: explicit approval and approved scope only
- `operator`: infrastructure operation with rollback

Codex uses a read-only sandbox for read-only profiles and workspace-write for other profiles. Claude uses plan mode for read-only profiles. Other tools receive the profile instruction, but their technical enforcement limits must be treated honestly.
