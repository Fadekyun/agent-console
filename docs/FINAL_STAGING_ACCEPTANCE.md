# Staging revamp acceptance — 2026-10-02

This is the historical 2026-10-02 staging acceptance record. Subsequent accepted changes and current verification are recorded in [the 2026-10-03 overhaul audit](OVERHAUL_VERIFICATION_20261003.md). Stable creation ordering now replaces attention/activity priority ordering; attention stays visible without moving work cards. Direct human Add session and single-action recipe/continuation launches supersede the earlier mandatory proposal/preview sequence. These are intentional usability changes, not missing original features.

Scope: implement #90, #140 and #6 together, publish the development branch and
make the implementation available as a separate staging console. The current
console remains independently usable. Main-branch merge, current-console cutover
and physical-device certification are not claimed by this staging release.

Functional candidate: **0.20.0 / bc72451b963b5a4aa4df3d169453f549af7a3b3e**,
published on `feature/connected-work-staging-20261001` and verified on staging.
The acceptance-documentation release adds only this audit, version labels and
development/release notes. Its deployed identity is recorded in
`STAGING_REVISION` and the task's final runtime evidence.

## #90 — Workbench

| Requirement | Evidence and result |
| --- | --- |
| Attention first; running, waiting and recent work; conditional readiness | `workbench_state.py`, state/API tests, and browser attention/readiness/filter scenario verify priority, explicit warnings and absence of an empty attention strip. |
| Distinct terminal, attention and result states; useful project/repository/role/harness/activity | State/API tests and actual staging overview records. A stopped terminal does not fabricate a successful result. |
| Compact ownership/child progress and logical attempt history | Native/connected bindings in `workbench_state.py`; actual native result/attached-session history verified on staging, as recorded in `WORKBENCH_VERIFICATION.md`. |
| Structured recent results, failures and next actions | Result/store and state tests; actual ready/final handoff and browser failure-result actions. |
| Accessible new session, recipes and resume/retry | `workbench_launch.py` and API tests; real staging Shell recipe/continuation retained its workspace and selected result. Browser tests verify save without launching, reviewed launch once, drift rejection and attributed errors. |
| Overview, Terminal, Results/Handoff, Children, Configuration and History | Shared Workbench views with authenticated APIs; live overview/history, launch-configuration and native handoff records, plus the current browser journeys. |
| Useful secondary filters and local preferences | Browser reload preserves result filtering; reset, root/all, attention and project/profile/harness selectors remain available. Backend state tests cover the corresponding fields. |
| Shared phone/desktop state; keyboard and pending/errors | All 42 Workbench scenarios pass at 1280, 360 and 390 pixels. They cover stale responses, pending deduplication, keyboard tree navigation and preserving terminal frame/focus/draft through dashboard refresh. |
| Nested terminal, follow-output, selection/clipboard, resize and reconnect | Real staging tmux/WebSocket/xterm matrix plus browser regressions in `WORKBENCH_VERIFICATION.md`. The page and terminal have separate scroll ownership; alternate-screen behavior and clipboard-denied fallback are explicitly tested. |
| Empty/multiple sessions, attention, children, completion/failure, doctor warning, recipe, keyboard and phone fit | These named #90 acceptance scenarios are present in `tests/ui/workbench.spec.mjs` and the current 42-scenario run. Live Skills inspection additionally passes at all three widths without overflow or page errors. |

Continuation starts a new conversation using recorded settings, preserves the
workspace and attributes the prior result. Unknown native defaults and older
sessions without receipts are explicit. The original #90 requirement did not
promise reconstruction of unknown provider settings or native chat restoration;
the earlier audit shorthand is clarified in `WORKBENCH_LAUNCHES.md`.

## #140 — Connected work

| Requirement | Evidence and result |
| --- | --- |
| One session can finish; optional manual Add/Attach | No mandatory review chain. Desktop/phone Add session and existing-session attachment tests; native single-step completion produces no compulsory follow-up. |
| Useful suggestions; accept/edit/reject; suggestions-only default | Engine/API tests bind proposal reason, role/action and reviewed launch configuration. Agent capabilities can propose but cannot accept or enlarge the operator envelope. |
| Optional automatic expansion with bounded scope | Engine tests cover repositories, actions, roles, harnesses, targets, concurrency, total/depth/rerun budgets, versioned expansion and explicit reviewed changes. |
| Ownership separate from dependency graph; ready/final/alongside, joins and no cycles | Graph/store tests and actual attached-session ownership. A sibling is not an implicit prerequisite; joins deliver selected input versions atomically. |
| Immutable selected results/artifacts, durable inbox and delivery/consumption | Real Codex Pro attempt read the selected snapshot after its source file was removed, acknowledged consumption and published one passing final. Durable fixture IDs are in `WORKFLOW_DISPATCH_VERIFICATION.md`. |
| Restart reconciliation, receipts, capacity/backoff and unavailable adapters | Real web restart during native execution retained one attempt/final; engine tests cover prepared/uncertain launch states, capacity and no duplicate dispatch. Missing adapters explain setup requirements. |
| Failure isolation, transitive stale work, coalescing and preserved history | Graph/engine tests cover failed prerequisites blocking dependent work, independent branches, changed input invalidating downstream evidence, one replacement after the running attempt and rerun bounds. |
| Pause and Stop | Engine/process tests distinguish preventing new dispatch from interrupting owned attempts and retaining their durable records. |
| Exact candidate/action/target release authority | Release tests and actual isolated target application match the selected full commit, archive hash and causal verification evidence. No production target is silently installed. |
| Uncertain external outcome reconciliation | Actual isolated adapter applied once, lost acknowledgment, survived web restart and was observed applied without a second write. IDs and target proof are in `WORKFLOW_RELEASE_VERIFICATION.md`. |
| Seven rewritten canonical guides and real two-session handoff | `CONSOLE_GUIDES.md`, actual copied-session receipts, the native consumed-snapshot handoff and native discovery of every current package across the supported harness matrix. |

