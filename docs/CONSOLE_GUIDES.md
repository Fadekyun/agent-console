# Console operating guides

v0.21.0 adds live tree awareness (`session relatives --current`, relative output reads) without mandatory result publication or child waits. Existing sessions can use the commands immediately; new launches receive the instructions. Routine work remains simple: manual child sessions are direct, scheduled work is optional, and recipe/continuation launch validation runs without a second approval screen. Workbench, Coding and Coordination reuse existing authorization and do not prescribe a review chain. New guide revisions apply to future sessions or explicit restarts.

The seven guides are maintained in the canonical skills workspace on n100, `/home/fadekyun/codex/skills`, and mirrored into staging's `/home/agentpreview/codex/skills`. Edit the canonical packages, not generated session copies. These packages use portable YAML with namespaced Console metadata; they are not a new plugin system.

| Package | Revision | Staging selection |
| --- | --- | --- |
| agent-console-workbench | 2026-10-02.7 | Shared session basics, navigation, configuration and continuation |
| agent-console-results | 2026-10-02.4 | Shared ready/final results, selected artifacts, delivery and consumption |
| agent-console-coding | 2026-10-02.3 | coder, bugfix |
| agent-console-coordination | 2026-10-02.3 | orchestrator, planner |
| agent-console-verification | 2026-10-02.1 | reviewer, verifier |
| agent-console-release | 2026-10-02.1 | release |
| agent-console-skills | 2026-10-02.3 | orchestrator; General remains compatible for an explicit assignment |

Role compatibility is checked before launch. A guide does not expand the profile's permissions. For example, coordination guidance lets a planner propose useful steps without authorizing implementation. Existing unrelated infrastructure `agent-console-ops` guidance is preserved.

The initial General assignment of the maintenance guide was removed after live verification showed it blocked plain Shell sessions, which do not isolate agent skills. A fresh Shell/General session now launches normally; the guide remains assigned to Orchestrator. Existing session copies are unchanged, and the earlier General delivery proof remains historical evidence.

## Connected behavior

The guides describe the implemented controls and commands. Small tasks can finish in one session. Optional next steps carry a reason, output and dependency; suggestion-only is the default. Automatic expansion is bounded by a reviewed operator envelope. Selected immutable inputs and explicit consumption bind check evidence to the actual candidate. Release authorization is a separate exact candidate/action/target operation with observed outcome reconciliation.

Interactive sessions using durable workflow handoffs publish through `agentctl workflow`; supervised native attempts return the runner's structured final and do not duplicate publication. Continuation preserves the workspace and an attributed result, starts a new conversation, and discloses unknown native defaults. Skills guidance covers local and bounded anonymous HTTPS Git staging, inspected activation and fetched-versus-declared provenance.

## Verification — 2026-10-02

All four new packages pass the skill-creator validator and registry validation. Eight role assignments pass effective Codex Pro previews. Three temporary actual staging sessions (coder, orchestrator, reviewer) received exact copied bytes of all four new packages, with durable delivery receipts; only those fixtures were stopped. Workbench, Results and Release had already been verified in actual session copies. Delivery is proof of availability, not model use.

The actual two-session consumed-snapshot handoff is recorded in [native dispatch verification](WORKFLOW_DISPATCH_VERIFICATION.md): the source file was removed after publication, the receiving Codex Pro attempt read the preserved selected artifact and published one passing final across a web restart. This protocol evidence and guide-copy evidence are distinct; no claim is made that all seven guides were invoked by a model in that fixture.

Historical initial package SHA-256 values (superseded for Coding and Coordination by the tree-awareness rollout):

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

## Capability-session guide revision — issue #141

The seven current mirrored packages were read against the new implementation.
Their existing single-session default, untrusted peer-output treatment, immutable
handoffs, and separate release authorization remain valid. Two instructions need
correction: Verification currently tells every reviewer to open a workflow inbox,
and Skills still treats a native harness's extra skill sources as an assignment
isolation limitation. Workbench and Coordination need the distinction between an
operator's Add-session link and agent-originated delegated authority.

Apply the following **pending canonical revisions** on n100 after the new service
is selected. They are update instructions, not a claim that host packages were
modified by the code change. Use the canonical source, preserve operator changes,
and bump each package's `agent-console/revision` to `2026-10-03.1` after applying
its relevant update. Validate all seven packages, refresh content-bound reviews
where applicable, mirror the reviewed packages through the normal sync process,
and verify new-session delivery. Do not rewrite active session snapshots.

| Package | Exact content to add or replace |
| --- | --- |
| Workbench | Add under tree awareness: “Every role can inspect bounded peers in its project through the session capability endpoint. Unassigned sessions inspect their own tree. If tmux observation fails, live status is unknown; stored metadata and saved transcripts remain readable. `agentctl session attention --current --state ready_for_review` reports completion without requiring database writes. A stopped or unobserved process is not proof of success.” |
| Results | Add under Reporting and recovery: “Session inspection, own attention and authorized descendant control also use the session capability. This does not grant environment management or release execution. Workflow publication remains optional for ordinary work. Never use direct database writes to recover a failed reporting request.” |
| Coding | Add: “When separate implementation is useful and authorized, use `agentctl delegate coder --parent "$AGENT_CONSOLE_SESSION_ID" --task 'Bounded task'`. Coding delegates receive an isolated worktree and inherit the project/repository. Read-only verification can use the verifier role. Wait only for delegated work required by the current task and examine the result before relying on it.” |
| Coordination | Add: “Human Add session is an operator action and can place a writable child under a read-only parent without changing that parent's permissions. Agent-originated delegation is different: read-only or Plan sessions may delegate only read-only work, including verifier. Writable sessions may manage descendants within existing authorization. Existing child/global capacity limits and an eight-level delegation depth limit apply.” |
| Verification | Replace the opening sentence of Bind the evidence to the candidate with: “For a direct review, inspect the supplied commit, diff or worktree and report findings in the session; no workflow inbox or publication is required. Only when the task supplies durable workflow inputs, run `agentctl workflow inbox --current` and inspect the source-attributed result and immutable selected artifacts.” Scope the following acknowledgment and publication instructions to that durable workflow case. Add: “Report your own completion with `agentctl session attention --current --state ready_for_review`; this is allowed for read-only roles.” |
| Release | Add: “Preparing a candidate, inspecting checks and local verification use existing task authorization; do not add separate permission prompts for each routine step. Push, merge, deployment and release still require the explicit action/candidate/target authorization described below. The session-control capability cannot grant or run release actions.” |
| Skills | Replace the final sentence of the policy-preview paragraph with: “Claude added-directory skills, OpenCode configured paths and Hermes external directories support Console-selected snapshots; their other native discovery sources can remain active. Use delivery capability and version diagnostics, not suppression of all native sources, to decide assignment compatibility. Unsupported or unverified adapters must still report their actual limitation.” Add: “The service preview reports affected running sessions and restart requirements. Profile listings include a bundled diff for customized installed roles; review it without overwriting local instructions. `scripts/probe-skill-delivery.py` runs credential-free OpenCode and Hermes discovery probes in disposable homes; a successful probe is availability evidence, not model use.” |

The applicable API and CLI behavior, portable descriptor checks, native-probe
limits, and code rollback are documented in [Session capabilities](SESSION_CAPABILITIES.md).
Canonical guide changes are independently reversible by restoring their reviewed
previous package versions and refreshing future assignments; a code rollback
must not silently rewrite the library or old delivery receipts.
