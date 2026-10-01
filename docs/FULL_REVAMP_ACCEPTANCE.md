# Full staging revamp completion audit

Objective: finish issues #90, #140 and #6, update development documentation, push the implementation and deploy it fully to staging while retaining the current console. The first staging slice at 09ae3be is progress, not completion. Recheck each item against current source, tests, published branch and runtime before closing the goal.

## #90 — workbench

- [x] Conditional attention/readiness strip; priority ordering; running/waiting/recent work.
- [x] Distinct mechanical, attention and result states; repository/project, role, harness/model and activity.
- [x] Compact child completion progress; structured recent results, failed/retryable results and next action.
- [x] New session, reusable recipe preview/run, reviewed continuation/retry using recorded configuration with explicit unknowns.
- [x] Session overview, terminal, result/handoff, children, effective configuration and history.
- [x] Project/profile/harness/mechanical/attention/result/root filters with local preferences.
- [x] Shared desktop/mobile state, keyboard navigation and pending/errors; terminal focus survives refresh.
- [x] Real terminal matrix: embedded/expanded, wheel/touch, normal/alternate screen, output while reading, selection/copy/paste and denied clipboard, resize/keyboard/orientation, reconnect and multiple terminals.
- [x] Playwright acceptance scenarios from the issue plus real staging verification.

## #140 — connected workflows

- [x] One-session completion without compulsory review agents; manual Add/Attach by durable ID with role/skill checks.
- [x] Ready/final results and selected versioned artifacts; source-attributed durable inbox with sequence, delivery and consumed acknowledgment.
- [x] Justified suggestions; accept/edit/reject and explicit suggestion-only default.
- [x] Ownership distinct from DAG dependencies; after-ready/after-final/alongside; joins and cycle rejection.
- [x] Explicit reviewed auto envelope for repositories/actions/roles/harnesses/targets/budgets, versioned expansions and limits.
- [x] Durable launch receipts; restart reconciliation without duplicate dispatch; capacity/backoff and unsupported adapter explanation.
- [x] Failed inputs block dependents; independent branches continue; changes stale downstream results and review evidence.
- [x] Coalesced replacement after current attempt; rerun limits; history retained.
- [x] Pause blocks new dispatch; stop cancels/interrupts with durable history.
- [x] Exact-candidate release evidence and authorized action/target; uncertain external outcomes require reconciliation before retry.
- [x] Rewrite all seven Console guide bundles against implemented commands and verify a real two-session handoff.

## #6 — skills control plane

- [x] Portable YAML and namespaced metadata/validated sidecar; legacy migration diagnostics.
- [x] Provenance/source/revision/content hash, global/project scope, harness/profile compatibility and dependency diagnostics.
- [x] Local-trusted/imported-unreviewed/reviewed/blocked states; staged import and review before activation.
- [x] Content drift invalidates approvals; safe paths, duplicate/collision, secret and executable validation.
- [x] Effective allow/ask/deny before launch, selection reasons and actual session snapshot/delivery.
- [x] Capability/version-aware Codex/Pro, Claude, OpenCode, Hermes and Pi delivery; honest isolation limitations.
- [x] Inspect/validate/sync/assign/remove through UI and CLI; source/content secrets never returned.
- [x] Immutable running-session skill content; explicit refresh/restart semantics and migration/rollback.
- [x] Canonical operating guides updated on n100, not only generated session roots; real harness discovery evidence.

## Publication / deployment gates

- [x] Development roadmap and release notes reflect completed behavior, configuration, migrations, tests and rollback.
- [x] Source commits published to the staging/development branch; no current-console cutover.
- [x] Staging source identity matches published SHA; services and both version-switch routes verified.
- [x] Existing staging data and current service/configuration preserved; migrations/recovery/rollback exercised.
- [x] Requirement-by-requirement evidence attached here or in a linked final audit; no remaining required work.

## Final acceptance — 2026-10-02

