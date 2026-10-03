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

CLI parity: in a managed agent session, `agentctl session create` and `agentctl
delegate` both use capability-authenticated delegation and enforce parent role,
project, repository, worktree, depth and capacity rules. Operator CLI `delegate`
uses the same admission rules. Operator CLI `session create` has no parent flag
corresponding to the UI's human Add session endpoint; that UI-only operator action
may choose a writable child of a read-only parent without changing its authority.
This is a surface gap, not permission for an agent to bypass delegation restrictions.

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

Parity is bounded to projects and environment. Workflow owner policy/review,
connections and release operator actions remain web controls; existing CLI
workflow proposal, result publication, routing, acknowledgement and inspection
commands retain their current capability-scoped behavior.
Manual child creation from the local operator session CLI remains a separate
parity gap; managed delegation and the web Add session control support children.
