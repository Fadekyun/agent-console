# Native dispatch staging evidence — 2026-10-02

Tested and published native source: `9967813c24eb466408145da6952590f7f52b06ab` (0.14.1), following the dispatch feature at `91da30e877deb622b3e8b4e9f3dabe9cee24ce63`. Branch: `feature/connected-work-staging-20261001`. This is partial evidence for #140; it does not close the whole revamp.

## Automated checks

- 37 workflow tests passed, including real isolated tmux launches with a deterministic executable fixture, receipt recovery, concurrent dispatcher sweeps, prepared-runner loss, pause/stop, read-only action enforcement, changed-input coalescing, budgets, exact worktree artifacts, joins, failed inputs, immutable results, and reader-held SQLite snapshots during native result commits.
- 70 authenticated API tests and 117 existing session tests passed. A session capability can propose but cannot accept or enlarge the operator envelope.
- 18 browser scenarios exercise the shared workbench components at desktop and 360/390px phone widths. These use mocked API/PTY fixtures; the separate live checks below use staging's actual APIs and native runtime.

## Live native task

No unrelated staging session was active. A disposable shell source published a ready checkpoint. Its proposed read-only Codex Pro step did not launch before acceptance, and acceptance still waited for a final source result. The fixture then published that final with one selected file and removed the source file.

The native attempt read the preserved snapshot, verified its hash and returned the exact four-word sentence. The runner recorded the input as consumed and published one passing final, with no unnecessary follow-up. Staging's web service was restarted during native execution. The attempt completed once; no replacement identity or duplicate launch appeared. Both delivered skill package hashes matched the reviewed launch preview. Cleanup stopped only the disposable workflow.

- Root: `sess-a8a565d82f0a42c2a71fa2ce89bd9db5`
- Logical step: `step-993383f38ae7493db267f1f2b70b3702`
- Attempt: `attempt-ba0b039192cd47f983fc5217d4125df5`
- Result: `result-a352d3c829574437b43f8c5078bb7eac`
- Native adapter: Codex Pro, `codex-cli 0.159.2`.

An earlier live run revealed conflicting interactive completion instructions and a SQLite reader/writer lock cycle. Both were corrected before this successful run. The runner now uses dedicated native instructions; the companion DB uses WAL, and a regression holds a status-read snapshot while a native result writer commits.

## Recovery and UI

With the fixture stopped, selecting staging 0.13 and restoring 0.14.1 preserved exact counts across results, inbox, events, graphs, nodes, edges, revisions, deliveries, active deliveries, output bindings, native bindings, policies, steps and attempts. Older result APIs could still read the native final. The stopped workflow and single-attempt history returned after restoration.

Live Chromium checks at 1280, 360 and 390px read the stopped workflow, passing native result and consumed input without page errors or horizontal overflow. These are browser emulation checks, not a physical-device terminal certification. The screenshots prompted a further compact task/details and handoff-form improvement in 0.14.2.

The current console retained PID `2575152` and release `release-20260920-014453-dc29ccdd1b7e`. Current, staging and `/versions` TLS routes returned 200. The task evidence directory `handoffs/console-dispatch-20261002` retains fixture scripts, sanitized result/receipt records, screenshots and recovery counts.

## Guides and remaining scope

Canonical n100 `agent-console-results` and `agent-console-workbench` guides were updated to revision `2026-10-02.3`, with content-checked backups outside their packages, then copied to staging. Existing session copies and current-console mirrors were preserved. The real attempt received the reviewed updated packages. The other required guide bundles and the full harness discovery matrix remain open.

External release actions are explicitly unsupported by the dispatch action allowlist until the exact-candidate release gate is implemented. Unifying connected ownership with the main tree, remaining workbench features, remaining skills provenance/provider work and the final full acceptance audit are still required.
