# Authentication contexts

Authentication contexts isolate account and provider state per tool. The registry at
`~/.config/agent-console/auth-contexts.json` stores references and readiness metadata;
it never stores credential values. API-key files live under
`~/.config/agent-console/secrets.d/` with directory mode `0700` and file mode `0600`.
Provider-owned OAuth stores remain authoritative.

Useful commands:

```bash
agentctl auth context list
agentctl auth context status
agentctl auth context doctor
agentctl auth context add TOOL NAME --provider PROVIDER --kind KIND
agentctl auth context set-default TOOL NAME
agentctl auth context disable TOOL NAME --reason REASON
agentctl auth login TOOL --context NAME
agentctl secrets set openrouter --credential openrouter-main
agentctl secrets status --credential openrouter-main
```

Codex contexts receive separate `CODEX_HOME` directories. API-key contexts reference
one file in `secrets.d`; a generated per-session launcher receives only its selected
credential source. Sessions pin `auth_context`, `provider`, and `agent_mode`, so restart
does not silently switch accounts.

Health states are `ready`, `setup-required`, `disabled`, and `error`. A disabled tool
may still attach to existing legacy sessions. New sessions require a ready selected
context. OpenCode defaults to the `plan` agent; `build` must be explicitly selected.

Never put credential values in the registry, SQLite, service environments, shell startup
files, launch scripts, worktrees, logs, API responses, or browser storage.
