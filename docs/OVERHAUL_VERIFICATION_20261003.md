# Console overhaul verification — 2026-10-03

Issue: #141. Baseline: local deployed `cf4cb0864c87`, v0.24.1. Initial deployed release: v0.25.0 (`22af690441c0`). Follow-up candidate: v0.27.1 on the isolated `feat/console-overhaul-20261003` branch. GitHub main was older than the deployed baseline; this work does not discard those local improvements or imply a push/merge.

## Delivered behavior

- Work cards retain immutable creation order; attention and activity remain visible without moving cards. Tree siblings have deterministic ties. Open work targets the root, including stopped roots; direct child and historical-attempt links retain their explicit destinations.
- Tree rows and Add controls are reconciled in place, preserving a click across refresh, focus and scroll. Cancelling a drawer-origin Add returns to the same drawer location. Terminal focus changes only on explicit opening and respects Scroll/Select mode.
- Forest, Ocean and Violet palettes support System/Light/Dark appearance. Stored preferences, other tabs and embedded terminals synchronize; xterm colors change without rebuilding the terminal or reconnecting it. Semantic text contrast is checked across all six combinations.
- Settings → Environment provides owner-authenticated, write-only global/project variables, multiline and empty values, disable/delete/suppress, quotas and revision status. Private atomic files and literal process bootstrap avoid shell expansion and value disclosure in launch scripts, arguments, API descriptions or SQLite. Project deletion clears its scope; restart validates first and refreshes the process/MCP configuration together. User capability-named variables rotate normally while the session's own reporting credential remains stable.
- Managed session inspection and own completion use a scoped service capability, so read-only roles need no database/config writes. Project peer reads are bounded and private integration requests are redacted. Writable delegation controls descendants; read-only agent delegation cannot escalate. Human Add session retains its separate operator authority.
- Role instructions and native skill adapters are aligned. Selected Claude/OpenCode/Hermes snapshots no longer require suppressing unrelated native sources. Customized installed profiles expose differences and are retained during normal updates. Seven canonical Console guides were updated on n100 and the configured mirror to reviewed revision `2026-10-03.1`. Four disposable native sessions verified all seven copied package hashes and receipts; existing session snapshots remain unchanged.
- Maintenance preserves runtime keys/comments and custom service definitions. New releases have independent Python dependencies; canaries have private state, no live dispatch, loopback binding and identity/PID health checks. Lifecycle changes during updates no longer imply lost sessions.
- Audit fixes cover interrupted release-column migration, structured/quoted credential log redaction, duplicate integration request recovery, exited-child cleanup races, trusted proxy identity provenance, and exact browser terminal origins.

## Verification evidence

Tests use isolated state/configuration/workspaces/tmux sockets. Broad runs never target the live database. Test output is retained in the implementing host's `/tmp` logs; runtime evidence and canonical guide payloads are kept in the parent workspace's `handoffs` directory.

| Scope | Observed result |
| --- | --- |
| Integrated Python regression run | 1,010 passed, 10 skipped, 104 subtests passed; two stale test fixtures failed. The fixtures bypassed the reader constructor and asserted an obsolete delegation error string. |
| Final affected Python suites | 119 passed, 15 subtests passed, including both corrected fixtures, environment rotation/deletion/suppression, HTTP/WS auth, inspection and version checks. |
| Integration request race regressions | 75 tests and 30 subtests passed in the implementing child, then included in the integrated run. |
| Maintenance/installer/release suites | 185 tests passed in the implementing child, then included in the integrated run. A real isolated canary also passed. |
| Logging/migration suites | 101 tests covered successfully, including two Uvicorn checks rerun with the correct interpreter. |
| UI broad run | 214 passed, 43 skipped, 10 failed. The failures were rechecked against the final integrated UI; the old attention fixture and restored-drawer expectation needed corrections. |
| Integrated targeted UI | 70 passed, 2 skipped; three old drawer-reopen assertions failed because cancellation now restores the drawer. Corrected expectations and final ordering checks are verified separately below. |
| Final drawer/order browser checks | All 9 passed across desktop, 360px and 390px. Every failure from the broad UI run was resolved and rechecked. |
| Independent navigation review | 27 passed on native child candidate `a186aa8`, covering nine scenarios at desktop, 360px and 390px. Parent integrated it as `ae579ea`. |
| Package and source checks | v0.25.0 wheel built with an isolated current build backend; JavaScript syntax and patch whitespace checks passed. SPDX metadata now declares a compatible build backend. |
| Native skill discovery | Credential-free OpenCode 1.18.31 and Hermes discovery probes passed. Claude adapter fixtures pass; the installed Claude symlink is broken, so no current native Claude result is claimed. |
| Canonical guide rollout | All seven packages validated; drift-checked canonical and mirror updates backed up and reviewed. Four native session profiles received all seven current guide hashes unchanged. |

