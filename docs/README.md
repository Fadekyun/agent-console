# Agent Console Docs

The Agent Console manages persistent AI terminals. tmux owns live sessions; `agentctl` and SQLite add metadata and consistent lifecycle commands. SSH and the Tailscale web terminal are control surfaces for the same session manager.

Normal workflow:

```text
ssh codex
→ resume or create a session
→ select a profile
→ submit a task
→ detach or disconnect
→ return later
```

Codex is the default new-session tool, OpenCode defaults to its `plan` agent, and named
authentication contexts isolate provider or account state per session. See
`auth-contexts.md` for credential handling and account setup.

Recovery remains available through SSH (root) and SSH LAN alias (user). See the other files in this directory for architecture, operation, security, Discord, recovery, and rollback details.
