# Console reliability and tool history

The workbench uses an authenticated bounded overview (`GET /api/workbench?overview=true`). Initial responses include active groups; historical groups load in pages of twenty. Search applies to full stored briefs, including unloaded history. A selected durable session or logical step includes its complete group and preserves historical attempts. Existing callers of `/api/workbench` and `compact=true` retain their original response shape. The overview adds `page`, `filters` and `project_names` metadata. History paging uses `history_offset` and `history_limit` (maximum fifty); it never changes stored visibility or attention.

Cards emphasize the next action. Process and recorded-result facts remain available in a disclosure. Current attention and historical reviews have separate links. Stopping a process does not establish completion, and marking a review handled does not rewrite its result.

Session audit events include stable `session_id` metadata. History follows unambiguous legacy rename intervals, omits uncertain attribution and never treats a reused name as proof of identity. No schema migration is required.

## Read-only tool calls

Session details → **Tool calls** reads Codex/Codex Pro records from the session's pinned native home and a verified conversation. Pi, Hermes and other providers explicitly report unavailable history. Shared/global homes, ambiguous conversations and mismatched native headers are refused. Existing native recovery bindings are reused when present. This reader does not create or resume a conversation, read arbitrary paths supplied by a browser, or copy native logs into a second store.

- `GET /api/workbench/sessions/{id}/tool-calls` returns up to fifty calls, timestamps, available durations, tool names, command exit codes and redacted previews. `before` and `thread` paginate older bounded segments.
- `GET /api/workbench/sessions/{id}/tool-calls/output` validates `offset`, `call_id` and `thread`; `download=true` returns a redacted text attachment. Both routes use existing owner authentication and `Cache-Control: no-store`.
- Each scan reads at most 16 MiB, each accepted native record is at most 4 MiB, previews are 2,048 characters, and expanded/downloaded output is limited to 256 KiB. The native original is preserved. Missing output is explicitly unavailable; it is not labelled running. Nonzero exits can be expected for searches or deliberate failure tests.
- Tool arguments and prompts are not returned by this interface. Existing credential-redaction rules apply to every output surface. Native output is untrusted data, rendered as text.

## Operator and development commands

`agentctl session restart NAME` aliases `restart-agent`; `agentctl profiles list` aliases `profile list`. Both retain existing permission/read boundaries. `agentctl doctor` includes executable availability and command hints; missing `rg` is advisory and does not change admission.

Use `python scripts/test-isolated.py` to run Python tests. It supplies fake harnesses, private state/config/tmux paths and a temporary log directory, preventing fixture traffic from entering operational logs. JSON diagnostics include process/release identity, optional session/request/operation context, exception class and a redacted bounded traceback.

## Workspace entrypoint

`python scripts/compact-workspace-instructions.py /path/to/workspace` previews compaction. Add `--write` to atomically replace its top-level `AGENTS.md` after preserving the full original in a SHA-256-named operations archive. Existing guardrails, AGC policies and Console/GitHub policy blocks remain in the entrypoint; all project policy remains applicable through the required operations-manual link. The command is idempotent and refuses conflicting archives. Keep canonical N100 and CT115 copies scoped; do not mirror credentials or perform a broad skill sync.

## Rollback

No deployment is performed by these changes. Select the prior immutable release if a later approved rollout needs rollback. New audit JSON metadata is backward compatible; overview and tool-history APIs are additive. To undo workspace compaction, verify the recorded archive hash and restore that exact `AGENTS.md` file. Native history and workspaces are never deleted.
