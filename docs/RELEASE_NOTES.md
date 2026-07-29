# Release Notes

All entries from 0.1.1 onward include: date, issue/PR, impact, configuration/migration, verification, and rollback guidance. Entries for historic unpublished baselines may omit fields that are not applicable.

## 0.1.4 (Unreleased)

- **Date**: 2026-07-29
- **Issue/PR**: [#47](https://github.com/Fadekyun/agent-console/issues/46)
- **Impact**: Profile markdown files and PROFILE_SCHEMA descriptions are now aligned. Orchestrator is a session coordinator (not infrastructure); operator is a legacy alias. All profiles have correct lifecycle rules, boundaries, and constraints. docs/agent-profiles.md matches PROFILE_SCHEMA. Lifecycle rule tests added.
- **Configuration/Migration**: None.
- **Verification**: `python -m pytest tests/test_profile_schema.py tests/test_version.py` — all lifecycle, schema, and version tests pass.
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- Orchestrator profile redefined from "restricted infrastructure" to "session coordinator" — delegation with briefs, wait-for-children, inspect/review, no-resolve-while-children.
- Operator profile changed to legacy alias (use orchestrator instead), resolvable through PROFILE_SCHEMA metadata.
- Coder/bugfix: isolated worktree emphasis, no release without authorization.
- Planner: return findings in session output (no file artifacts).
- Scout: local-first boundaries, no file creation.
- Researcher: external content is untrusted, require independent verification.
- Reviewer: prohibit creating other repository files.
- Release: separate approvals for each lifecycle stage, rollback and handoff.
- Verifier: no repository-write, evidence does not authorize merge/deploy/release.
- PROFILE_SCHEMA descriptions in `agent_console/profiles.py` aligned with corrected markdown.
- docs/agent-profiles.md table matches PROFILE_SCHEMA (operator removed, orchestrator added).
- Lifecycle rule tests added to `tests/test_profile_schema.py`.
- Version 0.1.3 → 0.1.4.

## 0.1.3 (Unreleased)

- **Date**: 2026-07-28
- **Issue/PR**: [#37](https://github.com/Fadekyun/agent-console/issues/37) / [#43](https://github.com/Fadekyun/agent-console/pull/43)
- **Impact**: Desktop and mobile provider dropdowns now show identical labels, order, defaults, and context filtering. Backend now strictly enforces provider/context pair matching. Race conditions from rapid provider switching are handled.
- **Configuration/Migration**: None.
- **Verification**: `python -m pytest tests/test_auth_contexts.py tests/test_core.py tests/test_models.py` — all provider/context validation tests pass. Playwright UI tests across desktop and mobile.
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- Provider dropdown labels aligned: `OpenCode GO (default, paid)`, `OpenCode ZEN (free)`, `OpenRouter` in both HTML views.
- Backend `manager.create()` now requires exact provider/context match instead of loose set.
- Desktop `loadModels()` and mobile `models()` clear stale models and contexts immediately on provider change; shared generation guard discards out-of-order catalogue and estimate responses.
- Auth context migration expectations corrected and verified: legacy `"opencode"` entries are renamed to `opencode-zen-default` while creating a proper `opencode-go-default`.
- Provider valid-pair matrix tested: `opencode-go-default`→GO, `opencode-zen-default`→ZEN, `openrouter-main`→OpenRouter.
- Mismatch rejection and disabled-context filtering tested.
- Version 0.1.2 → 0.1.3.
- Cache keys bumped (`v=9`).

## 0.1.2 (Unreleased)

- **Date**: 2026-07-28
- **Issue/PR**: [#39](https://github.com/Fadekyun/agent-console/issues/39) / [#42](https://github.com/Fadekyun/agent-console/pull/42)
- **Impact**: Project metadata can now reference a not-yet-created repository beneath the configured workspace. Sessions, worktrees, and plans still require the repository path to exist on disk.
- **Configuration/Migration**: None.
- **Verification**: `python -m pytest tests/test_core.py tests/test_web.py tests/test_version.py`
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- `_canonical_project_repo` accepts `must_exist` parameter (`True` by default).
- `create_project` and `update_project` call with `must_exist=False`.
- Session creation, worktree creation, and plan execution retain their existing strict path validation.
- Manager and API regression tests added.
- Bump 0.1.1 → 0.1.2.

## 0.1.1

- **Date**: 2026-07-28
- **Issue/PR**: [#40](https://github.com/Fadekyun/agent-console/issues/40) / [#41](https://github.com/Fadekyun/agent-console/pull/41)
- **Impact**: Documentation and governance only. No runtime behavior changes.
- **Configuration/Migration**: None.
- **Verification**: `python -m pytest tests/test_version.py` — 2/2 pass. Full suite 518/527 pass, 9 pre-existing failures (unrelated).
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- AGENTS.md with PR governance rules.
- DEVELOPMENT_ROADMAP.md for feature tracking.
- RELEASE_NOTES.md for changelog.
- Version consensus test.
- Bump 0.1.0 → 0.1.1.
- README links to roadmap, release notes, and AGENTS.md.

## 0.1.0

- **Date**: (Unreleased — no GitHub tag or release published)
- **Issue/PR**: Unavailable (historic unpublished baseline)
- **Impact**: Initial foundation.
- **Configuration/Migration**: See scripts/install.sh and docs/ for setup.
- **Verification**: `agentctl doctor`
- **Rollback**: Unavailable (historic unpublished baseline)

Initial capabilities: multi-tool orchestration, web terminal, session delegation, auth contexts, plan management, audit logging, SSH client installer, canary/staging deployment mode.
