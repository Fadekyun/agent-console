# Agent Console

A unified tmux session manager for AI coding agent orchestration. Manage multiple AI coding tools (Codex, Claude, OpenCode, Hermes) through a web terminal, CLI, and SSH with session isolation, delegation trees, and audit logging.

## Features

- **Multi-tool orchestration** — Codex, Claude, OpenCode, Hermes, and Shell sessions
- **Web terminal** — Browser-based xterm.js PTY attached to tmux sessions
- **Session delegation** — Parent/child trees with read-only peer review
- **Auth contexts** — Per-tool credential isolation with `secrets.d` storage (legacy context key `opencode-go-default` is preserved for compatibility)
- **Model catalogue** — OpenRouter and OpenCode model browsing with cost estimation
- **Plan management** — Discord-integrated planning with execution handoff
- **Audit logging** — SQLite-backed audit trail for all operations
- **SSH client installer** — Cross-platform SSH config for remote access

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- tmux
- git
- At least one AI coding tool installed (codex, claude, opencode, or hermes)

## Quick Start

```bash
git clone https://github.com/Fadekyun/agent-console.git
cd agent-console
# Set required variables before install
export AGENT_CONSOLE_TAILSCALE_LOGIN=your-email@example.com
export AGENT_CONSOLE_LAN_CIDR=10.0.0.0/8
export AGENT_CONSOLE_TRUSTED_HOSTS=localhost,127.0.0.1,your-lan-ip
./scripts/install.sh
```

The installer creates a Python venv, installs dependencies, symlinks CLI wrappers, resolves AI CLI tool paths to absolute paths (so systemd --user can find them), validates configuration, generates systemd user units, and starts the web service.

Verify the installation:

```bash
agentctl doctor
```

## Configuration

All settings are controlled via environment variables. The installer uses sensible defaults; override as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_CONSOLE_WORKSPACE_ROOT` | checkout root | Root directory for repos and handoffs |
| `AGENT_CONSOLE_STATE_DIR` | `~/.local/share/agent-console` | SQLite DB, launchers, transcripts (note: does not relocate the installer venv, which stays under the original default path) |
| `AGENT_CONSOLE_DB` | `<state_dir>/agent-console.sqlite3` | Database path |
| `AGENT_CONSOLE_PROFILE_DIR` | `<checkout>/agent-profiles` | Agent profile directory (always relative to checkout, not workspace) |
| `AGENT_CONSOLE_PORT` | `3210` | Web service listen port |
| `AGENT_CONSOLE_BIND_HOST` | `127.0.0.1` | Web service bind address |
| `AGENT_CONSOLE_SOURCE_ROOT` | checkout root | Source root for deployment releases |
| `AGENT_CONSOLE_CANARY_BIND` | `127.0.0.1` | Canary server bind address |
| `AGENT_CONSOLE_CANARY_PORT` | `33100` | Canary server listen port |
| `AGENT_CONSOLE_DEPLOYMENT_MODE` | `disabled` | Deployment mode (`disabled` or `staging`) |
| `AGENT_CONSOLE_TUNNEL_PORT` | `13210` | Reverse tunnel remote port |
| `AGENT_CONSOLE_TUNNEL_HOST` | `localhost` | SSH tunnel jump host |
| `AGENT_CONSOLE_TMUX_SOCKET` | (system default) | tmux socket name |
| `AGENT_CONSOLE_TMUX_SOCKET_PATH` | `/run/user/<uid>/agent-console/tmux.sock` | Canonical tmux socket path |
| `AGENT_CONSOLE_TAILSCALE_LOGIN` | (empty) | Expected Tailscale identity email |
| `AGENT_CONSOLE_LAN_CIDR` | `127.0.0.1/32` | Trusted LAN network (set to your LAN CIDR) |
| `AGENT_CONSOLE_TRUSTED_HOSTS` | `localhost,127.0.0.1` | TrustedHostMiddleware allowlist |
| `AGENT_CONSOLE_MAX_PTY_CLIENTS` | `2` | Max WebSocket PTY clients per session |
| `AGENT_CONSOLE_MAX_SESSIONS` | `12` | Max managed sessions |
| `AGENT_CONSOLE_MAX_CHILDREN` | `3` | Max children per parent session |
| `AGCONSOLE_CODEX_BIN` | (auto-detected) | Absolute path to Codex CLI binary |
| `AGCONSOLE_CLAUDE_BIN` | (auto-detected) | Absolute path to Claude CLI binary |
| `AGCONSOLE_OPENCODE_BIN` | (auto-detected) | Absolute path to OpenCode CLI binary |
| `AGCONSOLE_HERMES_BIN` | (auto-detected) | Absolute path to Hermes CLI binary |
| `AGCONSOLE_SKILLS_ROOT` | `~/codex/skills` | Canonical skills root directory |
| `AGCONSOLE_RETAINED_SKILLS` | (empty) | Comma-separated skill names to retain |

## Docker

```bash
# 1. Copy and edit environment config
cp .env.example .env
# Edit .env — at minimum set AGENT_CONSOLE_TAILSCALE_LOGIN

