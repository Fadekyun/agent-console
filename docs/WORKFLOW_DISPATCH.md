# Reviewed connected work

A small task can finish in its original session. Add session creates a proposed next step with a task, reason, expected output, launch configuration and required inputs. Open Results & handoffs → Next steps, inspect Preview launch, then accept, edit or reject. Acceptance authorizes that configuration when inputs and capacity are ready; it does not create mandatory review layers.

Suggestions-only is the default. A native agent may submit a justified suggestion through `agentctl workflow propose --current --task TEXT --reason TEXT --expected-output TEXT --tool codex-pro --profile planner --action read --request-key UNIQUE_KEY`. Its own final result is the default prerequisite; repeated `--input SESSION_ID:after-ready|after-final|alongside` options select other inputs. The narrow reporting capability can propose but cannot accept, enlarge policy, or control another session.

Workflow mode & limits supports explicitly reviewed automatic expansion. Review the full repository/action/role/harness/target scope plus concurrent-session, total-session, descendant-depth and per-step recomputation limits. Every launched attempt counts toward the total; original and attached sessions count too. Out-of-scope proposals remain suggestions with an explanation. Editing a step resets its approval; skill or adapter/configuration drift also requires a fresh preview. Existing running attempts retain their frozen authorization.

## Native execution

Only Codex and Codex Pro currently expose this task adapter. The installed executable is probed for version and required native flags before acceptance. Unsupported adapters report setup required before creating a terminal. Other harnesses remain available for ordinary interactive sessions; a connection alone does not inject task text into them.

Each attempt reserves its durable identity before native session creation. A supervised runner waits for a persisted prepared receipt, running policy and unchanged approval/inputs, then supplies the task and immutable input manifest on stdin once. Required selected artifacts are verified before their local snapshot paths are supplied. Read-only roles and read action classes use the read-only sandbox; other approved work uses workspace-write with unattended approvals disabled. Attempts have a 30-minute runtime limit. This reuses the native harness's sandbox and Console's trusted-host operator boundary; it is not a new OS-account security boundary.

The native final response contains `outcome`, `summary`, `checks`, selected `files`/`commit`, `consumed_inputs` and optional `suggestions`. The runner publishes it once and records explicit consumption claims. A passing final that omits a required input becomes blocked. Agents should not additionally publish a duplicate final with agentctl. Snapshots resolve from the session's actual isolated worktree. Optional follow-up proposals use the same policy and never create an automatic reviewer chain by default.

Ownership uses logical step IDs; a binding identifies the current native attempt. Required inputs use the separately versioned DAG. After-ready/final readiness, failed inputs, joins and stale results follow the existing result contract. When input content changes during execution, the current attempt keeps its frozen input. Once it finishes, one replacement takes the newest ready join, subject to remaining limits. Old attempts, selected inputs and results remain inspectable.

## Control and recovery

Pause blocks dispatch and holds prepared native startup; running work can finish. Stop interrupts the original/attached connected sessions and receipt-owned attempts, preserving files, transcripts and result history. Stopped workflows cannot resume; start new work. Completed attempt terminals are archived and retired to free capacity.

The web service serializes dispatcher sweeps across workers. Receipt states distinguish reserved, creating, prepared, starting, running, completed, failed, cancelled and unknown. Capacity waits reuse the reserved identity. A lost process/receipt is unknown, never presumed safe to retry. Use Resolve uncertain attempt only after observing the session and any affected systems; record not-started, pass, fail or blocked with evidence. Live or unobservable process groups prevent reconciliation. Recording an outcome does not itself retry; Retry when ready is a separate action. External deployment/release actions remain unsupported until the exact-candidate gate is implemented.

Back up the sessions database, `connected-work.sqlite3`, `result-objects/`, skill policy and `workflow-attempts/`. Dispatch schema 1 is additive and independently gated. Before rollback, pause and settle/stop active workflows. Runners are pinned to their launch source directory and survive a web restart, so preserve source releases and never assume selecting an older web release stops work. v0.13 ignores dispatch records; restoring v0.14 returns the review/recovery controls.
