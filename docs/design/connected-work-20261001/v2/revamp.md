# Unified Agent Console revamp · B, revision 2

The operator selected concept B and requested simpler mobile navigation, a tree that scales with task complexity, manual **Add session**, reliable nested terminal scrolling, and integrated skills/function cleanup. This revision brings those into one delivery program under #90/#80. It supersedes the fixed four-step example as the default UX; the four-step chain remains an optional workflow example.

This is a revised design and implementation contract, not a runtime release. Desktop B is accepted in direction; the phone revision and added interactions remain reviewable before their application implementation.

## One session first

Start a task in one suitably authorized session. A coder may investigate, implement and run relevant checks in that session. A small issue does not need separate planning, review and release agents solely to satisfy a pipeline template. Existing repository-required checks and permissions still apply.

| Task | Reasonable shape | Completion |
|---|---|---|
| Copy fix, small isolated bug | One coder | Make change, run relevant checks, publish final result |
| Feature with separable frontend/backend work | Initial session proposes two implementation branches and a join if useful | Verify the combined result; no reviewer-of-reviewer chain |
| Data migration or broad behavioral change | Plan → implementation + targeted verification | Exact candidate evidence, rollback/recovery validation |
| Release | Explicit release action if within the authorized scope | Verify target and outcome; preserve commit-matched evidence |

Complexity is a reason to suggest help, not an arbitrary session-count quota. Each suggestion names its job, why a separate context helps, expected output and prerequisites. The system should also be able to say **“No extra session needed.”** Stop adding work when the acceptance criteria are met.

A read-only planner remains read-only. If it needs implementation, propose or attach a coder rather than silently changing the original session's permissions.

### Suggestions or bounded automatic growth

Revision 2 defaults to **suggestions only** while the operator's expansion-mode preference remains open. Session 1 can propose follow-ups; the operator accepts, edits, removes or manually adds them. Accepting a set authorizes its already-listed actions; downstream dispatch remains automatic when dependencies are ready.

An optional **Auto within limits** task policy permits the coordinator to add useful sessions without another prompt, but only inside a scope reviewed at Start: task, repositories, action classes, allowed roles/harnesses, targets and budget. That is an explicit delegation envelope, not an unlimited right to change the task. Each expansion is versioned and visible with a reason. New repositories, permissions, release targets or a larger budget require a reviewed change.

Initial proposed defaults for this optional mode: 2 concurrent workflow sessions, 4 total sessions, maximum descendant depth 2, and 3 recomputations per session within global capacity. These are task settings, not compulsory overhead. The original 2-concurrent/3-rerun defaults are preserved; total/depth limits are newly proposed. At a limit, explain why further work is waiting. Do not silently increase the budget or create recursive reviewer chains.

Auto mode is a design option only in this prototype. It does not start real or simulated agents by itself.

### Add session below

Every node exposes **Add session below**, including completed and already-connected sessions. Ask only for:

1. A useful task/purpose.
2. A suitable role (suggested, editable within permitted capabilities).
3. Readiness: after the parent's final result, after an explicit ready checkpoint, or alongside using current snapshots.

Inherit the project and approved policy, select the parent's relevant results/files, and show effective skills before dispatch. Allow connecting an existing session by durable ID without restart. Unsupported auto input shows setup required rather than failing after launch.

The **parent tree is ownership**, while **input dependencies are a graph**. Adding a child does not automatically make it depend on every sibling. Multiple required inputs form a join; reject cycles. Show additional dependencies in Details without turning the everyday screen into a graph editor.

Keep #140's versioned artifacts, durable inbox and acknowledgment, stale propagation, coalescing, exact-commit review and external-operation reconciliation. A simpler screen must not remove those behaviors.

## Phone: one screen at a time

| Screen | Visible by default | Open on demand |
|---|---|---|
| Work | A small attention prompt and task cards | New session; Settings |
| Session | Short status, useful next action, **Open terminal**, compact session list, **Add session** | Details, inputs/results, branch controls, pause/stop |
| Terminal | Back, connection/session identity, output, Scroll/Select/Type, composer | Latest output when scrolled away; details |
| Settings | Skills, agent defaults and readiness | Source/revision, delivery explanation, diagnostics |

