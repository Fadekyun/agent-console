# Development Roadmap

This document tracks the planned and completed development work for Agent Console.

## Completed

- [x] **Native session model and effort selection** (2026-09-11, [#78](https://github.com/Fadekyun/agent-console/issues/78)) — Create/delegate accept Codex model, reasoning effort and Plan effort; launchers preserve overrides across restart. Desktop creation exposes optional controls. The orchestrator skill uses native flags when available.

### 0.1.x (Current)

- [x] **Selectable Codex Pro provider** (2026-08-26, [#76](https://github.com/Fadekyun/agent-console/issues/76)) — Adds `codex-pro` as a separate Codex-compatible provider with an independently stored authentication context and identical plan/auto execution controls.
- [x] **Provider dropdowns aligned between desktop and mobile** (2026-07-28, [#37](https://github.com/Fadekyun/agent-console/issues/37)) — Identical labels, order, defaults, strict backend validation, race-condition-safe context filtering.
- [x] **Per-PR release governance** (2026-07-28) — AGENTS.md, roadmap, release notes, version bump policy.
- [x] **Profile instruction alignment** (2026-07-29, [#46](https://github.com/Fadekyun/agent-console/issues/46)) — Orchestrator is session coordinator (not infrastructure), operator is a legacy alias, all profiles have correct lifecycle rules and boundaries. PROFILE_SCHEMA descriptions match markdown profiles.
- [x] **Keyboard focus loss in dedicated and docked xterm terminals** (2026-07-29, [#44](https://github.com/Fadekyun/agent-console/issues/44)) — Silent brief loading no longer steals terminal focus; docked terminal xterm receives focus on iframe load and tab switch; composer focus is opt-in via explicit parameter.
- [x] **Project repo path may reference non-existent directory** (2026-07-28, #39) — `create_project`/`update_project` accept repo paths that do not exist yet on disk. Session/worktree/plan creation still requires an existing path.
- [x] Multi-tool orchestration (Codex, Claude, OpenCode, Hermes, Shell)
- [x] Web terminal with xterm.js PTY
- [x] Session delegation (parent/child trees, read-only peer review)
- [x] Auth contexts (per-tool credential isolation via filesystem; sessions remain mutually trusted under one Unix user)
- [x] Plan management (Discord-integrated planning with execution handoff)
- [x] Audit logging (SQLite-backed)
- [x] SSH client installer
- [x] Canary/staging deployment mode

## Upcoming

### 0.2.x

- [ ] CI pipeline (lint, typecheck, test on PR)
- [ ] Automated release workflow
- [ ] Concurrency-safe, versioned migration handling
- [ ] Session search and filtering

### Future

- [ ] Multi-user support
- [ ] Webhook integrations
- [ ] Plugin system
