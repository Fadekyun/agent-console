# Selected-release normal CLI entrypoints

The maintenance installer `scripts/install-entrypoints.py` owns only agentctl,
agent-selector, agent-console-status and agent-console-logs in HOME/bin and
HOME/.local/bin. All eight links target releases/current/scripts/<name>. A
missing, escaped, changed or invalid current release is unavailable; no package,
checkout or older-release search is permitted. Manual regular files are refused.

Validation opens directory components without following symlinks, checks full
manifest contents and pinned file identities, rejects FIFOs/hardlinks, and
rechecks source/alias directory identities before atomic link replacements.
Cooperating alias writers and Deployer current-link changes share entrypoints.lock.
This trusted-owner maintenance boundary is not protection against a privileged
process maliciously changing immutable release files after installation.

Receipts preserve old link metadata privately for diagnosis; they are not an
instruction to restore unsafe historical writers. A validation failure before
installation changes no alias. A late external filesystem race can fail after a
subset of links is updated: keep evidence and correct forward; do not automatically
restore legacy targets. No old source, manager, database or provider is executed
by normal alias installation.

First install explicitly prepares/selects an installer-owned release and initializes
only a new database. Existing DB without current fails closed for recovery. Upgrades
retain selected source until the normal guarded switch. Update rollback now uses
scripts/select-release.py and the same alias installer, never raw link copying or
restoration of old CLI targets. The current-state schema guard remains authoritative.

## Private context-sync patch

`ops/prepare-lxc115-sync-patch.py` renders only a new inert artifact, gated by the
exact observed authoritative helper SHA256. It changes the helper's fixed legacy
PATH component and alias function only. The replacement body is maintained as
ops/lxc115-entrypoint-delegate.py: fixed-home selected release containment and
installer/module manifest checks precede delegation. Validated installer and imported
package/module bytes are copied into a sealed anonymous memfd archive; Python runs
that archive under -I/-B, not the original paths. Write/grow/shrink/seal seals are
mandatory. The installer then checks the captured selection and implementation
hashes under its shared lock before the full validation/alias operation. No installer means failure, never old source.

The renderer cannot apply the patch, overwrite its input, invoke the helper, change
a timer, or restart a service. Review the exact generated diff and host ownership,
mode and source hash before a separately authorized application. Preserve unrelated
context/key/config/backup logic; do not invoke the full helper merely to test this
change. A recurring external caller has been observed, but its scheduler is not
identified. Installing two links alone does not fix that authoritative writer.

Rollout must patch the authoritative writer before repairing aliases once; during
the interval before a release containing the installer is selected, its alias step
must fail closed. Verify the next naturally occurring authorized invocation; do not
force a full sync. Never restore the unsafe helper/legacy aliases as a schema11
rollback. Direct manual execution of historical absolute paths remains unsupported.

This change does not make live read-only WAL snapshots available when SQLite
sidecars are missing. Correct CLI inspection can still return state-unavailable;
that diagnostic must never trigger a legacy writer or an unguarded read fallback.
The long-running service now holds one writer connection for its lifetime so the
live sidecars stay present during normal operation; the reader itself still
creates and changes nothing.

The generated stable web runner has no checkout/package fallback. Missing, broken,
escaped or incomplete current releases refuse startup; initial install prepares,
validates and selects a release before normal service startup. Required package
init/web files and terminal assets must exist as regular non-symlink leaf files.
A rejected startup never executes the checkout canary. Host delegate race fixtures
mutate each installer/package/module path after immutable capture (both replacement
and in-place writes), verify sealed bytes cannot be written, and require refusal
before alias changes without executing the substituted code. Selection changes
are also rejected. This Linux memfd boundary fails closed when sealing is absent;
no host package/policy change or fallback is attempted.


Normal Python CLI wrappers use Python 3.11 safe-path mode (`-P`) and put the selected
release first on `PYTHONPATH`. Calling an installed alias from an old checkout
cannot import that checkout's `agent_console` package. The caller's working
directory stays unchanged, preserving relative workspace defaults. The wrappers
also disable bytecode writes (`-B`). All eight installed aliases continue to
follow `releases/current`, with no old-wrapper fallback.

