# Recipes, configuration and continuation

Work → Recipes lists reusable task/settings templates. New session → Save a reusable recipe creates one without opening a terminal. Edit and removal require the displayed recipe revision; removal preserves existing work. Using a recipe opens an editable draft, then Review launch shows resolved settings, selected skill hashes and warnings. Launch session is explicit. No LLM runs to suggest recipes.

Preview and creation share `SessionManager.prepare_launch`, including project repository resolution, role/mode capability, account readiness, model validation and allow/ask/deny skill policy. A launch hash binds the task, normalized configuration, role content hash, selected skills, launcher fingerprint/version and continuation input. Editing a form invalidates its preview; changed settings between preview and creation fail before native launch.

Connected Next steps use this same preparation contract since 0.19.2. Their approval includes the resolved configuration, inherited project and native sandbox; admission compares the actual receipt before dispatch. Native Plan/read actions can narrow the interactive configuration, and the receipt records the sandbox actually passed to the task adapter.

Each new interactive/native session keeps a safe configuration receipt in the companion workflow database. Configuration shows model and both reasoning settings, role, provider/account reference, repository/worktree policy, project, permission mode, profile hash, launcher identity and delivered skill hashes. It never exposes full launcher scripts, authentication values, environment variables or capability tokens. Unpinned defaults remain explicitly unknown; the fingerprint identifies the configured launcher, not every transitive dependency or remote model revision. Native/plugin/repository context may add instructions outside Console's selected packages.

Continue work is available for stopped managed interactive work. It starts a **new conversation**, keeps the original repository/worktree including uncommitted files, and records the prior session as its parent. It delivers the latest explicit result through the durable inbox, preserving source/version attribution. The new agent still acknowledges actual consumption. The task remains an unsent terminal draft until the operator sends it. This is not provider-native chat-history restoration.

Continuation checks the previous receipt against current normalized configuration, profile hash, skill hashes, permission mode and launcher fingerprint/version. It also rejects a live source, an occupied preserved workspace, missing workspace, incompatible/currently denied configuration and supervised workflow attempts (use their Next steps retry controls). Repository instructions are read afresh; a configuration receipt does not freeze external files. Changed defaults that were never pinned cannot be proven identical and remain labeled as defaults for review.

Legacy sessions without receipts and explicit live-terminal restart/refresh report that exact configuration is unavailable. The operator can open a new draft from known fields and review it; the UI explains that this path does not reuse the old worktree. No historical reasoning effort or model is fabricated.

Operator launches reserve a request key before creation. Retrying that key returns the same receipt; changing its request is rejected. A process interruption recovers a created session only from a matching durable configuration receipt. A name alone cannot prove ownership. An uncertain launch stays unresolved and is never automatically relaunched. Result delivery is idempotent during acknowledgment recovery.

## API and storage

All routes below require operator authentication; the agent reporting capability does not authorize them:

- `GET /api/workbench/recipes`; `POST /api/workbench/recipes` to save; `POST /api/workbench/recipes/{id}` to update; `POST /api/workbench/recipes/{id}/remove` with expected revision.
- `POST /api/workbench/launches/preview` with a `request` and optional durable `source_session_id`.
- `POST /api/workbench/launches` with the same request/source, `expected_hash` and stable `request_key`.
- `GET /api/workbench/launches/{request_key}` for acknowledgment/reconciliation state.
- `GET /api/workbench/sessions/{identity}/configuration` for safe recorded settings and legacy known fields.

Additive `workbench_launch_schema` version 1, `launch_configurations`, `work_recipes` and `work_launch_requests` live in `connected-work.sqlite3`. They retain records across session Stop and recipe removal. Receipt sequence identifiers are decimal strings so browsers preserve their full precision. Main session schema stays unchanged. Back up active WAL databases using SQLite backup API. Older releases ignore these tables and must not be represented as recording new exact configuration. Restore this release to recover the controls; current Console remains independently selected.
