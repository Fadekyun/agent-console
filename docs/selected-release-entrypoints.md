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
