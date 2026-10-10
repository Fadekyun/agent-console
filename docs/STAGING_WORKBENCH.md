# Connected Work staging · issue #90

The staging workbench is a persistent second instance. Set `AGENT_CONSOLE_UI=workbench` to make it the home page; `/work` opens it explicitly. `/desktop` and `/mobile` retain the full existing control panels on the same staging data. The default installation continues to use the existing interface.

## Isolation and switching

Use a separate Unix account as described in the README multi-instance setup. Ports alone do not isolate tmux, CLI entrypoints, native account homes, or services. Give staging its own home, workspace, profiles, skills root, configuration, state/database, release root, tmux socket, log directory and service. Do not point either instance at the other's SQLite database or run the global installer under the current-console user.

Set `AGENT_CONSOLE_INSTANCE_LABEL=Staging`, `AGENT_CONSOLE_CURRENT_URL` and `AGENT_CONSOLE_STAGING_URL` to authenticated, absolute HTTP(S) URLs. Settings links to the current console; `/versions` offers both versions. An additive proxy route can expose this chooser on the current origin without changing the current application release. Keep the original proxy route and login gate. Keep staging on LAN/tailnet access; this does not authorize a public endpoint.

Switching preserves each instance's sessions but does not migrate them or share files, projects, drafts or settings. Account stores are independent and must be configured host-locally; never bundle credentials into release artifacts. Restart only the staging web process when updating its release; keep its tmux processes alive. For resource-constrained hosts, cap staging sessions (initial trial: two) and service CPU/memory separately.

The existing `deploy canary` health-check command stops its canary process after checking it. It does not provide this persistent second instance. Do not use `promote-user-service` to trial the workbench on the current console.

## Implemented behavior

- Root-session work cards, search, active/history/attention filters, compact parent tree and explicit Add session beneath managed nodes.
- One session can complete a simple task. A human can explicitly create a child with a different role without changing its parent's role. Child admission uses the existing cross-process capacity lock. Agent delegation keeps its original role restrictions.
- Child creation starts a session immediately. Task text remains unsent under **Input · draft**; open it to review and send. Parent links describe ownership, not dependency readiness.
- Desktop resizable terminal beside the tree; phone full-screen terminal; bounded cached iframes preserve output position while switching views. Browser drafts survive reload in sessionStorage.
- Terminal reconnect no longer forces the reader to the bottom or leaves obsolete sockets scheduling reconnects. On the staging backend, Scroll-mode wheel/touch reads tmux pane history through bounded control messages; Latest output returns to live view. This is shared tmux copy mode, so other attached clients see the same history position. Typing or sending input exits history first. A standalone terminal against an older backend retains xterm scrollback and application paging.
- Current role skill assignments and provider readiness are visible. Existing projects, skill assignment controls, profiles, plans and diagnostics remain in the full control panel.

The trial workflow skill `agent-console-workbench` is maintained in the canonical skills workspace on n100 and copied into the staging skills root. It explains proportional workflows and handoffs. Staging profiles can refer to it without rewriting the current-console profiles or unrelated domain skills.

## Current program status

Optional scheduling/dependencies, durable results/inbox and launch-time skill provenance
are now implemented; see FINAL_STAGING_ACCEPTANCE.md and WORKBENCH_LAUNCHES.md.
Normal Add session remains direct. The 0.23.0 controls and architecture decision are
in CONSOLE_SIMPLIFICATION.md. No additional terminal service is required.

## Verification and rollback

Run API tests (`python -m unittest discover -s tests -p test_web.py`) and Playwright (`npm run test:ui -- workbench.spec.mjs`). The browser suite covers desktop, 360px and 390px, child payloads, drafts and reconnect reading position with real xterm assets and API fixtures. Also test the installed instance with disposable shell sessions, actual PTY output, wheel/touch scrolling, narrow viewport and version switching.

Before and after deployment record the current service PID, selected release and active session IDs. Verify distinct database, socket and effective Unix identities. Check denied HTTP/WebSocket identity as well as authorized access. Rollback means selecting the current-console link. To withdraw staging, stop only its service and remove only its added proxy routes; preserve its database, sessions/worktrees and canonical skill source. Cutover requires a separate explicit decision.
