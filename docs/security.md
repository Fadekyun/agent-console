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

## Docker Security

- The default `docker-compose.yml` binds to `127.0.0.1` only (loopback).
- `no-new-privileges:true` is enabled by default.
- `privileged: true` must never be used. It grants full host access and defeats container isolation.
- Uvicorn uses `--loop asyncio` to avoid uvloop's `socketpair()` requirement.
- On hosts where AppArmor blocks `socketpair()` (e.g., Proxmox LXC containers), use the
  optional `compose.n100-apparmor.yaml` override. This disables AppArmor confinement for the
  container only — confirm the denial with `journalctl -k` before using it.
- The entrypoint script copies default profiles on first boot and preserves user edits on restart.
