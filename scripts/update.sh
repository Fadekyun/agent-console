#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
state="${AGENT_CONSOLE_STATE_DIR:-$HOME/.local/share/agent-console}"
config_dir="${AGENT_CONSOLE_CONFIG_DIR:-$HOME/.config/agent-console}"
runtime="$config_dir/runtime.env"
unit="$HOME/.config/systemd/user/agent-console-web.service"
tunnel_unit="$HOME/.config/systemd/user/agent-console-tailscale-tunnel.service"
runner="$state/runner.sh"
requested_sha="${1:-}"

if ! [[ "$requested_sha" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Usage: %s <40-character-approved-main-sha>\n' "$0" >&2
  exit 2
fi
if [ ! -f "$runtime" ]; then
  printf 'FATAL: existing trusted runtime configuration is required.\n' >&2
  exit 1
fi
# Resolve the configured state before choosing locks, backup paths or the runner.
runtime_exports="$(python3 -B "$root/scripts/maintenance.py" environment "$runtime")"
eval "$runtime_exports"
state="${AGENT_CONSOLE_STATE_DIR:-$state}"
runner="$state/runner.sh"
backend="$(python3 -B "$root/scripts/maintenance.py" backend)"
if [ "$backend" = launchd ]; then
  unit="$HOME/Library/LaunchAgents/com.agent-console.web.plist"
fi
if [ "$backend" = foreground ]; then
  printf 'FATAL: replace the Docker image through its supervisor; do not mutate a running container.\n' >&2
  exit 1
fi
if [ ! -f "$runtime" ] || [ ! -f "$unit" ] || [ ! -f "$runner" ]; then
  printf 'FATAL: existing runtime, service units, and stable runner are required before update.\n' >&2
  exit 1
fi
if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'FATAL: updater must run from a Git checkout.\n' >&2
  exit 1
fi

mkdir -p "$state/update-sources" "$state/update-backups"
chmod 700 "$state/update-sources" "$state/update-backups"
if [ "${AGENT_CONSOLE_UPDATE_LOCKED:-}" != 1 ]; then
  exec python3 -B "$root/scripts/maintenance.py" with-lock "$state/update.lock" "$0" "$requested_sha"
fi

git -C "$root" fetch --quiet origin main
approved_sha="$(git -C "$root" rev-parse origin/main)"
if [ "$requested_sha" != "$approved_sha" ]; then
  printf 'FATAL: requested SHA %s is not current protected origin/main %s.\n' \
    "$requested_sha" "$approved_sha" >&2
  exit 1
fi

checkout="$state/update-sources/$requested_sha"
if [ -e "$checkout" ]; then
  existing_sha="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [ "$existing_sha" != "$requested_sha" ]; then
    printf 'FATAL: existing update checkout has unexpected revision: %s.\n' "$checkout" >&2
    exit 1
  fi
else
  git -C "$root" worktree add --detach "$checkout" "$requested_sha"
fi
if [ -n "$(git -C "$checkout" status --porcelain --untracked-files=all)" ]; then
  printf 'FATAL: update checkout is not clean: %s.\n' "$checkout" >&2
  exit 1
fi

# Read the existing trusted runtime before choosing the database to back up.
runtime_exports="$(python3 -B "$root/scripts/maintenance.py" environment "$runtime")"
eval "$runtime_exports"
database_path="${AGENT_CONSOLE_DB:-$state/agent-console.sqlite3}"
timestamp="$(date +%Y%m%d_%H%M%S)"
backup="$state/update-backups/${timestamp}-${requested_sha:0:12}"
mkdir -p "$backup"
chmod 700 "$backup"
cp -a "$runtime" "$unit" "$runner" "$backup/"
if [ -f "$tunnel_unit" ]; then cp -a "$tunnel_unit" "$backup/"; fi
if [ -f "$state/maintenance.py" ]; then cp -a "$state/maintenance.py" "$backup/"; fi
mkdir -p "$backup/releases"
current_link="$state/releases/current"
if [ -L "$current_link" ]; then
  current_target="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$current_link")"
  case "$current_target" in
    "$state/releases"/release-*) cp -a "$current_link" "$backup/releases/" ;;
    *)
      printf 'FATAL: current release link escapes the releases directory: %s.\n' "$current_target" >&2
      exit 1
      ;;
  esac
elif [ -e "$current_link" ]; then
  printf 'FATAL: current release path is not a symlink: %s.\n' "$current_link" >&2
  exit 1
fi
mkdir -p "$backup/bin" "$backup/local-bin"
for name in agentctl agent-selector agent-console-status agent-console-logs; do
  if [ -L "$HOME/bin/$name" ]; then cp -a "$HOME/bin/$name" "$backup/bin/"; fi
  if [ -L "$HOME/.local/bin/$name" ]; then cp -a "$HOME/.local/bin/$name" "$backup/local-bin/"; fi
done
if [ -d "$state/launchers" ]; then
  cp -a "$state/launchers" "$backup/"
fi
# Consistent closed snapshots avoid live WAL-sidecar creation in the guarded
# inventory reader. This is maintenance backup, never an inspection fallback.
"$state/venv/bin/python" -B "$checkout/scripts/snapshot-database.py" \
  "$database_path" "$backup/agent-console.sqlite3"
python3 -B "$checkout/scripts/maintenance.py" snapshot-inventory "$backup/agent-console.sqlite3" > "$backup/sessions-before.json"

runtime_exports="$(python3 -B "$root/scripts/maintenance.py" environment "$runtime")"
eval "$runtime_exports"
health_host="${AGENT_CONSOLE_BIND_HOST:-127.0.0.1}"
case "$health_host" in
  0.0.0.0|::) health_host=127.0.0.1 ;;