### Human child creation and managed delegation

In a managed agent session, `agentctl session create` and `agentctl delegate`
use capability-authenticated delegation and enforce parent role, project,
repository, worktree, depth and capacity rules. Operator CLI `delegate` retains
those delegation rules.

A local human owner can instead use `session create --parent NAME_OR_ID`, matching
the web Add session action. It accepts a managed interactive parent, inherits its
project/repository when omitted, and retains global capacity, configured positive
child limits and the eight-level depth guard. A human may choose a writable child
of a read-only parent without changing the parent's authority. Managed callers
cannot use this flag to bypass delegation checks; incomplete session reporting
credentials fail closed.

```sh
agentctl session create --parent PARENT_ID --tool codex-pro --profile coder \
  --worktree --task 'Implement the bounded approved change'
```

Run owner examples from a local human owner terminal using the selected release,
not inside a managed agent session. Keep the same configured workspace and state.

### Local owner projects and environment

`agentctl project list|show|create|update|delete|assign|unassign` uses the same
manager validation as the web interface. Examples:

```sh
agentctl project create 'Example' --repository /path/in/workspace --description 'Brief'
agentctl project show PROJECT_ID
agentctl project update PROJECT_ID --status paused
agentctl project assign PROJECT_ID SESSION_NAME
agentctl project unassign PROJECT_ID SESSION_NAME
agentctl project delete PROJECT_ID
```

`agentctl environment list|set|unset|enable|disable|suppress` manages global
settings by default; add `--project PROJECT_ID` for a project scope. `list` and
mutation output contain names, states, revisions and session refresh metadata,
never values. Set values through `--stdin` (exact UTF-8, including newlines,
maximum 32 KiB) or the hidden prompt in an interactive terminal. There is no
value argument or `--value` option. Avoid typing secrets into shell commands:
pipe an existing private file or use the prompt.

```sh
agentctl environment set API_KEY
agentctl environment set API_KEY --stdin < /path/to/private/value-file
agentctl environment list --project PROJECT_ID
agentctl environment disable API_KEY --project PROJECT_ID
agentctl environment enable API_KEY --project PROJECT_ID
agentctl environment suppress API_KEY --project PROJECT_ID
agentctl environment unset API_KEY --project PROJECT_ID
```

Disable retains the stored value and allows inherited settings to apply.
Project-only suppress removes the inherited variable and discards that project's
stored value; set a new value to enable it again. Unset deletes the scope entry
and restores inheritance. Reserved execution and Console variables remain
protected. Running sessions require an explicit restart to use changes.

These commands require a local human owner terminal. Managed agent sessions
explicitly reject them before opening a local writer; session capabilities do
not grant owner environment administration or project assignment. Existing
managed session tree, review, attention and delegation commands remain supported.
If the sandbox denies their network transport, retry with authorized network
access or escalation; there is no local database writer fallback.

### Local owner workflows, connections and releases

`agentctl workflow manage` calls the same services and request models as the web
owner controls. It requires a local human owner terminal; any managed-session
identity/reporting marker rejects it before reading the payload or constructing a
local manager, including inspect commands. Managed agents retain the separate
capability-scoped `workflow propose`, results, inbox and acknowledgment commands;
this does not grant them policy acceptance, release authority or a local writer.

Commands that take a request require exactly one of `--json-file PATH` or
`--stdin`. Supply one UTF-8 JSON object, at most 256 KiB. Do not pass JSON or
credentials in command arguments. Request validation errors omit submitted
values. IDs below stand for actual durable identities returned by inspection.

| Commands after `agentctl workflow manage` | Identity / request model |
| --- | --- |
| `inspect`, `propose`, `policy`, `control` | Root/session identity; respectively no body, `ProposalRequest`, `PolicyRequest`, `ControlRequest` |
| `step preview`, `step review`, `step edit`, `step retry` | Step ID; no body, `DecisionRequest`, `EditRequest`, no body |
| `attempt reconcile` | Attempt ID; `ReconcileRequest` |
| `connections inspect`, `connections attach`, `connections dependencies`, `connections deliver` | Session identity (parent for attach, target for dependencies/deliver); no body, `AttachRequest`, `DependenciesRequest`, `DeliverRequest` |
| `releases targets` | No identity or body |
| `releases evidence` | Candidate result ID; no body |
| `releases preview`, `releases authorize` | No positional identity; `Preview`, `Authorize` |
| `releases list`, `releases inspect`, `releases start` | Session identity, release ID, release ID; no body, no body, `Start` |

