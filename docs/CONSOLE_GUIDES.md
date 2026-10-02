# Console operating guides

v0.20.2 simplifies routine work: manual child sessions are direct, scheduled work is optional, and recipe/continuation launch validation runs without a second approval screen. Workbench, Coding and Coordination reuse existing authorization and do not prescribe a review chain. New guide revisions apply to future sessions or explicit restarts.

The seven guides are maintained in the canonical skills workspace on n100, `/home/fadekyun/codex/skills`, and mirrored into staging's `/home/agentpreview/codex/skills`. Edit the canonical packages, not generated session copies. These packages use portable YAML with namespaced Console metadata; they are not a new plugin system.

| Package | Revision | Staging selection |
| --- | --- | --- |
| agent-console-workbench | 2026-10-02.6 | Shared session basics, navigation, configuration and continuation |
| agent-console-results | 2026-10-02.3 | Shared ready/final results, selected artifacts, delivery and consumption |
| agent-console-coding | 2026-10-02.2 | coder, bugfix |
| agent-console-coordination | 2026-10-02.2 | orchestrator, planner |
| agent-console-verification | 2026-10-02.1 | reviewer, verifier |
| agent-console-release | 2026-10-02.1 | release |
| agent-console-skills | 2026-10-02.3 | orchestrator; General remains compatible for an explicit assignment |

Role compatibility is checked before launch. A guide does not expand the profile's permissions. For example, coordination guidance lets a planner propose useful steps without authorizing implementation. Existing unrelated infrastructure `agent-console-ops` guidance is preserved.

The initial General assignment of the maintenance guide was removed after live verification showed it blocked plain Shell sessions, which do not isolate agent skills. A fresh Shell/General session now launches normally; the guide remains assigned to Orchestrator. Existing session copies are unchanged, and the earlier General delivery proof remains historical evidence.

## Connected behavior

The guides describe the implemented controls and commands. Small tasks can finish in one session. Optional next steps carry a reason, output and dependency; suggestion-only is the default. Automatic expansion is bounded by a reviewed operator envelope. Selected immutable inputs and explicit consumption bind check evidence to the actual candidate. Release authorization is a separate exact candidate/action/target operation with observed outcome reconciliation.

Interactive sessions publish through `agentctl workflow`; supervised native attempts return the runner's structured final and do not duplicate publication. Continuation preserves the workspace and an attributed result, starts a new conversation, and discloses unknown native defaults. Skills guidance covers local and bounded anonymous HTTPS Git staging, inspected activation and fetched-versus-declared provenance.

## Verification — 2026-10-02

All four new packages pass the skill-creator validator and registry validation. Eight role assignments pass effective Codex Pro previews. Three temporary actual staging sessions (coder, orchestrator, reviewer) received exact copied bytes of all four new packages, with durable delivery receipts; only those fixtures were stopped. Workbench, Results and Release had already been verified in actual session copies. Delivery is proof of availability, not model use.

The actual two-session consumed-snapshot handoff is recorded in [native dispatch verification](WORKFLOW_DISPATCH_VERIFICATION.md): the source file was removed after publication, the receiving Codex Pro attempt read the preserved selected artifact and published one passing final across a web restart. This protocol evidence and guide-copy evidence are distinct; no claim is made that all seven guides were invoked by a model in that fixture.

The four new file SHA-256 values, checked on canonical installation and on actual staging delivery:

| Package suffix | SKILL.md SHA-256 |
| --- | --- |
| coding | aae32a0c94cbd3211672e4ced8311a35ee169a3e3113c1446fba97ade22c3c47 |
| coordination | 2c6ef117659c3f80f6a26ecef470c4403e4a64c3177bf1f5e345cafcc8878f62 |
| verification | e1dd99cd019ce3d8b1d9341841ec1b525bd8c1b0c2eec5cf266be8c1b1db35e1 |
| skills (.3) | 1fb7067118ec210d771289d305ad62b873151f92c48aca625feac5bec28a05bd |

The package hash also includes supporting files and permissions and differs from the entrypoint file hash. Installation preserves an existing package unless it exactly matches the supplied entrypoint. Local evidence is under `handoffs/console-guides-20261002` in the implementing workspace.

## Rollback and remaining work

Unassign a new staging guide to remove it from future role sessions; existing session snapshots and delivery histories remain intact. Remove a newly installed canonical/mirror package only after checking that it is still the exact owned version and is no longer assigned. Current-console mirror and assignments were not changed. Selecting an older staging source does not undo canonical package or assignment changes.

The complete cross-harness discovery/version matrix and remaining Workbench acceptance checks are tracked in [the full audit](FULL_REVAMP_ACCEPTANCE.md). Guide completion does not close those requirements. Git import behavior added in 0.19 is documented in [Git imports](SKILL_GIT_IMPORTS.md).

All seven current packages were also discovered by actual native Codex, Claude, OpenCode, Pi and Hermes readers in clean fixture homes; see [the native matrix](NATIVE_SKILL_MATRIX.md). The maintenance guide now documents package validation, profile validation, unassignment and the per-harness inspection control. No model invocation is inferred from discovery.
