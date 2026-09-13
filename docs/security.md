# Security

- The backend listens specifically on the LAN address at port `<web-port>`.
- Trusted clients on `<your-lan-cidr>` can connect directly; the tunnel host reaches the same
  listener through its loopback reverse tunnel and Tailscale Serve.
- The expected `Tailscale-User-Login` must match before pages, APIs, or WebSockets are accepted.
- An incorrect supplied Tailscale identity is rejected even when the source address is trusted LAN.
- Session, plan, profile, and path inputs are validated.
- Tool launchers use argument arrays and shell-quoted generated launcher files.
- Authentication contexts store references only. API keys are mode-`0600` files under a
  mode-`0700` `secrets.d` directory; provider OAuth stores remain authoritative.
- Discord remains planning-only.
- Raw terminal keystrokes are not logged.
- Managed CLI Kill requires two yes/no confirmations unless `--yes` is supplied.
  Browser Kill uses a two-click modal; unmanaged sessions add an acknowledgement checkbox.
- Kill preserves worktrees, transcripts, plan artifacts, and session history.
- Authorized-key inventory contains fingerprints and comments only. Private keys are never copied.

The console is privileged by design because an attached terminal has the authority of `<your-user>`.
The trusted LAN and the tailnet identity allowlist are its access boundaries.

## Automatic planning request gate

Issue #91 adds a local CLI protocol, not an HTTP request endpoint and not a generic PTY
paste/send API. It is disabled when `<config-dir>/plan-integration.json` is absent. The
configuration file must be a mode-`0600` regular file with exactly these server-managed
fields (the values below are deliberately placeholders, not production mappings):

```json
{
  "protocol_version": 1,
  "enabled": false,
  "provider_capability_verified": false,
  "relay_capability_sha256": "<sha256-of-inherited-relay-capability>",
  "owners": ["<17-to-20-digit-owner-id>"],
  "channels": ["<17-to-20-digit-private-channel-id>"],
  "context_root": "/server/controlled/prepared-contexts",
  "provider": {"tool": "codex", "auth_context": "server-selected-context"},
  "projects": {
    "n100": {
      "project_id": "<existing-active-console-project-id>",
      "context_dir": "/server/controlled/prepared-contexts/n100",
      "provenance": "<collector and source description>",
      "collected_at": "<RFC3339 collection time>"
    }
  }
}
```

The fixed relay opens the approved capability file on a private inherited descriptor and
sets `AGENT_CONSOLE_RELAY_CAPABILITY_FD` to that descriptor number. Request JSON never
contains the capability. Console verifies its hash before it trusts the supplied Discord
owner/channel metadata, then repeats the allowlists at request and status boundaries.
The forced-command environment must pin Console state/config paths and must not allow a
remote caller to choose the capability descriptor, configuration path, provider argv,
model, auth context, context path, or feature flags.

The runner uses a fresh per-request `HOME` and `CODEX_HOME`, copies only the selected native
provider authentication file, and passes a small environment with no SSH agent, hooks,
plugins, MCP/connectors, approval/evidence capability, relay capability, or inherited
`AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS`. It invokes `codex exec --json` with `--sandbox
read-only`, `--ignore-user-config`, `--ignore-rules`, approval policy `never`, a server
schema, a frozen context directory, and the frozen prompt on stdin. These controls are
preparation, not proof of OS credential isolation: a clean home does not prevent reads of
every other host-readable path, and the model transport itself still requires provider
network access. The separate native `codex sandbox` probe showing denied fixture writes and
tool network is useful evidence but does not prove the integrated `exec` path, event schema,
recursion denial, or minimal filesystem visibility.

Therefore `provider_capability_verified` must remain false until a deployment-specific,
real integrated smoke proves: exact CLI compatibility; pipe event ordering; output schema;
read-only prepared context; denied tool network/SSH/connectors/hooks/delegation/Console
recursion; and an explicit minimal-read-root containment policy that prevents reading host
credentials outside the clean home. Do not enable based only on unit tests or the clean-home
profile. No automatic fallback, privilege increase, alternate provider/model, or request
replay is permitted when this gate fails.

Request content, frozen prompt/context, and result files expire after 30 days. Expiry keeps
the request key, canonical hash, safe terminal state, acknowledgement/receipt bindings, and
artifact digest as an idempotency tombstone, so an old request ID can never launch again.
Runner process receipts bind PID, process-group ID, Linux process start time, and kernel boot
ID. Lifecycle operations fail closed to `delivery_unknown` and retain admission whenever any
identity component is absent or mismatched, or `/proc` cannot prove that the owned group is
empty; they never signal a guessed or reused PID.

## Docker Security

- The default `docker-compose.yml` binds to `127.0.0.1` only (loopback).
- `no-new-privileges:true` is enabled by default.
- `privileged: true` must never be used. It grants full host access and defeats container isolation.
- Uvicorn uses `--loop asyncio` to avoid uvloop's `socketpair()` requirement.
- On hosts where AppArmor blocks `socketpair()` (e.g., Proxmox LXC containers), use the
  optional `compose.n100-apparmor.yaml` override. This disables AppArmor confinement for the
  container only — confirm the denial with `journalctl -k` before using it.
- The entrypoint script copies default profiles on first boot and preserves user edits on restart.
