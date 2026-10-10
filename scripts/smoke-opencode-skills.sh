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

installed_version="$("$opencode_bin" --version)"
if [[ "$installed_version" != "1.18.30" && "$installed_version" != "1.18.31" ]]; then
  echo "fixture supports OpenCode 1.18.30/1.18.31 (found: $installed_version)" >&2
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
make_skill "$fixture_dir/snapshot/console81-selected" "console81-selected" "Console selected snapshot"
guide_root="${2:-}"
if [[ -n "$guide_root" ]]; then
  for guide in "$guide_root"/*; do
    [[ -f "$guide/SKILL.md" ]] || continue
    cp -R -- "$guide" "$fixture_dir/snapshot/"
  done
fi
config_content="$(python3 -c 'import json,sys; print(json.dumps({"plugin":[],"skills":[sys.argv[1]]}))' "$fixture_dir/snapshot")"

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
  OPENCODE_CONFIG_CONTENT="$config_content" \
  timeout 45s "$opencode_bin" debug skill --pure > "$output_file"
)

for skill_id in \
  console81-legacy-global \
  console81-agents-global \
  console81-native-nested \
  console81-claude-project \
  console81-agents-project \
  console81-selected \
  console81-native-project; do
  grep -Fq "$skill_id" "$output_file" || {
    echo "missing discovered fixture: $skill_id" >&2
    exit 1
  }
done
python3 - "$output_file" "$guide_root" <<'PY'
import json
import sys
from pathlib import Path

skills = json.load(open(sys.argv[1], encoding="utf-8"))
shared = [skill for skill in skills if skill.get("name") == "console81-shared"]
assert len(shared) == 1, "duplicate fixture must discover one native winner"
assert shared[0]["description"] in {"shared legacy", "shared native global", "shared native project"}
if sys.argv[2]:
    guides = sorted(path.parent.name for path in Path(sys.argv[2]).glob('*/SKILL.md'))
    assert set(guides) <= {skill['name'] for skill in skills}, 'missing guide packages'
    print('Native guide discovery: ' + ', '.join(guides))
print("Observed duplicate winner: " + shared[0]["description"] + "; precedence remains unverified")
PY

echo "OpenCode $installed_version native skill discovery fixture passed"