No permanent terminal under the phone overview. No five inspector tabs above another scrollable terminal. No four mandatory stage cards. No second fixed toolbar over the phone keyboard. Work and Settings are the only top-level phone destinations; Session and Terminal are navigated views.

Use a shared view/state model across desktop and phone; responsive composition changes the arrangement, not session identity or actions. The same session, draft and output position survive switching views. Desktop keeps B's overview and can embed or expand a terminal inside a task workspace.

## Terminal repair is a first delivery slice

Source inspection found existing xterm follow-output, resize, reconnect, touch-mode and clipboard code. In the reviewed base, reconnect always re-enables follow and scrolls to bottom; touch scrolling also applies a fixed six-line step at touch end, or emits page keys for alternate buffers. Validate these interactions against the reported nested-scroll failures rather than adding another independent scroll handler.

Use one terminal controller/component for embedded, expanded and mobile views:

- One scroll owner for the terminal output. Flex/grid ancestors use `min-height: 0`; page scrolling stays outside the terminal. Contain overscroll so reaching the terminal boundary does not unexpectedly move its parent window.
- Track whether the operator is following output. Incoming data must not move an operator reading history; offer **Latest output**. Preserve a meaningful buffer anchor through resize, split changes and reconnect where retained history permits.
- Avoid blanket wheel interception and duplicate native/custom touch movement. Define and test normal scrollback versus alternate-screen applications independently; scrolling must not accidentally type into a shell or send application keys in the wrong mode.
- Mobile gets a dedicated viewport with the visual keyboard height and safe areas accounted for. Composer, Back and input-mode controls stay reachable. Tapping output does not open the keyboard.
- Preserve focus, selection and draft across refresh/navigation. Make paste reviewable when needed; retain clipboard-denied/insecure-context fallbacks. Ctrl+C semantics distinguish copying a selection from interrupting a process.
- Expanding, hiding, switching tabs or changing device width must not restart the session or create duplicate sockets/listeners. Unmount/reconnect must clean up owned observers and handlers.

**Required real terminal matrix:** embedded and expanded xterm; desktop wheel/trackpad; phone drag/swipe; normal and alternate buffers; new output while reading old lines; selection/copy/paste; denied clipboard; resized dock; virtual keyboard; reconnect; orientation change; multiple terminals; empty/failed/long outputs. Verify in real mobile browsers before claiming a scrolling fix. Revision 2 tests a native scroll fixture only, not production xterm.

## Skills, profiles and context use the same session contract

Retain the existing canonical library, assignment engine, shared allowlist and capability table. Reconcile #81/#131 with actual shipped code before scheduling work; an open issue is not proof its feature is absent. Close/report stale issue state only after verification.

The new UI must explain **effective access for this session**, not merely list installed skills:

- skill name and purpose;
- source/provenance and selected content revision/hash;
- why it was selected (shared, profile, project, explicit task);
- role/harness compatibility and actual materialization/delivery status;
- ready, available but not selected, needs setup/update, or blocked, with an actionable reason;
- which changes affect new sessions versus require an explicit refresh of an existing session.

Skills, tools and permissions are separate concepts. A skill is guidance; it does not grant tools, secret access or release authority. Avoid claiming isolation for a harness whose discovery path can still load broader shared content. The UI should expose that limitation before start.

### Operating-guide rewrite scope

Audit and rewrite Console-related guidance in the authoritative canonical skills workspace, not in generated per-session copies. Keep domain runbooks outside this rewrite unless their actual Console interface has changed. No live skill files were modified by this design revision.

