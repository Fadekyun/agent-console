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
./scripts/install.sh
```

The installer creates a Python venv, installs dependencies, symlinks CLI wrappers, and starts systemd user services.

Verify the installation:

```bash
./scripts/agentctl doctor
```

## Configuration

All settings are controlled via environment variables. The installer uses sensible defaults; override as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_CONSOLE_WORKSPACE_ROOT` | `.` | Root directory for repos, profiles, and handoffs |
| `AGENT_CONSOLE_STATE_DIR` | `~/.local/share/agent-console` | SQLite DB, launchers, transcripts |
| `AGENT_CONSOLE_DB` | `<state_dir>/agent-console.sqlite3` | Database path |
| `AGENT_CONSOLE_PROFILE_DIR` | `<workspace>/agent-profiles` | Agent profile directory |
| `AGENT_CONSOLE_TMUX_SOCKET` | (system default) | tmux socket name |
| `AGENT_CONSOLE_TMUX_SOCKET_PATH` | `/run/user/<uid>/agent-console/tmux.sock` | tmux socket path |
| `AGENT_CONSOLE_TAILSCALE_LOGIN` | (empty) | Expected Tailscale identity email |
| `AGENT_CONSOLE_LAN_CIDR` | `127.0.0.1/32` | Trusted LAN network (set to your LAN CIDR) |
| `AGENT_CONSOLE_TRUSTED_HOSTS` | `localhost,127.0.0.1` | TrustedHostMiddleware allowlist |
| `AGENT_CONSOLE_MAX_PTY_CLIENTS` | `2` | Max WebSocket PTY clients per session |
| `AGENT_CONSOLE_MAX_SESSIONS` | `12` | Max managed sessions |
| `AGENT_CONSOLE_MAX_CHILDREN` | `3` | Max children per parent session |
| `AGCONSOLE_CODEX_BIN` | `codex` | Path to Codex CLI binary |
| `AGCONSOLE_CLAUDE_BIN` | `claude` | Path to Claude CLI binary |
| `AGCONSOLE_OPENCODE_BIN` | `opencode` | Path to OpenCode CLI binary |
| `AGCONSOLE_HERMES_BIN` | `hermes` | Path to Hermes CLI binary |
| `AGCONSOLE_SKILLS_ROOT` | `~/codex/skills` | Canonical skills directory |
| `AGCONSOLE_RETAINED_SKILLS` | (empty) | Comma-separated skill names to sync |

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
