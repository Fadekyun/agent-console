# Development Roadmap

This document tracks the planned and completed development work for Agent Console.

## Integration in progress

- [ ] **Explicit shared-skill allowlist** (2026-09-18, [#131](https://github.com/Fadekyun/agent-console/issues/131)) — `AGCONSOLE_SHARED_SKILLS` (empty by default) resolves standard skills independently of profile assignments; Codex/Codex Pro materialize them into the per-session isolated root on create and restart, Hermes merges them into `HERMES_HOME/config.yaml` `skills.external_dirs` on create and restart, and OpenCode registers the same isolated root as an additional `skills` config path (existing native sources stay active). `install.sh` writes and preserves the setting (empty default) across `update.sh` regeneration. Strict allowlist validation, no catalog-wide exposure, and the non-isolating-harness assignment guard preserved. Source tests pass; independent review and deployment pending.
- [ ] **Private directories follow a rename** (2026-09-18, [#129](https://github.com/Fadekyun/agent-console/issues/129)) — `Manager.rename()` moves `contexts/<stem>-<tool>` and `tool-overlays/<name>` for a stopped session and rewrites the launcher references; a running harness keeps its paths. Keeps name-keyed lookups (auto-namer activity signals, operator tooling) working after a rename. Source tests pass; independent review and deployment pending.
>>>>>>> ad326d4 (fix: move a session's private directories when it is renamed)

- [ ] **Rename without a live harness** (2026-09-18, [#125](https://github.com/Fadekyun/agent-console/issues/125)) — `Manager.rename()` skips the tmux call for a finished session (race-tolerant) and `--current` resolves through the exported `AGENT_CONSOLE_SESSION_ID` with a name fallback, in the writer and guarded read-only paths. Unblocks the lxc-115 auto-namer and future live-session renames. Source tests pass; independent review and deployment pending.

- [ ] **Native pi/Hermes session MCP config** (2026-09-17, [#123](https://github.com/Fadekyun/agent-console/issues/123), part of [#113](https://github.com/Fadekyun/agent-console/issues/113)) — The CommandCode adapter writes pi's credential in the `$CMD_API_KEY` reference form and generates the per-session pi `mcp.json` plus the Hermes `mcp_servers` block from environment-variable names, retiring the host-local Hermes wrapper merge and the pi normalize shim. Source tests pass; independent review and deployment pending.

- [x] **Native progress/final separation** (2026-09-14, #105): collect Codex commentary without premature JSON validation; validate final file/last message after complete stream, reject malformed or conflicting verdicts. Focused regressions prepared; live promotion pending.

- [ ] **Native exact-artifact review** (2026-09-14, #103): bounded AGC package/controller review via managed native runner, fixed codex-pro Astra low, durable binding and fail-closed verdict. Source tests prepared; independent review and deployment pending.

- [ ] **Selected-release CLI entrypoints** (2026-09-13, [#98](https://github.com/Fadekyun/agent-console/issues/98)) — Shared manifest/inode/containment validation and writer locking for eight aliases; explicit bootstrap, upgrade and guarded rollback; inert exact-hash authoritative sync patch. Source validation/review pending; no live application.

- [ ] **Single reviewed Console release candidate** (2026-09-13, [#96](https://github.com/Fadekyun/agent-console/issues/96)) — Combine the reviewed skill engine, disabled planning protocol and guarded inspection; enforce schema-compatible release/rollback, prepare existing-skill references/helpers and audit actual content delivery. Combined regression, assignment, native skill discovery and UI fixture validation passed; independent final review remains pending. No native planning enablement.

## Completed

- [x] **Live-WAL guarded inspection** (2026-09-15, [#109](https://github.com/Fadekyun/agent-console/issues/109)) — One writer connection is held for the web-service lifetime so the guarded read-only CLI routes keep working while the service runs; the reader still creates/changes nothing and still fails closed when no writer holds the database. Shipped in v0.7.1.

- [x] **Non-mutating CLI session inspection** (2026-09-13, [#93](https://github.com/Fadekyun/agent-console/issues/93)) — Nine session/profile read routes bypass writer initialization. Guarded schema10/11 snapshots and bounded tmux observations preserve stored lifecycle state, report observation failures honestly and keep integration content private. All integration operations remain writer-owned.

- [x] **Disabled-by-default safe planning request protocol** (2026-09-13, [#91](https://github.com/Fadekyun/agent-console/issues/91)) — Adds a strict authenticated local request/status contract, frozen prepared context, durable idempotency and launch receipts, a native noninteractive read-only task runner, shared admission serialization, view-only request sessions, and owner-only bounded results. Activation remains gated on prepared project mappings and real provider/containment verification.
- [x] **Provider-native skill capability harness** (2026-09-13, [#81](https://github.com/Fadekyun/agent-console/issues/81)) — A typed provider capability table now drives catalogue, safe canonical sync, and shared doctor diagnostics, including verified OpenCode 1.18.30 native discovery, bounded collision/shadow reporting, XDG-aware roots, and conservative version gates.
- [x] **Codex Plan network access restored** (2026-09-12, [#83](https://github.com/Fadekyun/agent-console/issues/83)) — `AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS` again switches Codex Plan sessions to `workspace-write` with sandbox network access while keeping the read-only approval policy; unset keeps strict read-only.
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

- [x] #107 — Pi harness and Hermes CommandCode contexts, exact authenticated DeepSeek V4.1 Flash defaults, native profile/session prompt delivery, runtime secret references, and explicit unsupported enforcement reporting (v0.7.0).
