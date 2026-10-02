## 0.23.2 — unreleased, 2026-10-02 (#90)

- Impact: an open terminal now shows one set of lifecycle actions. The session heading hides Open terminal and Stop session while the terminal is visible and restores them when it closes; the terminal header remains the single Stop/Close location. Phone outer headers use a single compact row (Sessions + Stop + Close) with 44px targets, the connection status stays available as screen-reader-only text and on the Sessions control, and Full screen stays desktop-only. A closed terminal stays closed across background refreshes.
- Configuration/migration: none. Browser-only change; refresh the page after release. No backend, schema, PTY or session behavior changes.
- Verification: Workbench browser tests on desktop, 360px and 390px cover duplicate lifecycle actions, close/reopen/switch/stopped sessions, close-then-refresh persistence, stale refresh after Stop, control bounds/44px targets, phone resized-height/orientation viewports, a 200% zoom-equivalent viewport, and the existing viewport/scroll regressions.
- Rollback: select 0.23.1, retaining all state. No session or configuration migration.

## 0.23.1 — unreleased, 2026-10-02 (#90)

- Impact: open desktop sessions fit short windows; long names and repository paths no longer push the terminal below the screen. Tree and details scroll within their panes. Embedded terminals use the outer Full screen control, removing the duplicate iframe action.
- Configuration/migration: none. Refresh browser pages after release.
- Verification: reproduce at 1024×600 with long names/paths and 25 children; check 800×500, details, terminal scrolling, expand/restore, close/reopen, navigation and desktop/phone terminal controls with Playwright.
- Rollback: select 0.23.0, retaining all state. Its fixed desktop terminal height can restore page scrolling.

## 0.23.0 — 2026-10-02 (#90)

- Impact: direct input is the terminal default on every viewport. Input/paste/send controls are optional, preserve drafts when collapsed, and show a draft indicator. Extra terminal keys/modes move into More; Reconnect appears when available. Session sidebar and tree branches collapse; a full-screen Sessions dialog supports navigation and adding children at any layer. Attention editing and Settings diagnostics use disclosures.
- Configuration/migration: none. No added terminal service or dependency. Refresh browser pages to load the interface. Existing session awareness, scheduled workflows and permission boundaries are unchanged.
- Verification: desktop/360px/390px Chromium coverage for optional controls, drafts, branch navigation, focus, lifecycle, native mouse scrolling and scheduled handoffs. See CONSOLE_SIMPLIFICATION.md for the architecture comparison and remaining hardware checks.
- Rollback: select 0.22.1 with all state retained. Browser-only disclosure preferences can be ignored by the older interface.

## 0.22.1 — 2026-10-02 (#90)

- Impact: mouse-enabled terminals use xterm's native wheel handling instead of forcing tmux copy mode. This lets full-screen agents receive their own scrolling input, including high-resolution trackpad deltas. Binary mouse reports are preserved. Typing exits tmux history after scrolling, and reconnect clears a previously stuck copy view. Touch gestures use the same native path when mouse reporting is active.
- Configuration/migration: none. Refresh browser pages to load the fix; running sessions need no restart. Copy mode remains the fallback for terminals without mouse reporting.
- Verification: native mouse/binary forwarding, fractional wheel input, touch routing and keyboard recovery regressions; a live-updating full-screen terminal fixture checks that scroll reaches the app without entering tmux copy mode. Physical Mac feedback remains necessary.
- Rollback: select 0.22.0 with all state retained. Its wheel interception can reintroduce the frozen-view behavior. No session or configuration migration.

## 0.22.0 — 2026-10-02 (#90)

- Impact: embedded terminals default to combined typing and history scrolling. Hidden views detach their browser PTY; reopening retains drafts and reconnects. Terminal startup no longer steals focus from the surrounding page. Wheel/touch bursts are coalesced. Stop is visible in the session heading and terminal header, Close terminal only disconnects the view, and stopping preserves private bounded recent output. Secondary session details start collapsed.
- Configuration/migration: no schema migration. `/api/me` reports configured session limits; Settings displays the cap. Main deployment raises `AGENT_CONSOLE_MAX_SESSIONS` from the default 12 to 24; staging keeps its separate cap. Scheduled workflow envelopes and child limits are unchanged.
- Verification: lifecycle/API tests, desktop/360px/390px Workbench tests for simultaneous typing/scrolling, hidden-client detachment, stop, draft preservation and focus; real staging tmux checks. See UI_REVIEW_20261002.md for findings and proposed next design pass.
- Rollback: select 0.21.0, retaining state and saved transcripts. Restore the prior runtime.env only if operator edits have not intervened; the cap is independent of source rollback. Existing session processes are preserved on web-only restart.

## 0.21.0 — 2026-10-02 (#90)

- Impact: grouped native sessions discover their live parent tree using `session relatives --current` and `session tree --current`. Relative or stable-ID output reads eliminate manual name lookup. Later additions and renames are visible on refresh. Normal launch instructions and guides no longer require workflow reporting or waiting for every child.
- Configuration/migration: none; uses existing native parent links across harnesses. No schema change. New instructions apply to future launches; existing sessions can use the commands immediately. Tree membership is context, not delegation or shared model memory. Scheduled workflows retain their contracts.
- Verification: read-only CLI tests cover late additions, rename, ancestors, descendants, ambiguous selection, saved transcript bounds, unrelated trees and cycles. Existing session/launcher tests and direct-child browser checks cover the unchanged launch path.
- Rollback: select 0.20.2 with state retained; restore guide backups only if rollout hashes still match. Older source lacks relative CLI commands. Running terminals and historical workflow records remain intact.

## 0.20.2 — 2026-10-02 (#90)