The authoritative JSON fields and constraints are defined in
[workflow dispatch schemas](../agent_console/workflow_dispatch_api.py),
[connection schemas](../agent_console/workflow_api.py), and
[release schemas](../agent_console/workflow_release_api.py).
`PolicyRequest.policy` must contain the complete envelope keys in
[`DEFAULT_POLICY` and `WorkflowEngine.configure`](../agent_console/workflow_engine.py),
including mode, repositories, actions, roles, harnesses, targets and all four
budgets; partial policy changes are rejected.

Inspect current state before preparing versioned changes. `expected_version`
must match the current policy, step or connection graph, not an old example.
Obtain the step's current launch `hash` from preview and supply it as
`preview_hash` when accepting. A rejected or changed request needs inspection
and a fresh review; do not silently overwrite newer state.

```sh
agentctl workflow manage inspect ROOT_ID
agentctl workflow manage propose ROOT_ID --json-file /path/to/proposal.json
agentctl workflow manage step preview STEP_ID
agentctl workflow manage step review STEP_ID --json-file /path/to/decision.json
agentctl workflow manage control ROOT_ID --stdin <<'JSON'
{"state":"paused"}
JSON
```

A proposal object contains `task`, `reason`, `expected_output`, `config`, optional
`dependencies` and a unique `request_key`. A decision object contains
`decision: "accepted"` or `"rejected"`, the inspected `expected_version`, and the
reviewed `preview_hash` for acceptance. Edit uses the full proposal fields except
`request_key`, plus `expected_version`. Use `control` with `running` to resume a
paused workflow; `stopped` interrupts/cancels owned work and cannot resume.

Attach an existing session without restarting it. For a new connection graph
only, `expected_version` is zero; otherwise use its inspected version. Each
optional dependency has `source_id` and `readiness` (`after-ready`, `after-final`
or `alongside`). Delivery uses the current graph version and ready-input signature
from connection inspection; delivery and consumption remain distinct.

```sh
agentctl workflow manage connections inspect PARENT_ID
agentctl workflow manage connections attach PARENT_ID --json-file /path/to/attach.json
agentctl workflow manage connections dependencies TARGET_ID --json-file /path/to/dependencies.json
agentctl workflow manage connections deliver TARGET_ID --json-file /path/to/delivery.json
```

An attach request, after substituting the actual session ID and inspected version:

```json
{"session_id":"EXISTING_SESSION_ID","purpose":"Targeted verification","dependencies":[],"expected_version":0}
```

Release operations still require explicit candidate/action/target authorization.
Preview binds the selected candidate result, current evidence result IDs, action
and configured target. Authorize repeats those fields with the preview's
`hash` as `expected_hash` and a unique `request_key`. It creates a grant; `start` separately
runs or probes it. Neither a new guide nor session capability authorizes release.

```sh
agentctl workflow manage releases targets
agentctl workflow manage releases evidence CANDIDATE_RESULT_ID
agentctl workflow manage releases preview --json-file /path/to/release-preview.json
agentctl workflow manage releases authorize --json-file /path/to/release-authorization.json
agentctl workflow manage releases inspect RELEASE_ID
agentctl workflow manage releases start RELEASE_ID --stdin <<'JSON'
{"mode":"probe","request_key":"observe-uncertain-release-1"}
JSON
```

Use `mode: "apply"` only for the already authorized action. An uncertain external
outcome requires observation/probing before retry, not another blind apply. Use
`attempt reconcile` with an observed outcome and summary for uncertain native
workflow attempts; it does not itself retry. See [release behavior](WORKFLOW_RELEASES.md)
and [dispatch recovery](WORKFLOW_DISPATCH.md). Durable request keys deduplicate
supported operations; reuse the same key for the same uncertain request and do
not invent a new key merely to bypass its receipt.
