#!/usr/bin/env bash
set -euo pipefail

opencode_bin="${1:-${AGCONSOLE_OPENCODE_BIN:-}}"
if [[ -z "$opencode_bin" ]]; then
  opencode_bin="$(command -v opencode || true)"
fi
if [[ -z "$opencode_bin" || ! -x "$opencode_bin" ]]; then
  echo "OpenCode binary not found; pass its path as argument 1" >&2
  exit 2
fi

installed_version="$($opencode_bin --version)"
if [[ "$installed_version" != *"1.18.30"* ]]; then
  echo "fixture is verified only for OpenCode 1.18.30 (found: $installed_version)" >&2
  exit 2
fi

fixture_dir="$(mktemp -d /tmp/agent-console-opencode-skills.XXXXXX)"
trap 'rm -rf -- "$fixture_dir"' EXIT
fixture_home="$fixture_dir/home"
fixture_project="$fixture_dir/project"

make_skill() {
  local path="$1"
  local name="$2"
  local description="$3"
  mkdir -p "$path"
  printf '%s\n' \
    '---' \
    "name: $name" \
    "description: $description" \
    '---' \
    '# Fixture' > "$path/SKILL.md"
}

make_skill "$fixture_home/.claude/skills/legacy" "console81-legacy-global" "legacy global"
make_skill "$fixture_home/.agents/skills/agents" "console81-agents-global" "agents global"
make_skill "$fixture_home/.config/opencode/skills/group/nested" "console81-native-nested" "native nested"
make_skill "$fixture_project/.claude/skills/claude" "console81-claude-project" "claude project"
make_skill "$fixture_project/.agents/skills/agents" "console81-agents-project" "agents project"
make_skill "$fixture_project/.opencode/skills/native" "console81-native-project" "native project"
make_skill "$fixture_home/.claude/skills/shared" "console81-shared" "shared legacy"
make_skill "$fixture_home/.config/opencode/skills/shared" "console81-shared" "shared native global"
make_skill "$fixture_project/.opencode/skills/shared" "console81-shared" "shared native project"

git init -q "$fixture_project"
output_file="$fixture_dir/discovery.json"
(
  cd "$fixture_project"
  HOME="$fixture_home" \
  XDG_CONFIG_HOME="$fixture_home/.config" \
  XDG_DATA_HOME="$fixture_dir/xdg-data" \
  XDG_CACHE_HOME="$fixture_dir/xdg-cache" \
  XDG_STATE_HOME="$fixture_dir/xdg-state" \
  OPENCODE_DISABLE_MODELS_FETCH=true \
  OPENCODE_CONFIG_CONTENT='{"plugin":[]}' \
  timeout 45s "$opencode_bin" debug skill --pure > "$output_file"
)

for skill_id in \
  console81-legacy-global \
  console81-agents-global \
  console81-native-nested \
  console81-claude-project \
  console81-agents-project \
  console81-native-project; do
  grep -Fq "$skill_id" "$output_file" || {
    echo "missing discovered fixture: $skill_id" >&2
    exit 1
  }
done
grep -Fq "shared native project" "$output_file" || {
  echo "project-native duplicate did not win the verified precedence fixture" >&2
  exit 1
}

echo "OpenCode 1.18.30 native skill discovery fixture passed"