- Impact: manual Add session opens a child terminal directly. Dependency scheduling is an explicit option; agent proposals retain review. Recipe and continuation drafts start with one button; configuration validation and receipt deduplication happen internally. Optional configuration preview remains available.
- Configuration/migration: no schema or permission changes. Canonical Workbench .6, Coding .2 and Coordination .2 guides favor one-session completion, carry existing authorization forward and reserve extra reviewers/checkpoints for concrete needs or repository requirements. Existing running skill copies remain unchanged.
- Verification: desktop/360px/390px browser checks cover direct children, optional scheduled acceptance, single-action recipe launch, continuation preview invalidation and uncertain-launch receipt reuse. Existing backend admission and release checks remain unchanged. Guide validators and version checks pass.
- Rollback: select 0.20.1 with all state retained. Restore the three guide files only if they still match this rollout's hashes, using the host-local backups. Existing terminals and results remain intact.

## 0.20.1 — 2026-10-02 (staging, #90 / #140 / #6)

- Impact: completes the staging acceptance record for Workbench, connected workflows and the skills control plane. No functional change after 0.20.0.
- Configuration/migration: none. Current and staging remain independently accessible through the version chooser; no current-console cutover.
- Verification: 81 final Workbench/workflow backend tests, 289 skills/session/API tests, 51 capability/CLI/delivery tests, all 42 Workbench browser scenarios and three focused Skills scenarios passed. Ten retained-catalog fixtures are intentionally skipped under the empty allowlist. Native readers, real staging terminals/handoffs, restart/reconciliation, rollback, all 40 database tables, and both HTTPS routes are covered by the linked audit. Physical-phone feedback remains part of cutover review.
- Rollback: select 0.20.0 with all data retained. This patch changes documentation and version labels only. See `FINAL_STAGING_ACCEPTANCE.md` for evidence and limits.

## 0.20.0 — 2026-10-02 (staging, #6 / #90)

- Impact: adds package validation and explicit profile-validation/unassignment CLI commands; Skills inspection exposes per-harness delivery diagnostics. Fixes Claude shared-snapshot delivery through its supported additional-directory argument, including legacy launcher upgrade on explicit restart.
- Configuration/migration: no DB migration. Capability records identify native-tested Codex/Pro 0.159.2, Claude 2.1.287, Hermes 0.21.4, Pi 0.99.2 and OpenCode 1.18.30/1.18.31. Existing permissive unknown-version policy remains for Codex/Claude/Hermes, now visibly unverified; Pi/OpenCode retain strict admission. Native user/project/plugin sources remain explicit; no claim of complete isolation. Maintenance guide revision .3 is canonical and staging only; existing copies remain frozen.
- Verification: native readers discover all seven guides without model calls or account credentials; native Claude uses the actual Console launcher. Capability/CLI and real-tmux lifecycle tests, authenticated API checks and all 42 desktop/phone Workbench scenarios pass. See `NATIVE_SKILL_MATRIX.md` and final audit for scope.
- Rollback: select 0.19.3 with data retained. Older source lacks package validation and native Claude snapshot wiring. Existing launchers remain; avoid restarting Claude through the older overlay writer. Restore the exact backed-up .2 maintenance guide only after checking for operator edits. Current console is unchanged.

## 0.19.3 — 2026-10-02 (staging, #6 / #123)

- Impact: generates native Pi MCP configuration without the retired adapter schema; Pi/Hermes receive the same credential-enabled n8n, Directus, OpenRouter and Bushi descriptors and second-based request timeouts. Source Pi launcher uses the current package scope.
- Configuration/migration: no DB migration. Verified Pi 0.99.2 / Hermes 0.21.4; existing sessions/configuration remain until explicit relaunch. New URL overrides: `OPENROUTER_MCP_URL`, `BUSHI_MCP_URL`. Credentials stay environment references. Existing current-console wrappers/configuration are untouched.
- Verification: 16 adapter tests and installed native clients each discover/call four authenticated loopback fixture tools. Native Pi validates disabled entries and trusted-project override semantics. See `NATIVE_MCP_VERIFICATION.md`; no external MCP mutation or model task is claimed.
- Rollback: select 0.19.2 with state retained; its older Pi format needs the existing host normalization shim for new native Pi launches. Retain current host wrappers and do not restart existing sessions merely to change release.

## 0.19.2 — 2026-10-02 (staging, #90 / #140)

- Impact: connected-step preview shares normal session creation validation, inherits its parent project, and binds the resolved configuration/native sandbox before admission. Plan/read actions stay read-only; Configuration reports the actual task sandbox. Removes the default staging General maintenance-guide assignment that blocked plain Shell.
- Configuration/migration: no schema change. Previously queued approvals need a fresh edit/preview/review; running attempts retain their contract. Guide remains assigned to Orchestrator and compatible with explicit General assignment.
- Verification: 62 workflow/release tests, 73 authenticated API tests, responsive Add session scenarios, project/mode/admission-drift checks and native argv/receipt sandbox equality. Live Shell launch/stop verifies the assignment correction.
- Rollback: pause/settle active workflows, then select 0.19.1 with state retained. Old code does not enforce the expanded approval/configuration check or Plan-mode sandbox correction. Preserve current-console separation.

## 0.19.1 — 2026-10-02 (staging, #6)

- Impact: package-declared provenance appears only when the package explicitly supplies it. Live inspection found generated staging paths/hash defaults mislabeled as declared claims; fetched provenance and bytes were unaffected.
- Configuration/migration: none. The read-time correction also applies to existing staged imports.
- Verification: native live staged import, updated canonical guide delivery, desktop/phone inspection, and regression coverage for packages without declared provenance.
- Rollback: select 0.19.0 with the same state; its misleading declaration labels return. Current console remains unchanged.

## 0.19.0 — 2026-10-02 (staging, #6)

