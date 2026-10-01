# Full staging revamp completion audit

Objective: finish issues #90, #140 and #6, update development documentation, push the implementation and deploy it fully to staging while retaining the current console. The first staging slice at 09ae3be is progress, not completion. Recheck each item against current source, tests, published branch and runtime before closing the goal.

## #90 — workbench

- [ ] Conditional attention/readiness strip; priority ordering; running/waiting/recent work.
- [ ] Distinct mechanical, attention and result states; repository/project, role, harness/model and activity.
- [ ] Compact child completion progress; structured recent results, failed/retryable results and next action.
- [ ] New session, reusable recipe preview/run, exact-configuration resume/retry.
- [ ] Session overview, terminal, result/handoff, children, effective configuration and history.
- [ ] Project/profile/harness/mechanical/attention/result/root filters with local preferences.
- [ ] Shared desktop/mobile state, keyboard navigation and pending/errors; terminal focus survives refresh.
- [ ] Real terminal matrix: embedded/expanded, wheel/touch, normal/alternate screen, output while reading, selection/copy/paste and denied clipboard, resize/keyboard/orientation, reconnect and multiple terminals.
- [ ] Playwright acceptance scenarios from the issue plus real staging verification.

## #140 — connected workflows

- [ ] One-session completion without compulsory review agents; manual Add/Attach by durable ID with role/skill checks.
- [ ] Ready/final results and selected versioned artifacts; source-attributed durable inbox with sequence, delivery and consumed acknowledgment.
- [ ] Justified suggestions; accept/edit/reject and explicit suggestion-only default.
- [ ] Ownership distinct from DAG dependencies; after-ready/after-final/alongside; joins and cycle rejection.
- [ ] Explicit reviewed auto envelope for repositories/actions/roles/harnesses/targets/budgets, versioned expansions and limits.
- [ ] Durable launch receipts; restart reconciliation without duplicate dispatch; capacity/backoff and unsupported adapter explanation.
- [ ] Failed inputs block dependents; independent branches continue; changes stale downstream results and review evidence.
- [ ] Coalesced replacement after current attempt; rerun limits; history retained.
- [ ] Pause blocks new dispatch; stop cancels/interrupts with durable history.
- [ ] Exact-candidate release evidence and authorized action/target; uncertain external outcomes require reconciliation before retry.
- [ ] Rewrite all seven Console guide bundles against implemented commands and verify a real two-session handoff.

## #6 — skills control plane

- [ ] Portable YAML and namespaced metadata/validated sidecar; legacy migration diagnostics.
- [ ] Provenance/source/revision/content hash, global/project scope, harness/profile compatibility and dependency diagnostics.
- [ ] Local-trusted/imported-unreviewed/reviewed/blocked states; staged import and review before activation.
- [ ] Content drift invalidates approvals; safe paths, duplicate/collision, secret and executable validation.
- [ ] Effective allow/ask/deny before launch, selection reasons and actual session snapshot/delivery.
- [ ] Capability/version-aware Codex/Pro, Claude, OpenCode, Hermes and Pi delivery; honest isolation limitations.
- [ ] Inspect/validate/sync/assign/remove through UI and CLI; source/content secrets never returned.
- [ ] Immutable running-session skill content; explicit refresh/restart semantics and migration/rollback.
- [ ] Canonical operating guides updated on n100, not only generated session roots; real harness discovery evidence.

## Publication / deployment gates

- [ ] Development roadmap and release notes reflect completed behavior, configuration, migrations, tests and rollback.
- [ ] Source commits published to the staging/development branch; no current-console cutover.
- [ ] Staging source identity matches published SHA; services and both version-switch routes verified.
- [ ] Existing staging data and current service/configuration preserved; migrations/recovery/rollback exercised.
- [ ] Requirement-by-requirement evidence attached here or in a linked final audit; no remaining required work.

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