# 2. Start
docker compose up -d

# 3. Verify
curl -f http://localhost:3210/healthz
```

The container binds to `127.0.0.1:3210` (loopback only). Set `AGENT_CONSOLE_TAILSCALE_LOGIN` in `.env` — without it, all protected UI and API requests return 503.

### Hosts with AppArmor restrictions

Some hosts (e.g., Proxmox LXC containers) have an AppArmor profile that blocks `socketpair()`, preventing the container from starting. If `docker compose up` fails with `PermissionError: [Errno 13] Permission denied` on socketpair, use the AppArmor override:

```bash
docker compose -f docker-compose.yml -f compose.n100-apparmor.yaml up -d
```

This disables AppArmor confinement for the container only. Do **not** use `privileged: true`. Confirm the AppArmor denial first:

```bash
sudo journalctl -k --since "5 minutes ago" | grep -i apparmor
```

### AI CLI Tools in Docker

The Docker image provides the web orchestration UI but does **not** bundle AI coding CLIs (Codex, Claude, OpenCode, Hermes). This is intentional — each tool has its own authentication flow and API keys.

To use AI tools inside the container, either:

1. **Install CLIs in a custom Dockerfile**:
   ```dockerfile
   FROM agent-console:latest
   RUN npm install -g @openai/codex
   ENV AGCONSOLE_CODEX_BIN=/usr/local/bin/codex
   ```

2. **Mount CLI binaries** (provider-specific, not all of ~/.config):
   ```yaml
   volumes:
     - /path/to/your/.local/bin/codex:/usr/local/bin/codex:ro
   ```

3. **Use the web UI for orchestration only** and run AI CLIs on the host, connecting via SSH aliases.

Without AI CLIs installed, the web UI shows tools as "launcher missing" in the provider health panel. The Shell tool is always available.

## Multi-Instance Setup

Agent Console uses fixed systemd user unit names, `~/bin` symlink targets, and a uid-based tmux socket path, so two instances **under the same Unix user** are not isolated even with different ports and state directories. The supported multi-instance approach uses **separate Unix users**:

```bash
# Create a dedicated system user for the secondary instance
sudo useradd --system --create-home --home-dir /opt/agent-console-secondary agent-console-2
sudo -u agent-console-2 bash
cd /opt/agent-console-secondary
git clone https://github.com/Fadekyun/agent-console.git .
export AGENT_CONSOLE_PORT=3211
export AGENT_CONSOLE_TUNNEL_PORT=13211
export AGENT_CONSOLE_TAILSCALE_LOGIN=your-email@example.com
export AGENT_CONSOLE_LAN_CIDR=10.0.0.0/8
export AGENT_CONSOLE_TRUSTED_HOSTS=localhost,127.0.0.1,your-lan-ip
./scripts/install.sh
```

Each Unix user gets its own systemd user bus, `~/.config/systemd/user/` unit directory, tmux sockets, and `~/bin` namespace, providing full isolation.

## Skills Setup

Agent skills provide reusable instructions (SKILL.md files) under a canonical root directory. The installer persists `AGCONSOLE_SKILLS_ROOT` and `AGCONSOLE_RETAINED_SKILLS` into `runtime.env` but does not auto-sync — run these steps after install:

```bash
# Sync retained skills into tool-specific roots
agentctl skills sync