The broad test run's workflow suite passed after the earlier host-load-related runs and a run affected by editing source during collection. Those earlier failures are retained as diagnostic history, not counted as passing evidence. The only warning in the final backend runs is Starlette's existing httpx TestClient deprecation.

## Follow-up audit and verification

The scout reviewed the full frontend and consulted primary MDN/WAI guidance. Confirmed findings were implemented: environment scope isolation, recovered bootstrap, older release candidates, skill failure diagnostics, reusable controls, guarded editors, preserved drafts, mobile group navigation and duplicate-submit prevention, accessible icons/dialogs/terminal close, project management, and insecure-HTTP clipboard handling. Owner project/environment CLI commands were added; advanced workflow/connection/release actions were subsequently given owner CLI parity in v0.27.0 below.

- Integrated affected backend suites: **97 passed, 82 subtests passed** (capacity, owner CLI, selected-release entrypoints, environment, session controls, inspection and versions).
- Combined desktop controls/icons/project run: **25 passed, 1 skipped**; two fixture errors were corrected (polling instead of clicking a toolbar obscured by an open inspector, and the select's accessible role). Both corrections passed on rerun, giving 27 verified cases.
- Integrated workbench/navigation/capacity checks: **21 passed**, including merged bootstrap recovery, root-first opening with stopped or running roots, explicit child navigation, stable Add clicks during polling and configured capacity explanations.
- Integrated phone environment/icons/project checks: **19 passed, 1 skipped**, at 360px/390px with explicit 320px reflow checks.
- Integrated actual insecure-HTTP clipboard checks: **15 passed, 6 desktop-only cases skipped on touch projects**. Native keyboard Copy/Paste, modal selection/focus, manual paste, no accidental send and dock controls are covered. Two existing secure-context clipboard regressions also passed in the child.
- Read-only live scout successfully used tree, root review and completion through the capability API with authorized network access. Initial EPERM came from sandbox network denial; no filesystem writer fallback or new repository/config write access was introduced.
- The required child wait reports all three current delegated sessions successful/ready_for_review. Its aggregate failure reflects three historical stopped children without completion receipts; these were not reclassified as successes.
- Host disk/page I/O stalls caused browser startup failures and aborted runs. Those are retained separately and are not counted as passing evidence. Final affected checks were run sequentially after pressure subsided.

References used for the browser behavior: [MDN Clipboard API](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API), [MDN currentTarget](https://developer.mozilla.org/en-US/docs/Web/API/Event/currentTarget), [WAI buttons](https://www.w3.org/WAI/ARIA/apg/patterns/button/), [WAI dialogs](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) and [WCAG reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).

## Deployment and rollback

The user explicitly authorized the current Console's port-3210 update. The rollout prepares a release from the exact clean local candidate, verifies its private canary, and backs up runtime configuration, service units, runner/helper, both SQLite databases and durable session identity inventory. It then selects the candidate through the schema guard, installs the release-aware runner, and verifies exact release/PID health and preserved session records. The initial profile directory was a clean historical checkout. The follow-up rollout copies the current installed instructions to a durable state profile directory, preserving any UI edits and keeping future edits outside immutable release files.

Before restart, set the verified CT969 proxy CIDR and exact LAN/Tailscale origins documented in REQUEST_AUTHENTICATION.md. Keep login, LAN policy and other runtime values. Do not stop or restart user harness sessions. Apply reviewed guides to canonical n100 and the configured mirror with drift checks/backups, refresh only their content-bound reviews, and verify delivery through an owned fixture.

Live result, selected SHA, private backup location and session-count comparison are recorded by `handoffs/console-overhaul-deploy/outcome.json` in the parent workspace. The rollout must not be reported as deployed merely because this source document exists. Rollback keeps operational databases and running tmux sessions; it restores the prior selected release and saved runner/config only after schema compatibility checks. See RELEASE_NOTES.md and platform-maintenance.md.

## Remaining verification boundaries

Native macOS login startup, Windows/WSL2 bootstrap and Docker image execution were not available for this Linux-host verification. Linux `/proc`-based presence and durable integration-process ownership are not claimed portable merely because a LaunchAgent can be installed. Physical mobile keyboards/trackpads remain separate from Chromium viewport checks. CI configuration is added but remote CI/branch protection was not run or changed because no push/merge was performed. A skill discovery result proves availability, not that a model invoked every skill. No claim is made that every future workload or third-party harness version is verified.

## Final requirement audit and CLI parity — v0.27.0

The original design and v2 implementation plan were compared with current source, deployed behavior and acceptance records. Concept B and the subsequent user request for stable ordering supersede earlier concept/priority-order prototypes. Mandatory multi-stage review, provider chat-history restoration and suppression of every native skill source were not accepted requirements. The current simple one-session path remains the default.

| Requirement | Current implementation and evidence |
| --- | --- |
| Stable Add/session ordering and root-first resume | Keyed tree reconciliation, immutable creation ordering and explicit root navigation; workbench/browser and live checks above |
| Simple mobile controls, icons and themes | Accessible SVG controls, 44px targets, phone reflow, three palettes and appearance modes; desktop/phone/insecure-HTTP tests above |
| Clipboard over plain IP | Selection-preserving copy fallback, native paste and unsent manual draft; actual insecure-origin browser and live checks above |
| Children beyond three, bounded agent reads/control | Unlimited default per-parent capacity with explicit/global/depth guards; all roles use project-scoped reads, writable descendant controls and read-only completion; focused backend/native scout evidence above |
| Shared environment entry and launch delivery | Write-only global/project UI/API/CLI, safe inheritance/suppression/rotation; an additional execution test launches all seven adapter specs through an inert executable and verifies actual child variables, literal multiline/empty values, no expansion and no launcher/SQLite values |
| Current roles and skills | Eleven installed roles match reviewed bundled templates; seven canonical guides delivered with content receipts; three guide revisions add current owner CLI boundaries |
| UI/CLI/backend consistency | `workflow manage` reuses UI models and services for owner policy, review, connections and release; `session create --parent` inherits parent project/repository for human owners while managed invocations retain delegation restrictions |
| Durable workflows, selected results and release authority | Existing staging acceptance and native dispatch/release evidence; new owner CLI tests exercise actual services, stale versions/hashes, dependency signatures, uncertain-attempt reconciliation and release grants/probe admission without executing an external release |
| Deployment and issue cleanup | Isolated exact-SHA release/private canary, configuration-preserving installation, state backups, exact runtime health and session preservation; obsolete/fixed GitHub issues closed with evidence under #141 |

Current CLI/environment/capacity/control/inspection validation: **75 passed, 131 subtests passed**. New workflow tests cover 60 managed-marker/route cases, reject before manager construction or request reads, and never silently generate authorization hashes/keys. Owner child tests use real isolated tmux sessions; name/ID parents inherit project/repository and missing parents do not launch. Managed child requests remain on the capability endpoint, and incomplete reporting context cannot fall back to a local writer. See selected-release-entrypoints.md for operator examples.

Physical-device, unavailable native Claude, non-Linux runner and remote publication/CI limits remain explicitly bounded; they are not represented as executed verification. Source publication remains separate from the user-authorized local port-3210 deployment.

Additional final review found and fixed stopped-name reuse creating parent cycles.
The shared admission guard resolves the final reused identity under lock and
rejects self/ancestor placement before launch. Rejection leaves the complete
session database and existing context, launcher, environment, skills and overlay
files unchanged. Both manager and authenticated child API regressions exercise
this behavior. Invalid non-text workflow policy modes now reject consistently
through the shared service and owner CLI without a traceback or policy mutation.

Canonical guide rollout is complete: Workbench, Coordination and Release `.2`,
with four remaining guides `.1`. Fresh coder/orchestrator/reviewer/release sessions
verified all seven copied package hashes against the reviewed mirror; fixtures
were stopped and hidden. Evidence: `handoffs/console-guides-20261003-parity/`.

Final integrated backend validation after all fixes: **235 passed, 141 subtests
passed** in 119 seconds. Suites: core, workbench launch, workflow engine, parent
cycles, child capacity, owner workflow/project/environment CLI, environment,
inspection, session control and version. Log: `/tmp/console-final-parity-tests.log`.
The only warning is the existing Starlette/httpx test-client deprecation. The
v0.27 changes do not change UI interaction code; the previously recorded 82
browser checks and live desktop/phone/insecure-IP acceptance remain applicable.

## Live-discovered exact tmux target fix — v0.27.1

After v0.27.0 passed its canary and preserved 573/573 session records, a live
fixture named a child with its parent's name as a prefix. tmux `has-session -t`
accepted the remaining child after the parent was killed, causing a false
"session still exists" error. The same tmux fallback could affect later reads
or controls. Exact session/pane targets now prevent that substitution, including
browser terminal attachment. The initial fixture report and service error were
retained; the fixture script's separate incorrect inspection URL was corrected.
The two original fixtures were stopped and hidden after inspection.

Older pre-capability sessions can have a session ID/token but no reporting URL.
They now fail closed instead of opening a local writer. To use the existing
capability without restarting, set `AGENT_CONSOLE_REPORTING_URL` to the configured
Console URL for that command. New launches receive it automatically. The parent
completion check used this explicit configured URL; no marker was removed and
no local writer fallback was used.

Final exact-target regression run: **241 passed, 28 subtests passed** in 98
seconds across real tmux targeting, core, authenticated web, session control,
inspection, parent-cycle rejection, child capacity and version checks. Log:
`/tmp/console-exact-target-final.log`. It verifies both missing-target rejection
and normal create/restart/rename/scroll behavior. The existing Starlette/httpx
warning is unchanged.
