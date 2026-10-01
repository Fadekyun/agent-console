# Versioned results and durable handoffs

Staging 0.12.0 connects Session → Results & handoffs with `agentctl workflow`. A session can publish a final result without creating review agents. Results distinguish explicit `ready` checkpoints from `final` reports and `pass`, `fail`, or `blocked` outcomes. Mechanical terminal state and attention remain independent.

A result contains a summary, actual check descriptions and selected file or exact commit artifacts. Selected files are copied through bounded no-follow reads. Commit selections preserve the selected tree as a compressed archive. Content hashes protect downloads against corruption; editing or removing the source cannot change a published snapshot. Snapshots reject possible credentials and never execute source scripts. File/archive size is limited to 2 MiB, expanded commit trees to 8 MiB/2,000 entries, selection count to 16 and result metadata to 256 KiB. Select individual files when an entire commit is too large.

Publishing allocates a durable per-session version in a transaction. Retry with the same request key to recover the same result; conflicting content is rejected. A new result version preserves earlier outputs. Handoffs reference an exact result ID and assign a monotonic per-recipient sequence. The inbox tracks queued, delivered and consumed states separately. A list read does not acknowledge delivery; explicit acknowledgment is required before consumption. Acknowledgments never move backward and survive session rename/stop and Console restart. All peer text is marked as untrusted task data.

The authenticated UI permits an operator to report and route results for managed sessions. Agent commands use the narrow `/api/agent-workflow` endpoint with the existing native session capability. A session can publish only as itself, send its own results and acknowledge only its own inbox. The endpoint permits no session creation, arbitrary commands or repository writes. Capability values are kept in native launcher environment, never response payloads. Reporting is not a new authority to edit, release, or impersonate an operator.

```sh
agentctl workflow publish --current --kind ready --outcome pass --summary 'Candidate ready for the bounded next task' --file candidate.txt --request-key checkpoint-1
agentctl workflow results "$AGENT_CONSOLE_SESSION_ID"
agentctl workflow send RESULT_ID --to RECIPIENT_SESSION_ID --request-key handoff-1
agentctl workflow inbox --current
agentctl workflow ack INPUT_ID --current --state delivered
agentctl workflow ack INPUT_ID --current --state consumed
```

Results and inbox are paged five at a time. Use `results SESSION --before VERSION` and `inbox --current --after SEQUENCE`; the UI offers older-results/more-inputs actions. Native launchers export `AGENT_CONSOLE_REPORTING_URL` from the configured Console bind host/port. Reporting needs connectivity to that endpoint. Older running sessions adopt that environment on explicit restart; their original task/permissions remain unchanged. Direct operator controls stay in the authenticated UI.

## Persistence and rollback

`<state_dir>/connected-work.sqlite3` is a version-gated companion database; the existing sessions database schema is unchanged. Immutable blobs are under `<state_dir>/result-objects/`. Back up both with the session database. Unknown workflow schema versions are rejected without relabeling. This first schema creates results, inbox and append-only workflow event tables transactionally.

The canonical operating guide is `agent-console-results` under the authoritative n100 skills workspace. Staging receives its own copy. Existing sessions retain their previous skills; new sessions receive the updated guide through the configured shared allowlist. Domain runbooks and current-console generated skills are not replaced.

Rollback selects the previous staging source and preserves the companion database and result objects. The older release cannot display these new records; selecting 0.12.0 again restores access. Reconcile pending handoffs before withdrawing the feature. No production cutover is implied.

## Validation and remaining workflow work

Unit tests cover concurrent monotonic versions, retry deduplication, snapshots surviving source edits/deletion, safe paths/secret checks, recipient-bound acknowledgments, persisted history and future-schema rejection. API tests cover operator authentication, native capability attribution, two-session delivery, immutable downloads and rename/stop persistence. Browser tests cover the actual result/inbox components at desktop and two phone widths.

This is the result/inbox contract used by #140. Suggestions, dependency joins, reviewed automatic envelopes, scheduler launch receipts, stale propagation, recomputation and exact-release reconciliation remain required in [FULL_REVAMP_ACCEPTANCE.md](FULL_REVAMP_ACCEPTANCE.md); a queued handoff alone does not implement them.