# Verify skill links
agentctl skills doctor
```

Configure which skills to retain:

```bash
export AGCONSOLE_RETAINED_SKILLS="skill-a,skill-b,skill-c"
./scripts/install.sh   # re-run to persist new value
```

## Upgrade Compatibility

The installer preserves the following existing state across reinstalls — it does not delete or replace them:

- tmux sockets (canonical and legacy)
- SQLite database
- Launcher scripts under `$AGENT_CONSOLE_STATE_DIR/launchers`
- Auth contexts and secrets
- Session transcripts
- Worktrees and handoffs

The venv, npm dependencies, and state directories are prepared **before** profile validation occurs, so a validation failure will have already created those directories and installed dependencies. This is intentional: validation gates unit writes and systemctl calls, not earlier preparation steps.

Existing systemd units are **replaced** on reinstall. Running sessions survive because `KillMode=process` (preserved in generated units) kills only the agent process on stop, leaving tmux sessions intact.

The installer generates a stable runner at `$AGENT_CONSOLE_STATE_DIR/runner.sh` (mode 0700). The systemd unit executes this runner instead of referencing the checkout directly. The runner selects a contained release from `$AGENT_CONSOLE_STATE_DIR/releases/current` only when all required `@xterm` assets are present, otherwise it falls back to the bootstrap checkout root. Promotion still restarts the web service; tmux sessions remain running and browser terminals use bounded reconnect.

When upgrading an existing installation:

1. The installer runs `systemctl daemon-reload`, `enable`, then `restart` — this picks up changed code and unit settings even when the service is already active.
2. Existing canonical and legacy tmux sessions are reconciled on next `SessionManager` startup.
3. Agent CLIs that have been removed or whose provider IDs have changed remain attachable while running but may not restart after a CLI upgrade. To replace a launcher for a still-running legacy session: first stop the agent gracefully (or let it complete), back up the launcher script, then recreate after the session has exited. Do not delete a launcher while its session is still running — preserve and recreate after stopping.
4. The `runtime.env` file is rewritten — any customizations added after the last install are lost. Keep a backup or re-apply overrides on reinstall.
5. Profile directory is validated **early** in the install flow (before unit writes and systemctl calls) but after directory/venv/dependency preparation. If `AGENT_CONSOLE_PROFILE_DIR` points to a nonexistent or empty directory, the installer fails before modifying units or calling systemctl, preventing silent zero-profile deployments.

For a Git-based installation, `scripts/update.sh <approved-main-sha>` provides the guarded update path. It accepts only the exact current `origin/main` SHA, creates a clean detached checkout, takes an online database and runtime/unit/runner/launcher backup, runs the installer, checks service health and session inventory parity, and restores the prior unit/runtime/runner on failure. It does not bypass protected-branch review and does not copy credentials.

## CLI Usage

```bash
# Check system health
agentctl doctor

# List sessions
agentctl session list

# Review a session's recent output
agentctl session review <name>

# Sync agent skills
agentctl skills sync

# Manage auth contexts
agentctl auth list
```

## Architecture

The `SessionManager` is the authoritative application layer. tmux owns live terminal state; SQLite stores metadata. The web backend, CLI, and SSH selector all call the same library.

See [docs/architecture.md](docs/architecture.md) for details.

## Security

- Tailscale identity or trusted LAN required for access
- Tool credentials stored as mode-0600 files under a mode-0700 directory
- Input validation on all session names, paths, and tool parameters
- Audit logging for all operations

See [docs/security.md](docs/security.md) for details.

## Project Structure

```
agent_console/     Core Python package
app/               Legacy standalone job-queue FastAPI app
client/            SSH config installers (Python + Zsh)
deploy/systemd/    Systemd user service templates
docs/              Architecture, security, operations, recovery docs
scripts/           Install, uninstall, verify, backup, CLI wrappers
tests/             Python unit tests + Playwright UI tests
web/static/        Frontend HTML, JS, CSS
```

## License

MIT — see [LICENSE](LICENSE).
