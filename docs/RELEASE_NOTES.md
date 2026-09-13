# Release Notes

All entries from 0.1.1 onward include: date, issue/PR, impact, configuration/migration, verification, and rollback guidance. Entries for historic unpublished baselines may omit fields that are not applicable.

## 0.4.0 (Unreleased)

- **Date**: 2026-09-13
- **Issue/PR**: [#96](https://github.com/Fadekyun/agent-console/issues/96) / pending
- **Impact**: Consolidates capability-driven OpenCode skill delivery, nine guarded CLI inspection routes and the disabled preparatory planning-request protocol. Preserves skill containment, approval and assigned-profile isolation gates; native discovery is not per-session isolation. Detailed skill-content and delivery audit accompanies this release.
- **Configuration/Migration**: Additive schema10→11 retains existing sessions as interactive and stores request receipts separately. Native planning remains disabled/unverified; do not enable it based on skill discovery or SQL guard tests. Reader supports fixed schema10/11 projections and fails closed on unavailable enforcement/WAL prerequisites. OpenCode mutation is verified only for1.18.30; collision precedence remains unverified. Release/schema compatibility guards and supported rollback procedure are required before deployment.
- **Verification**: Exact integrated candidate regression and independent final review pending. Historical component evidence does not substitute for testing this combined source. Use isolated HOME/XDG/state/socket and the exact real OpenCode binary, never its HOME-dependent wrapper; record native discovery separately from planning containment.
- **Rollback**: Preserve sessions, database, request receipts, tombstones and artifacts. Never start a schema10 writer against schema11 or restore a stale backup over new writes. Follow the schema-compatible rollback and recovery procedure; source publication is not deployment acceptance.

## 0.3.1 (Unreleased)

- **Date**: 2026-09-12
- **Issue/PR**: [#83](https://github.com/Fadekyun/agent-console/issues/83) / pending
- **Impact**: Restores the documented `AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS` behavior for Codex/Codex Pro Plan sessions. When the variable is enabled, Plan mode keeps its read-only approval policy but runs with `--sandbox workspace-write` plus `sandbox_workspace_write.network_access=true`, so planning sessions can inspect other hosts over the network (for example SSH to N100/LXC nodes). Permission mode is reported as `plan-network`; with the variable unset, Plan mode stays strictly read-only as before.
- **Configuration/Migration**: No database migration. Set `AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS=1` in the service environment to enable network access for Plan sessions. Apply the change through the supported update flow so the flag reaches the service environment.
- **Verification**: New core tests cover the env-enabled sandbox/network flags and `plan-network` permission mode, plus the strict read-only default when the variable is absent; existing core/web/skill/version suites pass.
- **Rollback**: Unset `AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS` (immediate return to read-only Plan) or restore the previous console release through the update flow; running launchers are unaffected.

## 0.3.0 (Unreleased)

- **Date**: 2026-09-11
- **Issue/PR**: [#78](https://github.com/Fadekyun/agent-console/issues/78) / pending
- **Impact**: CLI/API create and delegation can pin Codex/Codex Pro models and reasoning effort before the first process starts. Desktop session creation exposes optional model, reasoning effort and Plan effort controls. Delegation also forwards provider-qualified OpenCode models. Existing default selection is preserved when overrides are omitted.
- **Configuration/Migration**: No database migration. The existing model field records the chosen model; effort overrides live in the persisted launcher. Model availability remains account-dependent. The private orchestrator skill enforces its tier policy separately.
- **Verification**: Core, web, deployment, profile, skill and version suites; JavaScript syntax; delegation/restart pin regression. Skill wrapper uses isolated CLI fixtures for native and legacy capabilities.
- **Rollback**: Restore the previous console release. Existing launchers retain their explicit model/effort flags; changing the installed release does not rewrite running sessions. The skill detects older CLI capabilities and retains its legacy pinning path.

## 0.2.0 (Unreleased)

- **Date**: 2026-08-26
- **Issue/PR**: [#76](https://github.com/Fadekyun/agent-console/issues/76) / pending
- **Impact**: Adds `codex-pro` as a distinct selectable provider that mirrors Codex execution, profile enforcement, skill isolation, and plan/auto modes.
- **Configuration/Migration**: Existing registries gain a separate `codex-pro/default` OAuth-native context at `~/.config/agent-console/codex-pro/default`; it must be logged in independently.
- **Verification**: Provider and authentication tests; desktop and mobile JavaScript syntax checks.
- **Rollback**: Revert the commit. The new authentication directory can remain unused and does not affect the existing Codex context.

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
