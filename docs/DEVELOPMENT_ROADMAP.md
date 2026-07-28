# Development Roadmap

This document tracks the planned and completed development work for Agent Console.

## Completed

### 0.1.x (Current)

- [x] **Per-PR release governance** (2026-07-28) — AGENTS.md, roadmap, release notes, version bump policy.
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