- Impact: UI and CLI stage an anonymous HTTPS Git package at a resolved commit without checkout/installers. Inspection and delivery distinguish fetched provenance, package declarations and later local edits; activation remains separate and hash-bound.
- Configuration/migration: additive import/review/receipt fields in existing policy JSON; no DB migration or credentials. Source/ref/subdirectory inputs and time/size limits are documented in `SKILL_GIT_IMPORTS.md`. Canonical skills guide updated to 2026-10-02.2 for staging.
- Verification: real Git-object safety/provenance/delivery tests, authenticated API tests, desktop/phone stage-inspect-activate checks and actual unactivated public HTTPS fetch.
- Rollback: select 0.18 with library/policy/imports/receipts retained. Older code retains trust/hash policy but lacks fetched-origin display and Git staging; use 0.19 to reconcile provenance. Current console unchanged.

## 0.18.0 — 2026-10-02 (staging, #6)

- Impact: Pi receives assigned/shared skill snapshots through its private agent directory, participates in capability-driven discovery/sync, and preserves conflicting operator paths. Exact-version skill adapters now verify selected delivery before session create/restart, including OpenCode.
- Configuration/migration: no schema change. Verified Pi 0.99.2 and OpenCode 1.18.30 required for selected-skill delivery; unknown versions explain the block. Existing sessions retain their copies. No credentials or staging harness setup are silently installed.
- Verification: installed Pi resource loader and Hermes native skill listing discover four new guide packages; unit/version/collision tests and real-tmux snapshot/restart tests with inert launchers pass. See `PI_SKILL_DELIVERY.md` for evidence and limits.
- Rollback: select 0.17.3, preserving skill/session state; remove Pi-dependent assignments before using an older release without its delivery adapter. Current console remains unchanged.

## 0.17.3 — 2026-10-02 (staging, #140 / #6)

- Impact: completes the seven canonical operating guides with bounded coding, coordination, review/verification and skills maintenance. Documents exact commands, optional session expansion and separate release authority.
- Configuration/migration: four new canonical packages mirrored and assigned only in staging to compatible roles; no database migration. Current-console mirror and assignments are unchanged.
- Verification: all new package validators, eight effective profile previews, exact copied bytes in three actual staging sessions, 95 skill tests (10 retained-catalog fixtures skipped), and the previously verified real two-session consumed-snapshot handoff. See `CONSOLE_GUIDES.md`.
- Rollback: select 0.17.2 for source; separately unassign new staging guides for future launches. Preserve immutable session copies/receipts and do not remove edited canonical packages. No current-console cutover.

## 0.17.2 — 2026-10-02 (staging, #140)

- Impact: release evidence checkboxes align with their labels at phone widths. Records the completed live candidate/action/outcome verification and canonical release-guide delivery.
- Configuration/migration: no source schema change; canonical `agent-console-release` revision 2026-10-02.1 is assigned to the staging release profile only. The verification target is isolated test storage and is removed from the live target catalog after testing.
- Verification: responsive release browser scenarios, real single-apply/lost-acknowledgment/restart/probe recovery, native copied guide receipt, and additive rollback/restoration. See `WORKFLOW_RELEASE_VERIFICATION.md`.
- Rollback: select 0.17.1 for UI; unassign the staging release guide to remove it from future release sessions. Retain completed receipts and selected artifacts. No current-console cutover.

## 0.17.1 — 2026-10-02 (staging, #140)

- Impact: release status refreshes update only history, preserve the candidate form, and poll active workers until their outcome is known. Late loads cannot append obsolete status after a newer refresh. Live staging exposed a queued-card race while the backend correctly retained an unknown outcome.
- Configuration/migration: none.
- Verification: desktop/phone release scenarios and live unknown-outcome reconciliation across a web restart.
- Rollback: select 0.17.0; release records remain compatible, but status may require a page reload. Preserve active worker sources and outcome reconciliation.

## 0.17.0 — 2026-10-02 (staging, #140)

- Impact: connected results can preview and authorize an exact commit/check/action/target release, then run a configured trusted adapter with a separate external outcome probe. Lost acknowledgments deduplicate; unknown outcomes require observation before explicit retry. Unknown releases appear in Work attention. Workflow Pause/Stop also govern release dispatch/interruption.
- Configuration/migration: optional owner-controlled `release-targets.json` plus installed adapters; no default production target or credentials. Additive release schema 1 in the companion database and `release-attempts/` worker files. See `WORKFLOW_RELEASES.md` for the adapter protocol and trust boundary.
- Verification: real adapter process, causal evidence, stale input, auth, idempotency, timeout, orphan, pause/stop and web restart behavior; desktop/phone release preview and reconciliation controls. Live staging verification uses an explicitly isolated filesystem target, not a production release.
- Rollback: pause/settle or stop/reconcile active operations; select 0.16.3 while preserving state and pinned worker source. Older UI ignores release records and cannot reconcile them. Restore 0.17 for those controls; current console remains independent.

## 0.16.3 — 2026-10-02 (staging, #90 / #140)

- Impact: operator history includes imported and integration records; status/interrupt/stop requests show pending state and deduplicate through refresh. A delayed continuation response cannot open the wrong session's draft. Workflow approval now binds the exact role instruction hash and checks the actual launch receipt before native startup.
- Configuration/migration: no schema change. Previously accepted, not-yet-launched workflow steps lack the role hash and need a fresh edit/preview/review. Existing running attempts retain their launch instructions.
- Verification: history authentication and scope tests; native workflow role-drift/race, independent-branch, coalescing and recovery regressions; desktop/phone pending/navigation scenarios.
- Rollback: select the previous staging release with the same state. Pause/settle active workflows first. The old version does not enforce role-hash approvals; current console is unchanged.

## 0.16.2 — 2026-10-02 (staging, #90)