The release protocol supports explicitly configured operator adapters. Successful
fixture verification does not certify every future adapter or authorize a
production deployment. The workflow capability is gated before unsupported
native task input can run.

## #6 — Skills

| Requirement | Evidence and result |
| --- | --- |
| Portable SKILL.md, namespaced metadata/validated sidecar, legacy migration | Registry/schema tests and all seven portable canonical guide validators. No Console-only plugin format is required. |
| Provenance/revision/hash/trust, scopes, compatibility and dependencies | Registry/API tests and actual pinned anonymous HTTPS Git staging. Fetched identity and package declarations remain distinguishable; local edits do not masquerade as the fetched revision. |
| Stage, inspect and review before activation; no automatic scripts/installers | Local/Git import tests reject unsafe transports/paths/symlinks/gitlinks and bound resources. Real remote fixture remained unreviewed and unactivated, then was archived. |
| Drift invalidates approval; allow/ask/deny before launch | Registry, skill-preparation, API and session tests cover content-bound decisions, changed supporting files, blocked trust and role/harness/project/dependency checks. |
| Secrets withheld; safe rendering and paths | Registry/Git/CLI/API secret and containment regressions; UI renders library data with textContent. Validation reports findings without returning matched credential values. |
| Capability-driven/version-aware delivery for Codex/Pro, Claude, OpenCode, Hermes and Pi | `NATIVE_SKILL_MATRIX.md`: actual native discovery, recorded tested versions, explicit unverified/missing states and preserved version policies. OpenCode1.18.31 selected-path discovery passes. |
| Honest isolation and permissions | Native probes demonstrate broader user/project sources. Inventories no longer claim exhaustive configured-source inspection. Console skill selection does not grant tool or release authority; unsupported role-assignment isolation remains guarded. |
| Inspect, validate, doctor, sync, assign and remove through UI/CLI | Package/profile validation and unassignment tests; authenticated APIs; browser inspection/approval/import and live per-harness delivery/current assignment display. No hand-edited native config is required. |
| Explain effective selection and actual session delivery | Shared prelaunch policy preview, immutable copied package hashes and durable delivery receipts. Global sync diagnostics are explicitly distinguished from a session's copy. |
| Immutable running copies; explicit refresh, migration and rollback | Actual session receipts, real-tmux snapshot/explicit-restart tests (including legacy Claude upgrade), registry migration/rollback evidence and preserved delivery histories. |
| Canonical operating guidance and native discovery | n100 canonical packages and staging mirror match. Maintenance guide .3 entrypoint SHA: `1fb7067118ec210d771289d305ad62b873151f92c48aca625feac5bec28a05bd`; deployed CLI validates package hash `d7c259cdd925df064bd2548d544dc1c906304c5b576277da6ec5de7905d1c53b`. All seven current guides are discovered by every tested native reader. |

## Publication, preserved state and review boundary

- GitHub ref and staging `STAGING_REVISION` matched the functional candidate.
  Development roadmap, release notes, canonical guide revisions and reproducible
  native checks are included in the published branch.
- Current, staging and `/versions` HTTPS routes returned 200 through CT969's
  actual proxy. Current stayed at process **2575152** and release
  `release-20260920-014453-dc29ccdd1b7e`; it was not restarted or cut over.
- v0.20 deployment preserved the contents of **all 40 tables** across the main
  and connected-work databases, verified against consistent pre-update backups
  by sorted row hashes without exposing row contents. No staging verification
  session remains running.
- Earlier real rollback/restoration checks preserved connected results/inbox,
  launch receipts, imports/policy and release outcomes. v0.20 adds no DB schema.
  Previous selected release and backups remain available.
- Main/master was not pushed or merged. Provider credentials and current-console
  host wrappers were not changed; missing staging accounts remain setup-required.

Current regression evidence: 289 skills/session/API tests passed, ten retained-
catalog fixtures skipped under the explicit empty allowlist; 51 capability/CLI/
shared-delivery tests passed separately; all 42 Workbench browser scenarios and
the final three focused Skills scenarios passed. Version checks passed. Native
reader probes and live browser/state/route checks are separate from mocked UI
fixtures. All 81 final Workbench/workflow backend tests passed, including release
authority and reconciliation; the only warning is the existing Starlette/httpx
deprecation.

Local detailed evidence: `handoffs/console-native-matrix-20261002/` in the task
workspace, alongside the earlier dispatch/launch/overview/release/Git evidence.
Historical checkpoint documents describe the scope at their earlier versions;
their “remaining work” paragraphs are superseded by this requirement audit.

Phone layout, touch, keyboard and orientation checks use mobile browser
emulation; physical iOS/Android feedback is still part of the user's satisfactory
cutover review. The current console remains available throughout that review.
This staging acceptance does not claim physical-phone certification, native chat
history restoration, universal native-source isolation or new provider accounts.