All three issues are implemented and published to staging. See
[FINAL_STAGING_ACCEPTANCE.md](FINAL_STAGING_ACCEPTANCE.md) for the requirement
mapping, final regression counts, actual native/live evidence and review limits.
The checkpoints below are historical; their remaining-work notes are superseded
by the final audit. Current-console cutover remains a separate user decision.

## Registry checkpoint — 2026-10-02

Implemented source for the portable policy registry, staged local import/activation, hash approvals/revocation, immutable copied packages, durable launch receipts, UI/CLI controls and launch preview. See [SKILL_REGISTRY.md](SKILL_REGISTRY.md). This checkpoint does not close the full skills epic or connected workflows.

Verification: 95 skill tests (10 retained-catalog fixture tests skipped with the explicit empty retained allowlist), 23 real-tmux shared delivery tests, 64 authenticated API tests, 117 core session tests and 12 desktop/phone Playwright scenarios passed. Browser tests use the installed cached Chromium. The legacy terminal-output test now waits up to five seconds for its exact output marker; snapshot tests count skill directories separately from the receipt file. Failed stopped-session restart is tested to leave skill snapshots absent.

Remaining skills work includes the full provider/version discovery matrix (especially Pi), canonical operating guide migration, repository import provenance and the final live harness evidence. #140 scheduling/results/inbox and the remaining #90 workbench contract are still open above. Keep this goal active after publication of this checkpoint.

## Results / connected-input checkpoint — 2026-10-02

Versions 0.12–0.13 implement explicit versioned results and selected immutable artifacts, native capability-bound reporting, source-attributed durable inbox, and delivery/consumption acknowledgments. Real two-session CLI delivery and rollback/recovery were exercised on staging. Results now have their own shared phone/desktop view; phone navigation has Work and Settings.

Connected inputs add durable-ID attachment of existing sessions, separately versioned ownership/input DAGs, after-ready/after-final/pinned alongside conditions, joins, cycle rejection and content-based transitive stale state. Joins enter the inbox atomically with retry deduplication. Output binding requires publication after the joined inputs were acknowledged consumed. Existing processes and skill snapshots remain intact. UI, API, store and native reporting share these records.

This does not complete #140: launch-parent and connected ownership views still need unification; Add session with delayed dispatch, suggestions and reviewed auto envelopes, launch reconciliation/coalescing, pause/stop and release gates remain open. Full #90/#6 scope above is still required. Do not close the goal on this checkpoint.

## Reviewed-dispatch checkpoint — 2026-10-02

Version 0.14 adds proposed logical steps, accept/edit/reject, explicit reviewed automatic envelopes, probed native Codex/Pro task input, durable launch receipts and immutable configuration/skill checks. Required inputs gate launch; replacements coalesce behind the current attempt. Budgets include the initial/attached sessions and every launched attempt. Pause holds new dispatch, Stop interrupts owned connected sessions, and uncertain launches need explicit reconciliation. Native structured finals publish selected worktree artifacts and consumed-input claims; optional justified follow-ups pass the same proposal gate.

Unit/native-fixture, API and browser evidence covers dispatch, crash recovery, coalescing, scopes, budgets and control behavior. See `WORKFLOW_DISPATCH.md` for supported adapters and rollback. Exact external release gates, unified tree navigation, remaining workbench features, all seven guides and the full skill-provider/device matrix remain open. This checkpoint does not complete the goal.

## Native dispatch verification — 2026-10-02

The checked #140 items above are supported by [native dispatch verification](WORKFLOW_DISPATCH_VERIFICATION.md), not by terminal exit status alone. A real Codex Pro attempt consumed a selected immutable snapshot after its original file was removed, published a passing structured final and survived a web restart with one durable launch. Rollback to 0.13 and restoration preserved all 14 workflow table counts. Current-console PID/release and both switching routes were unchanged.