- Impact: configuration receipt sequence IDs round-trip through browsers without losing integer precision. Recovery verification found the old nanosecond integer was rounded by JavaScript; stored records were unchanged.
- Configuration/migration: receipt APIs emit sequence as a decimal string, including legacy stored receipts; no database rewrite or schema change.
- Verification: authenticated API asserts lossless string IDs, continuation/receipt regressions and live rollback/restoration compare the full receipt.
- Rollback: select staging 0.16.1; newer stored string IDs remain readable, while earlier integer receipts regain the old browser precision limitation. Current console is unaffected.

## 0.16.1 — 2026-10-02 (staging, #90)

- Impact: a successful recipe launch clears the earlier save-only notice. Shell configuration omits inapplicable model/account fields and worktree policy reads Yes/No.
- Configuration/migration: none.
- Verification: desktop/phone recipe journeys assert the stale notice clears; live staging configuration screenshots and source identity checks.
- Rollback: select staging 0.16.0; records and current console are unchanged.

## 0.16.0 — 2026-10-02 (staging, #90)

- Impact: reusable recipes save/edit/remove without launching, then preview actual settings and skill revisions before explicit launch. Session Configuration records model/reasoning settings, role and launcher fingerprints, permission mode and selected skill hashes. Continue work starts a new conversation in the preserved workspace, including uncommitted files, and delivers the latest explicit result as an attributed input. Changed settings/roles/skills/launchers require a new review; live or supervised attempts use their existing controls. Repeated launch requests cannot create duplicate sessions, and lost acknowledgments recover only from matching receipts.
- Configuration/migration: additive versioned workbench launch tables in `connected-work.sqlite3`; no session-schema migration. Shared launch validation now resolves project repository before skill policy. Existing sessions without receipts remain explicitly unknown. Default model/reasoning values are labeled unpinned; a continuation preserves recorded Console settings, not native model memory. Explicit live restart invalidates exact continuation until a new reviewed launch.
- Verification: real-tmux recipe/configuration/continuation tests, dirty-worktree and selected-result checks, duplicate/lost-ack recovery, role/skill drift, authenticated API, existing core/native workflow regressions and desktop/phone recipe/continuation journeys. Live staging proof is retained in the task handoff.
- Rollback: select staging 0.15.1 and preserve the companion database. Old releases ignore the additive recipes/receipts and do not create new configuration records. Existing terminals/worktrees stay intact; restore 0.16.0 for these controls. Use SQLite backup API for WAL state. Current console remains separate.

## 0.15.1 — 2026-10-02 (staging, #90)

- Impact: Copy selection retains text when its button is clicked or tapped. Denied clipboard access opens a selectable manual copy sheet for both selection and full text; legacy clipboard exceptions also fall back safely. Paste guidance covers denied permissions and insecure origins. Pending Text View captures show loading state and cannot reopen a dismissed dialog.
- Configuration/migration: none.
- Verification: desktop/360px/390px regression reproduces selection loss before the fix and checks both denied-copy fallbacks; real staging terminal matrix covers native scrollback, output while reading, alternate-screen capture/paging, drafts, resize and reconnect. Evidence and limitations are in `WORKBENCH_VERIFICATION.md`.
- Rollback: select staging 0.15.0 and restart preview only; no data migration. Current console remains unchanged.

## 0.15.0 — 2026-10-02 (staging, #90)

- Impact: Work groups actual connected ownership and logical steps, folds native retries into attempt history, and preserves old attempt navigation. Attention, running, waiting and recent groups expose distinct terminal/attention/result states, child progress, structured results, failed-result actions and readiness warnings. Secondary filters persist locally. Session history exposes bounded workflow and audit events. Keyboard/tree focus and terminal instances survive refresh. Add session supports unlaunched logical parents; editing unrelated proposal fields preserves action/target/project scope.
- Configuration/migration: authenticated read-only workbench summary/readiness/history APIs over existing session/workflow tables; no schema migration. Browser-local filters are stored under `workbench-filters`. Readiness checks are cached for 30 seconds and never launch another agent.
- Verification: ownership/attempt/result/attention tests; API authorization and history attribution; desktop/360px/390px journeys covering empty/active/attention/completed/failed states, warnings, filters, tree keyboard focus and terminal preservation. Existing native dispatch regressions remain passing. Live staging proof is recorded with the task handoff.
- Rollback: select staging 0.14.2 and restart preview only; existing graph, attempt, result and audit records remain intact. Clear the optional local filter preference if desired. Preserve current Console and staging databases.

## 0.14.2 — 2026-10-02 (staging, #90)

- Impact: compact Next steps cards keep long tasks readable through expandable Task & configuration details. Stopped workflows omit irrelevant expansion guidance. Result handoff forms open only when requested, reducing phone page length.
- Configuration/migration: none.
- Verification: desktop/360px/390px browser journeys expand a long task before preview/accept, and explicitly open the handoff form before sending; live staging screenshots and overflow checks.
- Rollback: select staging 0.14.1; no data changes or current-console cutover.

## 0.14.1 — 2026-10-02 (staging, #140)

- Impact: native attempts receive completion instructions matching their supervised structured-result contract. Concurrent result publication no longer blocks status readers through rollback-journal lock cycles.
- Configuration/migration: companion workflow database uses WAL, with owner-only database/sidecars. Backups must use SQLite's backup API (or checkpoint while quiescent), not copy only the main file. Running session skill snapshots remain unchanged; new sessions receive the revised canonical results guide.
- Verification: a reader-held snapshot now permits concurrent result commit; generated native instructions omit interactive completion requirements. Workflow, existing-session and authenticated API regressions plus repeated real staging launch/restart check.
- Rollback: settle/stop native attempts, retain both databases and SQLite backup snapshots, then select the previous staging release. WAL is SQLite-compatible with earlier releases; preserve its sidecars until checkpointed. Current console is unaffected.

## 0.14.0 — 2026-10-02 (staging, #140)