| Guide/bundle | Required content after the rewrite |
|---|---|
| Session basics | Read durable context, role and task; identify exact inputs; stay in scope; return useful progress/final status; recovery by durable ID |
| Bounded coding | One-session path for small tasks; isolated changes, relevant checks, final result; suggest another session only for a distinct justified job |
| Coordination | Suggest versus auto-envelope policy, add/attach/dependency rules, useful parallelism, limits, pause/stop, no automatic review pyramids |
| Results and handoffs | Ready versus final, selected artifacts and revisions, source-attributed inbox, delivery/consumption, stale work and reruns |
| Review/verification | Review the exact artifact and risk; actionable evidence; no recursive review merely because the previous agent was a reviewer |
| Release | Match authorized action/target and reviewed candidate; verify external outcome; reconcile uncertainty before retry |
| Skills maintenance | Inspect canonical source and compatibility, validate content revision, preview effective delivery, preserve user changes and rollback |

Keep each guide short and role-specific; link shared reference material instead of repeating a giant global prompt. Document real implemented commands, not imaginary APIs from a proposal. Pin or record what a session received; a changed file must not silently alter the contract of an already-running session. Move old instructions through a documented migration and retain rollback copies. Test both content correctness and real harness discovery, including Pi/Hermes parity and MCP configuration where applicable.

## One program, existing owners

#90 is the design/integration entry point under #80. Add cross-links rather than another parallel epic.

| Delivery slice | Existing owners | Concrete integration result |
|---|---|---|
| B shell and simplified mobile | #90; #31, #37, #54, #56, #57, #114 | Work/Session/Terminal/Settings share state; reduce visible controls and duplicate flows |
| Terminal behavior | #44, #45, #56, #114 | One tested controller for embedded/expanded/mobile; real scroll, clipboard and focus gates |
| Adaptive session trees | #140 + #86 | One-session fast path, Add session, justified suggestions, bounded auto envelope |
| Durable inputs/results | #87 + #121 | One result store and inbox shared by UI, CLI and scheduler |
| Effective skills and rewritten guides | #6 + #81, #122, #131 | Clear task-scoped delivery, capability parity, short consistent operating guidance |
| Project context / launch setup | #88 + #82 + #85 | Small bootstrap context, optional recipes, effective-session explanation |
| Harness/MCP parity | #113 + #123 | Same selected tool/skill context reaches the actual harness; retire wrappers only after parity proof |
| Module cleanup / safe rollout | #120 + #127 | Extract along component boundaries; preserve host config, sessions and rollback |

Suggested implementation sequence after the revised UX review:

1. Audit real completion state and map old/new screens, controls and operating skills. Freeze acceptance scenarios; retain the September branch as source material.
2. Ship the shared B shell and terminal fixes first, using current sessions. Mobile simplification should not wait for a workflow engine.
3. Add the one-session completion path and manual Add/Attach using existing delegation, plus role/capability checks and effective skills. Expose a capability as pending until its backend works.
4. Implement durable results/inbox and the revised operating-guide delivery together; exercise a real two-session handoff.
5. Add suggestions, joins, recomputation and optional bounded auto growth; test recovery and limits before enabling unattended behavior.
6. Integrate release steps where explicitly authorized; remove superseded UI/CLI/skill paths only after parity tests and rollback evidence.

Each slice uses one bounded issue/PR and the repository's version, roadmap, release-note and relevant-test rules. Avoid a whole-app rewrite before a working vertical slice. The unified acceptance journey is: start one simple task → finish with its own checks; start larger work → accept/add one session → exchange a versioned result → review on phone → use the same terminal without scroll/focus loss → inspect exactly which skills it received.

## Design evidence and remaining gates

Revision 2 includes a desktop overview, adaptive tree, separate phone Work/Session/Terminal screens, effective Skills view, and a clickable Add session form. Tests cover fixture navigation/drafts, waiting dependencies, limits policy display, nested wheel/touch scrolling and follow-output behavior.

Still required: operator review of simplified phone and expansion policy; application implementation; real xterm/clipboard/mobile-browser regression tests; harness capability and skill-content validation; persistence/recovery/migration tests; production rollout approval. The prototype neither fixes the deployed app nor rewrites the live skills library.
