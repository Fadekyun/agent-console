# Architecture

The Python `SessionManager` is the authoritative application layer. `agentctl`, `agent-selector`, FastAPI, and Discord plan discovery call the same library. tmux remains authoritative for whether a terminal is alive, while SQLite stores tool, profile, repository, worktree, parent, plan, origin, and audit metadata.

The orchestration UI is a derived tree rather than a workflow engine. Independent sessions
remain roots; only explicit read-only delegations create parent/child edges. Mechanical live
state comes from tmux and pane processes, never from interpreting terminal text. Peer review
captures bounded pane output through the recorded socket without persisting that output.

The web backend binds specifically to the trusted LAN address on port <web-port>. A
`<your-user>` user service opens a reverse SSH tunnel from <tunnel-host> loopback port <tunnel-port> to
that listener. Tailscale Serve HTTPS proxies to the tunnel and supplies the tailnet
identity header. Browser WebSockets receive a dedicated PTY attached to tmux; closing
a socket ends only that attachment.

Managed tmux sessions start a persistent login shell and invoke the generated agent
launcher as its child. If an agent exits after Ctrl-C, tmux returns to that clean parent
shell instead of destroying the session; Restart respawns the parent and relaunches the
pinned launcher. Each managed session sets `detach-on-destroy=on`, so killing it closes
its attached browser PTYs instead of silently switching them to another tmux session.

<tunnel-host>'s existing HTTP port 80 route belongs to Takashimaya Cards. Agent Console uses HTTPS 443 and must never reset the existing Serve configuration.