esac

wait_for_health() {
  local attempts="${1:-30}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if python3 -B "$checkout/scripts/maintenance.py" health "$health_host" "${AGENT_CONSOLE_PORT:-3210}" "$current_link"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

rollback() {
  # Gate BEFORE restoring old runner/CLI/release files: an old writer must never
  # silently relabel a database containing private request data.
  if [ -L "$backup/releases/current" ]; then
    rollback_release="$current_target"
  else
    rollback_release="$root"
  fi
  if ! "$state/venv/bin/python" -B "$checkout/scripts/select-release.py" \
    --database "$database_path" --config "$config_dir" --state "$state" \
    --releases "$state/releases" --release "$rollback_release"; then
    printf 'FATAL: schema-incompatible rollback refused; current source and all database state retained. Use reviewed forward recovery.\n' >&2
    return 1
  fi
  cp -a "$backup/runtime.env" "$runtime"
  cp -a "$backup/$(basename "$unit")" "$unit"
  if [ -f "$backup/agent-console-tailscale-tunnel.service" ]; then
    cp -a "$backup/agent-console-tailscale-tunnel.service" "$tunnel_unit"
  fi
  if [ -f "$backup/maintenance.py" ]; then cp -a "$backup/maintenance.py" "$state/maintenance.py"; fi
  cp -a "$backup/runner.sh" "$runner"
  # Historical aliases are evidence only; never restore a schema10 writer path.
  "$state/venv/bin/python" -B "$checkout/scripts/install-entrypoints.py" \
    --home "$HOME" --state "$state" --releases "$state/releases"

  python3 -B "$checkout/scripts/maintenance.py" service daemon-reload
  python3 -B "$checkout/scripts/maintenance.py" service restart
  if ! wait_for_health 30; then
    printf 'WARNING: restored service did not become healthy within 30 seconds.\n' >&2
  fi
}

export AGENT_CONSOLE_SOURCE_ROOT="$checkout"
# Preserve operator profile path; bundled role updates remain reviewable.

if ! "$checkout/scripts/install.sh"; then
  rollback
  printf 'FATAL: installer failed; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! release_name="$(PYTHONPATH="$checkout" "$state/venv/bin/python" - \
  "$state/releases" "$checkout" "$requested_sha" "$database_path" "$config_dir" "$state" <<'PY'
import sys
import time
from pathlib import Path

from agent_console.deployer import Deployer

releases_root, source, candidate_sha, database, config, state = map(Path, sys.argv[1:])
from agent_console.deployer import ProductionServiceRunner, ServiceConfig, DeploymentMode
runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
deployer = Deployer(releases_root, runner, source_tracker="git", prepare_runtime=True, database_path=database,
                    config_dir=config, state_dir=state)
from contextlib import redirect_stdout
with redirect_stdout(sys.stderr):
    try:
        release = deployer.create_release(source, candidate_sha=str(candidate_sha))
    except FileExistsError:
        time.sleep(1.1)
        release = deployer.create_release(source, candidate_sha=str(candidate_sha))
deployer.promote_canary(release["release_name"])
deployer.select_release(release["release_name"])
print(release["release_name"])
PY
)"; then
  rollback
  printf 'FATAL: exact-SHA release creation failed; previous release restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! "$state/venv/bin/python" -B "$checkout/scripts/install-entrypoints.py" \
  --home "$HOME" --state "$state" --releases "$state/releases"; then
  rollback
  printf 'FATAL: selected-release entrypoints unavailable.\n' >&2
  exit 1
fi

# A release-owned role path must follow current before the new service reads it.
if [ -n "${current_target:-}" ]; then
  python3 -B "$checkout/scripts/maintenance.py" redirect-profile-path "$runtime" \
    "$current_target/agent-profiles" "$state/releases/$release_name/agent-profiles"
fi

if ! python3 -B "$checkout/scripts/maintenance.py" service restart; then
  rollback
  printf 'FATAL: exact-SHA release restart failed; previous release restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! wait_for_health 30; then
  rollback
  printf 'FATAL: updated service failed health check; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

"$state/venv/bin/python" -B "$checkout/scripts/snapshot-database.py" \
  "$database_path" "$backup/agent-console-after.sqlite3"
python3 -B "$checkout/scripts/maintenance.py" snapshot-inventory "$backup/agent-console-after.sqlite3" > "$backup/sessions-after.json"
if ! python3 - "$backup/sessions-before.json" "$backup/sessions-after.json" "$checkout" <<'PY'
import json
import sys

from pathlib import Path
sys.path.insert(0, sys.argv[3])
from agent_console.maintenance import sessions_preserved
with open(sys.argv[1], encoding="utf-8") as handle:
    before = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    after = json.load(handle)
if not sessions_preserved(before, after):
    raise SystemExit(1)
PY
then
  rollback
  printf 'FATAL: durable session identities were lost; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

# Refresh exact bundled defaults only after the candidate has passed acceptance.
# Custom role text is preserved with a reviewable candidate copy alongside it.
if [ -n "${AGENT_CONSOLE_PROFILE_DIR:-}" ] && [ -n "${current_target:-}" ]; then
  python3 -B "$checkout/scripts/maintenance.py" update-profiles "$AGENT_CONSOLE_PROFILE_DIR" \
    "$current_target/agent-profiles" "$state/releases/$release_name/agent-profiles" --runtime "$runtime"
fi

printf 'Agent Console updated to %s as %s; rollback snapshot: %s\n' \
  "$requested_sha" "$release_name" "$backup"
