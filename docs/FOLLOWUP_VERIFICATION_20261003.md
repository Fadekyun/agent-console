# Follow-up verification — 2026-10-03, issue #141

Baseline: deployed v0.27.1 (`d9f4dc48487e`). Candidate: v0.28.0. The user approved the scout recommendations and requested independent verifier confirmation before continuing.

## Confirmed findings and bounded fixes

An independent verifier reproduced missing-reporting-URL fallback through fake workflow manager/session reader constructors, the actual installed Pi wrapper overwriting synthetic project values, its normalizer re-enabling a disabled synthetic MCP entry, and historical child failure overriding a ready current assignment. No real credentials or runtime databases were used in these probes.

- Shared managed-context checks cover managed workflow commands, routed session controls, peer session inspection and the existing owner project/environment/workflow-management guards. URL, session ID, capability, legacy name and context-file markers are recognized; incomplete managed context cannot silently become an owner reader/writer. Full managed context keeps authenticated API transport; genuinely unmarked owners retain existing local behavior. Recovery uses the configured URL and existing identity/capability or an explicit restart, never removing markers.
- The shipped Pi wrapper preserves Console's resolved environment and native MCP bytes in managed launches. It does not source host defaults or run the legacy normalizer. Empty/suppressed values remain empty/absent, selected skills and explicit themes remain intact, and a readable theme default is supplied only when unset. Standalone compatibility remains separate. The actual installed custom wrapper must be replaced with a reviewed, drift-checked, backed-up copy; selecting Console source alone does not do this.
- Selected child waits resolve repeatable `--child` names/IDs to a stable direct-child batch. Unrelated historical failures are excluded only when a batch is explicitly selected. Unknown, outside-parent, ambiguous, disappeared or reparented selections fail. Timeout, intervention, stopped-without-completion and success remain distinct. Existing unscoped whole-tree semantics and attention states are unchanged.

This is not a universal CLI owner-isolation or OS-account security claim. Other administrative command families retain their existing access model and need a separate policy audit before making that broader claim. Read-only filesystem restrictions are not bypassed by these fixes.

## Verification and rollout

The verifier independently reviewed the scoped-wait and Pi implementations. Additional tests cover selected-running timeout, blocked/needs-input outcomes, legacy markers, exact native MCP-byte preservation, invalid settings, no CLI fallback, normal owner commands and managed API routing. The final integrated test counts and runtime evidence are recorded in the parent workspace's `handoffs/console-followup-20261003/`.

Deployment selects an exact clean candidate after private canary verification. Configuration, service units, runner and databases are backed up, and durable session identities must be preserved. The Pi wrapper has a separate exact-preimage check and backup; actual installed-wrapper tests use temporary HOME, dummy credential files and an inert Pi executable. Running user harnesses are not restarted. Canonical guide updates use content hashes, backup copies, content-bound review and verified fresh delivery.

Physical iOS/Android keyboard, selection-handle and orientation checks require those devices. Browser viewport tests are not represented as physical-device evidence. Source publication/remote CI is distinct from a healthy local rollout, and no main-branch merge is implied by preparing or publishing a review branch.

## Final local verification

The final integrated run passed **315 tests and 825 subtests** in 156 seconds:
managed contexts, selected-child waits, actual Pi wrapper, native skill delivery,
session control/inspection, owner/workflow CLI, workflow engine, core, web,
environment and version checks. Only the existing Starlette/httpx test-client
deprecation warning remains. Log: `/tmp/console-v028-integrated.log`.

Independent verifier result: **GO for the three bounded fixes**, with 9 managed
context tests/607 subtests and 18 scoped-wait/Pi tests/20 subtests independently
passed. Reviewer-requested legacy marker and timeout/intervention cases are in
the final candidate. The initial integrated command used a nonexistent test
filename; it ran no tests and was corrected before this successful run.
