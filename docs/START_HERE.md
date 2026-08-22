# START HERE — Agent Console Development

This is the mandatory entry point for any agent or developer beginning work in this repository.

**Do not start implementation from chat history, an old prompt, a roadmap bullet, or an issue title alone. Verify the current repository and GitHub state first.**

## Required reading order

Before inspecting implementation details or writing code, read in this order:

1. [`AGENTS.md`](../AGENTS.md) — governance, PR rules, approval boundaries, versioning, and worktree requirements.
2. This file — startup protocol and current development context.
3. [`docs/DEVELOPMENT_ROADMAP.md`](DEVELOPMENT_ROADMAP.md) — milestone-level repository state.
4. The relevant master epic and its **latest comments/corrections**.
5. The exact GitHub issue selected for work, including its latest comments.
6. Search for an existing PR/branch/worktree for that issue before creating another one.
7. Read any architecture, runbook, migration, provider, or acceptance documents named by the issue.

GitHub issue/PR state is more current than a stale local planning note. If the roadmap, issue tracker, and code disagree, stop implementation and reconcile the discrepancy in the issue rather than guessing.

## Current major development phase

The current major architecture plan is:

- **#72 — Unified inference gateway, cache-aware usage intelligence, and provider routing**

Read #72 before working on any inference-provider, LiteLLM, model-routing, model-catalogue, usage/cost, quota, or related harness work. Read its latest comments as well: the epic contains a correction that **#73**, not historical #32, is the acceptance gate for this phase.

### Current implementation sequence

Unless the current tracker/dependency state has been deliberately changed and documented, use this sequence:

1. **#58** — reconcile roadmap/current repository state.
2. **#60** — CI required checks and branch-protection baseline.
3. **#61** — ordered/idempotent SQLite migrations.
4. **#62** — separate harness, inference provider, account, and model-route architecture.
5. **#64** — verified account adapters and normalized model catalogues.
6. **#63** — separate private LiteLLM gateway service.
7. **#65** — OpenCode native + gateway dual-mode reference integration.
8. **#67** — per-request usage/cache/cost/route telemetry.
9. **#68** — inference/usage UI.
10. **#70** — provider allowances, rolling windows, and operator budgets.
11. **#69** — sticky/cache-aware route resolution and explicit failover. The resolver may begin before #70 if it does not depend on budget pressure.
12. **#66** — remaining harness capability implementations after OpenCode establishes the reference path.
13. **#71** — provider/gateway operations, secrets, audit, retention, and recovery hardening.
14. **#73** — integrated staging acceptance for the exact candidate.
15. Explicit human production-promotion decision.
16. Revisit **#49–#52** operator-intelligence/evolution work on top of the stable inference/telemetry substrate.

Do not treat this numbered list as permission to implement an issue whose dependencies or review state are not ready. Verify GitHub first.

## Historical issue warning

**#32 is closed historical release/staging precedent. Do not reopen or repurpose it as the acceptance gate for the current inference phase. #73 is the current future staging-acceptance boundary for #72.**

## Before writing code

A working agent must establish and, when useful, state the following:

```text
Target issue: #NN
Issue status: open / closed / blocked / pending review
Existing PR: none / #NN
Existing branch/worktree: none / <name/path>
Dependencies: satisfied / blocked by #NN
Required documents read: <list>
Planned scope: <bounded summary>
Explicit non-goals: <what this issue does not change>
Deployment state: not deployed / staging-only / other verified state
```

If there is already an implementation PR or worktree for the issue, inspect it before creating parallel work. Do not duplicate work simply because the current agent session did not create it.

## Issue-first rule

Every implementation change begins from one GitHub issue.

- One implementation issue per PR.
- Use an isolated worktree/branch.
- Do not commit directly to `main` or a shared branch.
- Do not broaden scope silently. New material scope becomes a separate issue or is explicitly reviewed before inclusion.
- A roadmap entry is not itself authorization to implement.
- An agent saying work is finished is not acceptance.

## Current architecture rules that new work must preserve

For work under #72:

- Agent Console is the control plane; LiteLLM is an optional separate inference gateway.
- Harness selection and inference account/model selection are separate concepts.
- OpenCode native Go/Zen remains supported while gateway mode is added.
- Direct operator model/route selection has priority; do not build an opaque LLM router.
- A running/warm session is sticky to its exact account/model route by default to preserve remote cache value.
- Cross-account/provider/model migration is explicit and must warn that cache may be cold/lost.
- Fresh input, cache reads, cache writes, output, reasoning, actual cost, and estimated cost are separate telemetry concepts.
- Unknown provider metadata remains unknown; do not manufacture zero cost, unlimited capacity, or authoritative quota state.
- Provider plan names/marketing allowances are data, not application constants. API entitlement is verified by capability, not inferred from a label such as a legacy plan name.
- Raw upstream credentials do not belong in SQLite, browser responses, prompts, telemetry, catalogue caches, audit payloads, or logs.
- No automatic credit purchase/top-up or silent spend-changing behavior.

Read #72 and the exact child issue for the complete requirements; this section is only a guardrail summary.

## PR and release workflow

Follow `AGENTS.md`. The intended lifecycle is:

```text
GitHub issue
  -> isolated worktree/branch
  -> implementation
  -> focused tests
  -> full relevant local tests
  -> PR linked to exactly one issue
  -> required CI
  -> review against exact head SHA
  -> human approval
  -> merge to protected main
  -> immutable candidate/staging
  -> integrated acceptance when applicable
  -> separate explicit production approval
```

**Merge is not deployment approval. CI success is not deployment approval. Staging acceptance is not automatically production promotion.**

## End-of-session handoff

Every meaningful development session should leave enough durable state for another agent to continue without relying on chat history. Add/update an issue or PR comment with:

```text
Current issue:
Branch/worktree:
Current HEAD SHA:
What changed:
Tests run + exact result:
Open blockers/review findings:
Next concrete action:
Roadmap impact if scope/state changed:
Deployment state (normally: not deployed):
```

If nothing was changed, say so. If tests were not run, say so. If production was not touched, say so explicitly.

## When documents disagree

Use this precedence for operational decisions:

1. Explicit current human instruction.
2. Current repository governance/security rules in `AGENTS.md`.
3. Current GitHub issue/PR state and latest comments.
4. Current code and tests.
5. `docs/DEVELOPMENT_ROADMAP.md` milestone summary.
6. Older planning notes/chat/history.

Do not silently choose whichever source allows implementation to proceed fastest. Surface the conflict and keep the narrower/safe boundary until it is resolved.

## What success looks like

A fresh agent should be able to enter this repository, read `AGENTS.md` and this file, inspect the tracker, select the correct reviewed issue, understand its dependencies and non-goals, find any existing implementation, work only inside an isolated branch/worktree, and leave a durable handoff for the next session.
