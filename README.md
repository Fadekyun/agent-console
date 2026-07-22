# Agent Console

A unified tmux session manager for AI coding agent orchestration. Manage multiple AI coding tools (Codex, Claude, OpenCode, Hermes) through a web terminal, CLI, and SSH with session isolation, delegation trees, and audit logging.

## Features

- **Multi-tool orchestration** — Codex, Claude, OpenCode, Hermes, and Shell sessions
- **Web terminal** — Browser-based xterm.js PTY attached to tmux sessions
- **Session delegation** — Parent/child trees with read-only peer review
- **Auth contexts** — Per-tool credential isolation with `secrets.d` storage
- **Model catalogue** — OpenRouter and OpenCode-Go model browsing with cost estimation
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
| `AGENT_CONSOLE_STATE_DIR` | `~/.local/share/agent-console` | SQLite DB, launchers, transcripts |
| `AGENT_CONSOLE_DB` | `<state_dir>/agent-console.sqlite3` | Database path |
| `AGENT_CONSOLE_PROFILE_DIR` | `<checkout>/agent-profiles` | Agent profile directory (always relative to checkout, not workspace) |
| `AGENT_CONSOLE_PORT` | `3210` | Web service listen port |
| `AGENT_CONSOLE_BIND_HOST` | `127.0.0.1` | Web service bind address |
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

Running a second Agent Console instance on the same host requires changing ports and state paths to avoid conflicts:

```bash
git clone https://github.com/Fadekyun/agent-console.git /opt/agent-console-secondary
cd /opt/agent-console-secondary
export AGENT_CONSOLE_PORT=3211
export AGENT_CONSOLE_TUNNEL_PORT=13211
export AGENT_CONSOLE_STATE_DIR=$HOME/.local/share/agent-console-secondary
export AGENT_CONSOLE_CONFIG_DIR=$HOME/.config/agent-console-secondary
export AGENT_CONSOLE_WORKSPACE_ROOT=$HOME/agent-workspace-secondary
# profile_dir defaults to the checkout's agent-profiles — independent of workspace
export AGENT_CONSOLE_PROFILE_DIR=$PWD/agent-profiles
export AGENT_CONSOLE_TAILSCALE_LOGIN=your-email@example.com
export AGENT_CONSOLE_LAN_CIDR=10.0.0.0/8
export AGENT_CONSOLE_TRUSTED_HOSTS=localhost,127.0.0.1,your-lan-ip
./scripts/install.sh
```

The installer generates isolated systemd user units with unique ports and state paths. Each instance manages its own tmux sockets, SQLite database, launchers, and transcripts.

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

See [docs/skills.md](docs/skills.md) for skill authoring.

## Upgrade Compatibility

The installer is designed for safe upgrades — it never deletes or replaces:

- tmux sockets (canonical or legacy)
- SQLite database
- Launcher scripts under `$AGENT_CONSOLE_STATE_DIR/launchers`
- Auth contexts or secrets
- Session transcripts
- Worktrees or handoffs

Existing systemd units are **replaced** on reinstall. Running sessions survive because `KillMode=process` (preserved in generated units) kills only the agent process on stop, leaving tmux sessions intact.

When upgrading an existing installation:

1. The installer stops and restarts the web service (`systemctl --user restart`).
2. Existing canonical and legacy tmux sessions are reconciled on next `SessionManager` startup.
3. Agent CLIs that have been removed or whose provider IDs have changed remain attachable while running but may not restart after a CLI upgrade. Remove stale launchers manually from `$AGENT_CONSOLE_STATE_DIR/launchers` if needed.
4. The `runtime.env` file is rewritten — any customizations added after the last install are lost. Keep a backup or re-apply overrides on reinstall.
5. Profile directory is validated at install time. If `AGENT_CONSOLE_PROFILE_DIR` points to a nonexistent or empty directory, the installer fails **before** modifying any units or state, preventing silent zero-profile deployments.

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