- Impact: Add session now proposes a bounded next step with purpose, output, role and required inputs. Preview and accept/edit/reject control dispatch; suggestions are the default. Reviewed auto envelopes bound repositories/actions/roles/harnesses/targets, concurrency, total attempts, depth and reruns. Native Codex/Pro tasks use durable receipts, immutable selected inputs, structured results and optional justified follow-ups. Input changes coalesce after the current attempt. Pause holds dispatch; Stop interrupts connected work while preserving history. Uncertain launches require explicit reconciliation. Selected files now resolve from the actual isolated worktree.
- Configuration/migration: additive independently gated dispatch schema 1 and logical/native binding table in `connected-work.sqlite3`; owner-only launch manifests/output under `workflow-attempts/`. The web service runs a serialized dispatcher. No automatic expansion is enabled by default. Supported native adapters are capability-probed before acceptance; other harnesses remain available for manual interactive sessions. See [WORKFLOW_DISPATCH.md](WORKFLOW_DISPATCH.md).
- Verification: isolated real-tmux native-adapter tests cover waiting, pause/stop, exact inputs, coalescing, reviewed scope, budgets, lost receipts and concurrent restart recovery; authenticated operator/agent API, existing session regressions and desktop/phone Add session review journeys. Live staging native launch and recovery evidence is retained in the task handoff.
- Rollback: pause workflow dispatch and settle or explicitly stop active attempts before selecting v0.13.0. Preserve both databases, result objects, attempt directories and source release paths. A running runner is pinned to its launch release and can outlive a web restart; changing the web release alone does not stop it. Older versions retain result/inbox records but cannot dispatch/reconcile these steps. Restore v0.14.0 to recover controls. Current console remains separate.

## 0.13.0 — 2026-10-02 (staging, #140)

- Impact: connect existing sessions by durable ID without restart; versioned ownership and input DAGs; after-ready, after-final and pinned alongside inputs; cycle rejection; complete joins queued atomically into the durable inbox. Content changes stale dependent results, including transitive dependents. Publishing after explicit consumption records the input revision used.
- Configuration/migration: additive graph tables and independent graph schema gate in `connected-work.sqlite3`. Existing results and session schemas remain compatible. Graph changes are operator actions; native agents can inspect their connections. This increment does not automatically launch or interrupt agents.
- Verification: graph tests for joins, duplicate delivery, stale edits, cycles, failure isolation, snapshots, consumption and transitive staleness; authenticated API; desktop/phone connection journey; live staging attachment and input delivery.
- Rollback: select v0.12.1 and restart the preview service; preserve the companion DB. Existing results and inbox remain readable; graph controls return when v0.13.0 is selected again.


## 0.12.1 — 2026-10-02 (staging, #90)

- Impact: Results and handoffs open in a dedicated view on phones and desktop. Publishing is collapsed until requested, with inbox content visible immediately and a return to the session. Session refresh preserves the result form and existing terminal frame. Phone navigation has Work and Settings; Skills remains accessible from Settings. Failed result loads offer retry.
- Configuration/migration: none; result and inbox APIs/data are unchanged.
- Verification: desktop, 360px and 390px Playwright handoff journeys; version consistency; live staging inbox and skill delivery check.
- Rollback: select the prior staging release and restart only the preview service; current Console and stored results are unaffected.


## 0.12.0 (unreleased staging)

- Date: 2026-10-02. Issue: #140.
- Impact: one-session ready/final results, immutable selected file/commit artifacts, source/version-attributed durable inbox, separate delivery/consumption acknowledgments, capability-bound native reporting, and integrated desktop/phone result/handoff controls.
- Configuration/migration: companion `connected-work.sqlite3` schema 1 plus `result-objects/`; no sessions schema change. Native launchers export the reporting URL; older sessions need an explicit restart to adopt it. Add the canonical `agent-console-results` guide to staging's shared allowlist. See [RESULTS_AND_HANDOFFS.md](RESULTS_AND_HANDOFFS.md).
- Verification: store concurrency/idempotency/snapshot/recovery tests, native reporting and authenticated two-session API tests, responsive workbench result/inbox scenarios, and a live staging handoff before completion of this increment.
- Rollback: select previous staging source and preserve the companion database/result objects and original skills/config backup. Earlier sources do not expose result/inbox records; return to this release to inspect them. Current console stays separate.

## 0.11.1 (unreleased staging)

- Date: 2026-10-02. Issue: #90.
- Impact: clear obsolete missing-session notices after navigation, expose Skills directly in mobile navigation, and trim redundant skill descriptions and approval actions.
- Configuration/migration: none.
- Verification: desktop, 360px and 390px workbench scenarios include navigating from a missing session to Skills and confirming the old notice disappears. Live staging screenshots identified the defect.
- Rollback: select staging 0.11.0; no data changes. Current console remains separate.

## 0.11.0 (unreleased staging)

- Date: 2026-10-02. Issue: #6.
- Impact: portable YAML skill registry, staged import/review, content-bound approvals, project/role/harness/dependency policy, immutable session copies and persistent delivery receipts. Workbench Skills and launch preview connect the registry to actual session delivery; CLI exposes the same operations.
- Configuration/migration: pinned PyYAML 6.0.3; owner-only versioned `skill-policy.json` alongside the database, no SQLite schema change. Existing unbound approvals require approval of their current content. Canonical skill content remains the editing source. See [SKILL_REGISTRY.md](SKILL_REGISTRY.md).
- Verification: registry/skills tests, real tmux delivery/restart tests, authenticated API import/approval/drift/receipt tests, desktop and phone workbench acceptance scenarios. Live staging verification is recorded in the completion audit; the wider epic is still in progress.
- Rollback: select the previous staging release, preserve policy/import/delivery files and canonical library backup. Older releases do not enforce the registry; reconcile imported/restricted skills before launching through an older staging release. The separate current console remains available.

## 0.10.0 (unreleased staging)

