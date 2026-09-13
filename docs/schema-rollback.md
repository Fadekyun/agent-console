# Schema11 release and rollback contract

The combined release adds `sessions.execution_kind` (default interactive) and
private `integration_requests`. Native planning is disabled/unverified. A clean
HOME, OpenCode skill discovery or the guarded SQL reader is not native planning
containment evidence, and enabling its configuration is not part of this release.

Before deployment, record the exact candidate and current release, active session
inventory, configured database path and a consistent SQLite backup. Verify the
backup on an isolated copy. Do not naively copy a live WAL database, overwrite new
state with an old snapshot, or remove request artifacts/receipts/tombstones.

## Supported return to schema10

A schema10 binary can be selected only after the explicit compatibility check:

- planning configuration is absent or explicitly `enabled:false` (malformed,
  unreadable, symlinked or unknown configuration refuses the transition);
- schema metadata is known (10 or11); target schema is read as a literal from
  validated release source without executing it;
- the schema11 extension is structurally present and `integration_requests` has
  **zero rows**, including terminal receipts and tombstones;
- every retained session is interactive.

Under the shared admission lock and a database write transaction, the helper marks
schema metadata10 explicitly. It preserves all session rows and the additive
columns/tables, which remain compatible with ordinary schema10 inserts. Reopening
with schema11 restores metadata11 idempotently without losing those sessions.
This narrowly empty-feature compatibility rollback is not a general down-migration.
No table, receipt, tombstone, session or artifact is deleted. Config must remain
disabled throughout the authorized maintenance operation; don't concurrently edit
activation settings or invoke old release tooling outside this reviewed path.

Production manager release selections (including automatic fallback) use the same
check before changing the current link. The update helper checks compatibility
**before** restoring the old runtime/runner/release files. It backs up the actual
configured DB and uses the saved original release path, not a relative symlink
resolved from the backup directory. If the guard refuses, it leaves the newer
selected release and DB intact and reports failure; it does not restart an unsafe
older writer. Service health may remain impaired and requires operator recovery.

If request data or noninteractive sessions exist, the supported path is a reviewed
schema11-compatible forward fix/recovery release. Do not erase records merely to
make downgrade pass. A backup restore would require a separate maintenance plan
that accounts for every write since the snapshot; it is not automated here.

The new writer also rejects invalid/future schema metadata before migration DDL,
instead of silently stamping its own version. Historical binaries cannot be made
safe by a new source check alone: use the guarded selection/update operation, not
manual current-link replacement or the old updater.

## Verification boundaries

Fixtures must prove empty-feature11→10→11 round trips preserve ordinary sessions,
retained request/noninteractive records block downgrade without relabeling,
unknown/enabled config fails closed, target source isn't executed, future schema
is not overwritten, and failed selection/automatic fallback preserves the current
link. Exact combined validation and independent review are required before release.
No live schema change or release action is performed by reading this contract.