This verifies those protocol portions only. The launch tree still needs connected ownership/attempt navigation, exact external release gates remain unsupported, and the unchecked workbench/skills/guide/provider requirements remain necessary for the full goal.

## Work overview checkpoint — 2026-10-02

Version 0.15 unifies main Work/Session navigation with connected ownership and logical steps. Native attempts collapse under their task with explicit history links. Backend summaries keep terminal, attention and result states separate; stopped terminals without reports remain unknown. Explicitly acknowledged failures remain failed but can leave the attention queue. Results, child completion, readiness warnings, priority groups and locally persisted secondary filters are visible without opening a terminal. History exposes attributed workflow/session audit events with separate cursors. Refresh preserves tree focus and active terminal frames.

The checked #90 summary/filter requirements are covered by WorkbenchState tests, authenticated API coverage, browser scenarios and live staging evidence. Recipes, exact-configuration continuation, effective configuration and the complete terminal matrix remain open; this is not whole-goal completion.

## Terminal verification checkpoint — 2026-10-02

[Workbench verification](WORKBENCH_VERIFICATION.md) records real staging tmux/WebSocket tests at desktop and emulated phone widths, native/denied clipboard browser tests and the selection/dismissal fixes in 0.15.1. The terminal matrix checkbox reflects these explicit tests; physical-device certification is not claimed. Recipes, exact continuation, effective configuration, release gates and remaining skills/guide requirements above remain open.

## Launch configuration checkpoint — 2026-10-02

Version 0.16 adds [recipes and configuration receipts](WORKBENCH_LAUNCHES.md), shared preview/create validation, idempotent operator launch requests and continuation in the existing workspace with an attributed result input. It reproduces recorded Console configuration and blocks changed role/skill/launcher settings; unknown native defaults and pre-receipt sessions are explicit. It starts a new conversation and does not claim native conversation restoration. Full shared UI/attention/error, release and skills requirements remain open above.

## Release verification checkpoint — 2026-10-02

Versions 0.17–0.17.2 add explicit operator release grants for selected immutable commits and matching current check results, configured action/target adapters, durable worker receipts and observed external outcomes. [Release verification](WORKFLOW_RELEASE_VERIFICATION.md) records real isolated target execution, unknown-outcome handling across web restart, no duplicate apply and responsive UI evidence. Native regression tests verify failed-prerequisite isolation and stale candidate/check rejection. These support the two checked #140 requirements above.

The canonical release guide is the third implemented guide bundle after Workbench/session basics and Results/handoffs. Coding, coordination, review/verification and skills maintenance remain required, along with the remaining #90/#6 and final publication/audit requirements. This checkpoint does not complete the full goal.

## Operating guide checkpoint — 2026-10-02

All seven canonical bundles are now implemented against the current controls and command parsers. [Console guides](CONSOLE_GUIDES.md) records revisions, role assignments, exact entrypoint hashes and delivery evidence. Eight new profile previews and three actual temporary staging sessions verified the four remaining bundles. The real two-session consumed-snapshot handoff is recorded separately in `WORKFLOW_DISPATCH_VERIFICATION.md`. This supports the final guide checkbox for #140 without claiming that delivery proves model use.

The checked registry criteria are supported by the current 95-test skills run (10 retained-catalog fixtures skipped with the explicit empty retained allowlist), the earlier real-tmux/API coverage and actual immutable delivery receipts. Tests cover schema/legacy diagnostics, staged activation, interrupted activation denying trust, content-bound approval/revocation, safe copy and drift, collision/secret/executable/dependency checks, policy selection and receipt survival. The remaining combined provenance criterion stays open for Git import, and capability/discovery, UI/CLI final acceptance and full #90 acceptance remain open. This is progress, not whole-goal completion.

## Pi delivery checkpoint — 2026-10-02