- Date: 2026-10-01. Issue: #90.
- Impact: opt-in Connected Work home, responsive session tree and manual child creation; improved nested terminal scroll/reconnect and draft persistence. Classic controls remain available.
- Configuration/migration: `AGENT_CONSOLE_UI=workbench`, optional instance label/current/staging URLs; persistent staging uses a separate Unix account and state. No database schema migration or current-console cutover.
- Validation: API tests, desktop/phone browser tests and isolated live PTY smoke checks described in [STAGING_WORKBENCH.md](STAGING_WORKBENCH.md).
- Rollback: return to the current-console URL; remove only staging routes/service if withdrawing the trial. Preserve both workspaces and databases.

# Release Notes

## 0.9.0 (Unreleased)

- **Date**: 2026-09-19
- **Issue/PR**: [#138](https://github.com/Fadekyun/agent-console/issues/138) / this PR (part of the [#133](https://github.com/Fadekyun/agent-console/issues/133) decision-layer epic, related [#135](https://github.com/Fadekyun/agent-console/issues/135))
- **Impact**: Adds a read-only **Jev Ghost** view to the session dashboard so continuous probes of the shared Jev (TypeSafe) credential and typed-answer path can be reviewed without shell access. `agent_console/jev_ghost.py` reads the probe's sanitized artifacts (`runs.jsonl`, `latest.json`, `summary.json`) from `<state_dir>/jev-ghost` (override with `AGENT_CONSOLE_JEV_GHOST_DIR`), tails the history without loading the whole file, drops unknown fields, and degrades to an empty payload when artifacts are missing or malformed. `GET /api/jev-ghost?limit=N` sits behind the existing identity dependency and returns availability, the rolling summary, the latest run, the recent runs, the configured `AGCONSOLE_SHARED_SKILLS` allowlist, and per-run typed answers and skill-path status. The SPA adds a `Jev Ghost` navigation entry, a summary strip (runs, pass ratio, streak, last status, scenario, model), a shared-skill status strip, and a run table (when, scenario, status, checks, HTTP, typed answers, duration). The route is read-only; no mutation endpoint, no API call from the Console process, and no credential value is read, logged, or returned.
- **Configuration/Migration**: No database migration. The view is inert when no probe history exists. The probe itself is an operator-run systemd user timer that reads `TYPESAFE_API_KEY` from a reference-only `EnvironmentFile=`, so the Console process needs no new secret. `AGENT_CONSOLE_JEV_GHOST_DIR` is optional and defaults to `<state_dir>/jev-ghost`.
- **Verification**: `python3 -m pytest tests/test_jev_ghost.py -q` (reader default/limit/junk/unknown-field handling, missing-directory degradation, summary and shared-skills parsing, and the route contract for both populated and empty history). No live API calls in tests.
- **Rollback**: Re-select the previous release. The probe artifacts are plain files and are unaffected; no host file is modified by the release.

## 0.8.0 (Unreleased)

- **Date**: 2026-09-18
- **Issue/PR**: [#131](https://github.com/Fadekyun/agent-console/issues/131) / this PR
- **Impact**: Adds an explicit shared-skill allowlist (`AGCONSOLE_SHARED_SKILLS`) that is distinct from persisted per-profile assignments, so a small operator-managed set of standard skills reaches the harnesses that need it without a profile assignment or exposing the whole catalog. Codex and Codex Pro now materialize the allowlisted skills into the per-session isolated root alongside valid assigned skills on create **and** restart, so the existing `CODEX_HOME` overlay symlink exposes them. Hermes now receives `skills.external_dirs` in its per-session `HERMES_HOME/config.yaml` on create and restart; the merge preserves unrelated keys and operator-added directory entries, replaces the managed entry instead of appending (restart is idempotent), and refuses to rewrite an unreadable/non-object file. OpenCode now receives the same per-session isolated root as an **additional** `skills` config path (its V2 config `skills: string[]`/migrated `skills.paths`), so the managed path contains only the materialized skills while OpenCode's existing native sources stay active; no global root is mutated and the version-gated native materialization is untouched. Resolution is strict: an absent allowlist name, a `superpower`, an approval-required entry, a stale source, or a profile-disallowed skill raises instead of silently changing exposure, and the non-isolating-harness guard still uses profile assignments only, so assigning `typesafe-ai` to every profile (which broke pi/hermes creation) is no longer needed. Pi discovery is unchanged. `install.sh` now writes `AGCONSOLE_SHARED_SKILLS` (empty default) into `runtime.env`, so the updater's regeneration path carries a configured list through instead of silently dropping it.
- **Configuration/Migration**: No database migration. `AGCONSOLE_SHARED_SKILLS` is a comma-separated allowlist and defaults to **empty**, so existing installations are unaffected. `install.sh` (and therefore `update.sh`, which sources `runtime.env` before reinstalling) preserves the value across regeneration. The lxc-115 deployment opts in with `AGCONSOLE_SHARED_SKILLS=typesafe-ai` in `runtime.env` as a separate approved deployment step.
- **Verification**: `python3 -m pytest tests/test_shared_skill_discovery.py -q` — 23 passed, covering resolution/rejection rules, the from_env default/parse, assignment+shared merge, empty-allowlist no-op, Codex create/restart materialization, Hermes create/restart `external_dirs` (including unrelated-key and operator-directory preservation and idempotency), OpenCode per-session `skills` config, the missing-skill create failure with no side effects, and the preserved non-isolating-harness assignment guard. `python3 -m pytest tests/test_installer.py -q` — 47 passed, including default-empty persistence and a runtime.env regeneration check. Live bounded OpenCode probe: candidate-managed session config carries the isolated root (restart preserved), `opencode debug config` shows the additional path and keeps the MCP servers, and the native listing includes the shared skill; the earlier intermittent miss was traced to `opencode debug skill` truncating its JSON when stdout is a pipe (2/3 pipe runs incomplete vs 3/3 complete to a file). Scrubbed documented recipe (`env -u AGCONSOLE_RETAINED_SKILLS`) — 504 passed, 10 skipped (the legacy tmux review test is timing-flaky and passed on re-run).
- **Rollback**: Unset `AGCONSOLE_SHARED_SKILLS` (and restart the service) to return to assignment-only behavior, or re-select the previous release. Existing Hermes session configs keep their last `external_dirs` until the session is restarted; restarting after the variable is cleared removes the managed entry. Existing OpenCode session launchers keep their `OPENCODE_CONFIG_CONTENT` until the session is restarted.

## 0.7.3 (Unreleased)

- **Date**: 2026-09-18
- **Issue/PR**: [#125](https://github.com/Fadekyun/agent-console/issues/125) / [#126](https://github.com/Fadekyun/agent-console/pull/126) (this PR)
- **Impact**: Session renames no longer require a live tmux session, and `--current` keeps working for a harness whose session was renamed while it ran. `Manager.rename()` inspects the session first and only calls tmux when the harness is still running, tolerating the harness exiting between the inspection and the call, so a finished session can be renamed through the supported path (launcher, context file and database row are updated together). `AGENT_CONSOLE_SESSION_ID` is now the preferred identity for `session context --current` and `session attention --current`, falling back to `AGENT_CONSOLE_SESSION_NAME`, in both the writer path and the guarded read-only path. This removes the out-of-band monkeypatch an auto-namer needed to rename finished sessions, and is the prerequisite for renaming running sessions.
- **Configuration/Migration**: None. No schema change and no new environment variables: every managed launcher already exports `AGENT_CONSOLE_SESSION_ID`, and sessions created before that keep working through the name fallback.
- **Verification**: `tests/test_core.py` adds a finished-session rename (tmux session killed, rename must still update context/launcher/row) and a `--current` resolution test (exported id wins over a stale name); both fail against the previous implementation. Full documented recipe in a scrubbed environment plus the guarded read-only inspection tests.
- **Rollback**: Re-select the previous release. Renames performed with this release remain valid under the previous release (the launcher/context/database are consistent); only the id-preferred `--current` resolution reverts to name-only.

## 0.7.2 (Unreleased)

- **Date**: 2026-09-17
- **Issue/PR**: [#123](https://github.com/Fadekyun/agent-console/issues/123) (part of [#113](https://github.com/Fadekyun/agent-console/issues/113)) / pending
- **Impact**: Native pi and Hermes sessions receive their MCP client configuration from the release instead of host-edited wrappers. `CommandCodeAdapter` writes the pi credential as the explicit `$CMD_API_KEY` environment reference in `models.json` and `auth.json` (pi >= 0.74 treats a plain string as a literal), and generates the per-session pi `mcp.json` and the Hermes `config.yaml` `mcp_servers` block from a descriptor table keyed on environment-variable **names**. A server whose token variable is present in the Console process environment is emitted as a `bearerTokenEnv` / `${VAR}` reference; one whose token is absent is written to the per-session pi config as an explicit `{"disabled": true}` entry, so a host-global entry for the same name is suppressed rather than inherited and failed at connect time. The per-session file is rewritten on every launch, so a removed credential cannot leave a stale active entry. `<NAME>_URL` overrides the default endpoint, and unrelated shared servers (for example `openrouter`) are untouched. This retires the launch-time Hermes `mcp_servers` merge and the pi `auth.json`/`models.json` normalize shim. Hermes reasoning-effort gating and pi catalogue entries are unchanged.
- **Configuration/Migration**: None. No schema change and no new required variables: the Console process must keep exporting the token variables (`N8N_MCP_TOKEN`, `DIRECTUS_MCP_TOKEN`). Wrapper-side merges and the pi normalize shim become redundant and may be removed after the release is selected; leaving them in place is harmless for the generated server names.
- **Verification**: `python3 -m pytest tests/test_commandcode.py -q` — 15 passed; with `tests/test_version.py` — 18 passed. Coverage includes the credential-reference form, generated server shape, timeout and URL override, explicit suppression when a credential is absent, a same-session relaunch after the credential is removed, private file modes, and that no token value reaches any written file. The full documented recipe (`test_inspection.py test_cli_inspection.py test_core.py test_web.py test_logging.py test_commandcode.py test_auth_contexts.py test_profile_schema.py test_version.py`) passed 365 tests in a scrubbed environment (the legacy tmux path test requires the real `$HOME` and passes when re-run). Effective-resolution check against the installed pi adapter: loading the generated session config with `PI_CODING_AGENT_DIR` set resolves `openrouter` plus the generated servers, and reports the token-absent entries as disabled. Native smoke on the selected release: pi and Hermes `tools/list` against the n8n and Directus endpoints.
- **Rollback**: Re-select the previous release (`release-20260915-225402-8f7985cd82ac`). The release modifies no wrapper or host file, so no host restore is required; sessions created earlier keep their launchers and per-session configuration.

## 0.7.1 (Unreleased)

- **Date**: 2026-09-15
- **Issue/PR**: [#109](https://github.com/Fadekyun/agent-console/issues/109) / [#111](https://github.com/Fadekyun/agent-console/pull/111)
- **Impact**: Guarded read-only CLI inspection no longer returns `state-unavailable` during normal operation. One writer connection is held for the lifetime of the web service (`Database.keepalive()` plus the web lifespan) so the live WAL `-wal`/`-shm` sidecars stay materialized and `agentctl session context/list/tree/review` and `plan` reads work while the service runs. The guarded reader, its libseccomp write fence and its fail-closed behaviour are unchanged: the reader still creates and changes nothing, and inspection still returns `state-unavailable` when no writer holds the database.
- **Configuration/Migration**: None. No schema change. The web service holds one idle writer connection for its lifetime.
- **Verification**: `python3 -m pytest tests/test_inspection.py tests/test_cli_inspection.py tests/test_core.py tests/test_web.py tests/test_logging.py tests/test_commandcode.py tests/test_auth_contexts.py tests/test_profile_schema.py tests/test_version.py -q` (all pass). Isolated canary `127.0.0.1:33100` `/healthz` 200. Live check after promotion: `agentctl session context --current` 20/20 with the host stopgap service disabled. Independent read-only session review APPROVE on `75d7f38` / PR #111.
- **Rollback**: Revert the commit or re-select the previous release (`release-20260915-213629-41f9089fa385`). No data or configuration changes persist.

## 0.7.0 (Unreleased)

- **Date**: 2026-09-14
- **Issue/PR**: #107 / pending
- **Impact**: Adds Pi to session creation, authentication contexts, selectors and status. Hermes uses the selected CommandCode context/model instead of hardcoded OpenRouter settings. Both deliver Console profile and session context through native system-prompt inputs; sandbox, read-only and approval enforcement remain unsupported.
- **Configuration/Migration**: Run the value-blind `python -m agent_console.commandcode` provisioner with the key on stdin. It requires the authenticated catalogue to contain `deepseek/deepseek-v4.1-flash` before writing a 0600 credential or defaults. Install Pi 0.73.1 and use the supplied wrappers. Existing Hermes OpenRouter contexts are retained but the new adapter requires CommandCode. See `docs/PI_HERMES_COMMANDCODE.md`.
- **Verification**: Unit/integration/UI checks and bounded authenticated native smokes for both harnesses, each returning the Console context marker and a random local file value via a harmless tool call. No credential values in launchers, configuration JSON, public status or committed evidence.
- **Rollback**: Restore the previous selected release and backed-up runtime.env/auth-contexts.json/credential/wrappers. Existing sessions retain their launchers. CT115 deployment retains its live presence integration and queue-review features.


## 0.6.1 (unreleased)

- 2026-09-14, issue #105: native Codex progress messages no longer abort exact-artifact reviews before the final result. Strict final validation occurs after stream completion and child exit; malformed/conflicting verdicts remain blocked. Configuration/migration: none. Verification: progress-to-final, commentary-only, malformed/contradictory final, existing sequence and runner regressions. Rollback: restore prior task_runner.py in a guarded immutable release; preserve request evidence and do not retry uncertain deliveries.

## 0.6.0 (unreleased)

- 2026-09-14, issue #103: add native exact-artifact AGC review-request/status, fixed Codex Pro Astra low and read-only execution. Configuration: dedicated review-integration.json and private capability; no DB migration. Verification: focused artifact review and existing integration runner tests; independent review pending. Rollback: disable review integration and guarded-select previous release, preserving request/artifact evidence. See EXACT_ARTIFACT_REVIEW.md.

All entries from 0.1.1 onward include: date, issue/PR, impact, configuration/migration, verification, and rollback guidance. Entries for historic unpublished baselines may omit fields that are not applicable.

## 0.4.1 (Unreleased)

- **Date**: 2026-09-13
- **Issue/PR**: [#98](https://github.com/Fadekyun/agent-console/issues/98) / pending
- **Impact**: One manifest-validated installer keeps exactly eight normal CLI aliases on the selected release. Current selection and alias writers share a lock; bootstrap, upgrades and guarded rollback cannot restore checkout-era aliases. Includes an inert exact-hash patch renderer for the private context-sync writer, with no legacy fallback when the selected installer is missing.
- **Configuration/Migration**: No schema migration or native planning enablement. Explicit fresh bootstrap may initialize a new database; an existing database without a selected release requires recovery, not automatic source fallback. Applying the private helper patch and its managed PATH delta requires separate reviewed maintenance authorization. No live helper, alias, environment or service change is performed by preparing the patch.
- **Verification**: Isolated alias/manifest/inode/containment, shared-lock, bootstrap/upgrade, guarded rollback and helper-delegation fixtures; installer/release/core regressions and independent final source review required. No old writer, host helper, provider or real service is executed by fixtures.
- **Rollback**: Preserve receipts and current data. Use the new guarded release selector and reinstall current-based aliases; never restore historical alias targets or the known unsafe context-sync function while schema11 is active. Missing installer fails closed. The separate live-WAL read limitation is unchanged.

The stable runner refuses missing/incomplete current releases without checkout fallback. The host delegate executes sealed validated installer/package bytes; deterministic replacement and in-place race fixtures cover the boundary.

## 0.4.0 (Unreleased)

- **Date**: 2026-09-13
- **Issue/PR**: [#96](https://github.com/Fadekyun/agent-console/issues/96) / pending
- **Impact**: Consolidates capability-driven OpenCode skill delivery, nine guarded CLI inspection routes and the disabled preparatory planning-request protocol. Preserves skill containment, approval and assigned-profile isolation gates; native discovery is not per-session isolation. Detailed skill-content and delivery audit accompanies this release.
- **Configuration/Migration**: Additive schema10→11 retains existing sessions as interactive and stores request receipts separately. Native planning remains disabled/unverified; do not enable it based on skill discovery or SQL guard tests. Reader supports fixed schema10/11 projections and fails closed on unavailable enforcement/WAL prerequisites. OpenCode mutation is verified only for1.18.30; collision precedence remains unverified. Release/schema compatibility guards and supported rollback procedure are required before deployment.
- **Verification**: Integrated regression: 507 passed and 40 subtests; the 10 empty-catalog skips are covered by a separate 14-test assignment fixture. Isolated OpenCode 1.18.30 discovery, ES-module syntax and UI fixtures (13 passed, 8 layout-specific skips) passed. Independent final review remains required before publication. Historical component evidence does not substitute for testing this combined source. Use isolated HOME/XDG/state/socket and the exact real OpenCode binary, never its HOME-dependent wrapper; record native discovery separately from planning containment.
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
