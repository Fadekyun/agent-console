# Release Notes

All entries from 0.1.1 onward include: date, issue/PR, impact, configuration/migration, verification, and rollback guidance. Entries for historic unpublished baselines may omit fields that are not applicable.

## 0.1.6 (Unreleased)

- **Date**: 2026-08-19
- **Issue/PR**: [#74](https://github.com/Fadekyun/agent-console/issues/74) / [#75](https://github.com/Fadekyun/agent-console/pull/75)
- **Impact**: Adds a mandatory repository-local startup protocol for future development agents. `AGENTS.md` now requires reading `docs/START_HERE.md` and verifying the current epic, issue, existing PR/worktree, dependency state, scope, non-goals, and deployment state before implementation. The start-here document records the #72 inference-phase implementation order, identifies #73 as the current future staging gate and #32 as historical, and defines a durable end-of-session handoff format.
- **Configuration/Migration**: None. Documentation/governance only.
- **Verification**: Confirm `AGENTS.md` links to `docs/START_HERE.md`; confirm the startup protocol references #72/#73 and current implementation sequence; run version-consistency tests before merge.
- **Rollback**: Revert the documentation/governance commit and version bump. No data or runtime configuration changes persist.

Changes:
- Added `docs/START_HERE.md` as the mandatory first-read development entry point.
- Added mandatory startup instructions to `AGENTS.md`.
- Added live tracker/PR/worktree/dependency verification before coding.
- Added end-of-session handoff requirements so future agents do not depend on chat history.
- Version 0.1.5 → 0.1.6.

## 0.1.5 (Unreleased)

- **Date**: 2026-07-29
- **Issue/PR**: [#44](https://github.com/Fadekyun/agent-console/issues/44) / [#53](https://github.com/Fadekyun/agent-console/pull/53)
- **Impact**: Terminal keyboard focus is reliably restored across dedicated-terminal page load, silent brief loading (no composer focus theft), docked terminal open/tab-switch, composer submission, and background-output scenarios. Scroll and Select modes remain intentionally non-typing. Release blockers resolved: the web UI brand now matches the package version (v0.1.5), explicitly disabled Claude keeps its disabled status when the launcher binary is absent, the skill-sync test is hermetic to ambient `AGCONSOLE_RETAINED_SKILLS`, and `install.sh` tolerates a deliberately disabled/non-executable Claude launcher without weakening required-provider or relative-path validation.
- **Configuration/Migration**: None.
- **Verification**: `node --input-type=module --check < web/static/terminal.js` (pass), `node --input-type=module --check < web/static/app.js` (pass), `python3 -m pytest tests/test_version.py` (3/3 pass), `python3 -m pytest tests/test_web.py` (52/52 pass), `python3 -m pytest tests/test_skills_secrets.py` (4/4 pass), `python3 -m pytest tests/test_installer.py` (47/47 pass), `npx playwright test tests/ui/console.spec.mjs --project=desktop --project=samsung --project=iphone` (78/78 pass, 30 skipped)
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- `insertComposer()` accepts optional `focus` parameter to avoid stealing terminal focus during silent brief loading.
- Silent (`loadBrief(true)`) no longer focuses the composer.
- Docked terminal iframes focus xterm on `load` and on `activateTerminal` tab switch.
- Terminal focus is restored after composer submission in Type mode (unchanged behavior preserved).
- `SessionManager.tool_catalog()` preserves an explicitly disabled Claude status instead of overwriting it with a launcher-missing error.
- The skill-sync test now uses a fixed hermetic fixture list (`tailscale-router`, `agent-console-ops`) instead of import-time ambient `AGCONSOLE_RETAINED_SKILLS`.
- `install.sh` tolerates a deliberately disabled/non-executable Claude launcher while remaining fatal for required providers and invalid relative paths.
- UI brand version pinned to v0.1.5; a new test enforces the web UI brand matches `agent_console.__version__`.
- Version 0.1.4 → 0.1.5.

## 0.1.4 (Unreleased)

- **Date**: 2026-07-29
- **Issue/PR**: [#46](https://github.com/Fadekyun/agent-console/issues/46) / [#47](https://github.com/Fadekyun/agent-console/pull/47)
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