Version 0.18 fixes the missing link between Pi's per-session agent directory and Console's copied skill selection, adds its verified 0.99.2 capability, and enforces exact-version admission for selected Pi/OpenCode skills before launch/restart. [Native discovery evidence and integration checks](PI_SKILL_DELIVERY.md) distinguish actual installed Pi/Hermes skill readers from inert-launcher tmux tests and from unavailable staging model credentials. The complete provider matrix remains open; this checkpoint does not close #6 or the full goal.

## Git provenance checkpoint — 2026-10-02

Version 0.19 adds bounded, inert anonymous HTTPS Git staging to UI and CLI. The resolved commit/original package hash survive activation, review and actual selected-delivery receipts; declared metadata and locally edited content remain distinguishable. Real Git-object tests plus a real public HTTPS fetch and responsive stage/inspect/activate browser checks support the provenance criterion above. Existing scope/compatibility/dependency tests cover its other parts. See `SKILL_GIT_IMPORTS.md`. Provider matrix, final UI/CLI and remaining Workbench acceptance remain required for the whole goal.

## Shared launch contract checkpoint — 2026-10-02

Version 0.19.2 removes the separate connected-step launch validation path: preview now uses `SessionManager.prepare_launch`, inherits the project, binds the resolved configuration and actual native sandbox, and checks the session receipt before native startup. Plan mode and read actions remain read-only, with that sandbox recorded in Configuration. Native fixture regressions cover invalid preview settings, project mismatch, admission drift and actual argv/receipt equality; existing workflow/release/API and responsive Add session scenarios pass. Unpinned native defaults remain explicit rather than invented. The default maintenance-guide assignment was also removed from staging General after proving it blocked Shell; a real fresh Shell launch/stop now passes. Remaining full-provider and final #90 acceptance checks still apply.

## Native MCP configuration checkpoint — 2026-10-02

Version 0.19.3 corrects Pi's retired adapter schema and aligns native Pi/Hermes managed server selection and timeouts. [Native verification](NATIVE_MCP_VERIFICATION.md) records four authenticated loopback tool discoveries/calls per installed harness, disabled entry validation and trusted-project override behavior. The source wrapper points to the current Pi package scope. Current-console wrappers and credentials are preserved. This closes the identified MCP format gap without claiming complete tool isolation or authenticated staging model use; full provider skill discovery and remaining Workbench/final acceptance items are still required.

## Native matrix and Workbench acceptance — 2026-10-02

Version 0.20 completes the identified #6 delivery and operator-surface gaps. [Native skill matrix](NATIVE_SKILL_MATRIX.md) records actual readers for all six harness identifiers and all seven guide packages. Native Claude testing found its ineffective environment override; additional-directory delivery now works through the actual generated launcher, with a real-tmux legacy-restart/idempotence regression. The Skills inspector exposes per-harness native sync/version/discovery status; CLI package validation and explicit profile validation/unassignment complete the command surface. Registry, trust, secret-redaction, compatibility, API and session lifecycle tests support the other checked skill requirements.

All 42 current Workbench browser scenarios pass at desktop/360/390 widths: new/recipe preview and one launch, configuration and continuation drift, pending deduplication and errors, stale responses, release observation, Add session/drafts/mobile terminal, reconnect/scrolling, skills approval/import/delivery, results/inbox/attachment, attention/readiness/filter priority, empty state and keyboard/focus through refresh. Current source and earlier real staging recipe/continuation, history and terminal evidence support the three newly checked Workbench rows. Final current-release live verification remains a separate publication gate below.

The earlier internal shorthand "exact-configuration resume" exceeded #90's actual requirement for accessible new/recipe/resume actions if interpreted as native chat restoration or recovery of historically unrecorded defaults. The implemented contract preserves recorded settings and workspace, detects drift, attributes the prior result, and labels the new conversation and unknown defaults. This matches the original issue and the accepted design's context/permission boundaries; no native history restoration or invented setting is claimed. See `WORKBENCH_LAUNCHES.md` and its native/real-session evidence.
