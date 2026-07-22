from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install.sh"


class InstallerTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp_home = Path(tempfile.mkdtemp())
        self.bin_dir = self.temp_home / "test-bin"
        self.bin_dir.mkdir()
        self._create_stubs()
        # Build minimal, predictable env. DO NOT inherit real AGENT_CONSOLE_*
        # or AGCONSOLE_* vars — the test must control defaults.
        uid = os.getuid()
        xrd = self.temp_home / "run" / str(uid)
        xrd.mkdir(parents=True, exist_ok=True)
        self.base_env = {
            "HOME": str(self.temp_home),
            "PATH": f"{self.bin_dir}:{os.defpath}",
            "USER": os.environ.get("USER", "testuser"),
            "XDG_RUNTIME_DIR": str(xrd),
            "TERM": "dumb",
        }

    def _stub(self, name: str, content: str) -> Path:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def _create_stubs(self):
        self._stub("python3", """#!/bin/bash
set -euo pipefail
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/pip" <<'PIPEOF'
#!/bin/bash
exit 0
PIPEOF
  chmod +x "$3/bin/pip"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "agent_console.cli" ]; then
  shift 2
  exec /usr/bin/python3 -c "
import json, sys
if sys.argv[1:2] == ['doctor']:
    print(json.dumps({'ok': True}))
elif sys.argv[1:3] == ['skills', 'sync']:
    print(json.dumps({'ok': True}))
elif sys.argv[1:3] == ['skills', 'doctor']:
    print(json.dumps({'ok': True, 'skills': 0, 'problems': []}))
" "$@"
fi
exit 0
""")
        self._stub("npm", "#!/bin/bash\nexit 0")
        self._stub("systemctl", "#!/bin/bash\nexit 0")
        self._stub("tmux", "#!/bin/bash\necho 'tmux 3.4'")

    def _create_tool(self, name: str) -> Path:
        return self._stub(name, "#!/bin/bash\nexit 0")

    def _run(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(self.base_env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(INSTALLER)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def _env_path(self) -> Path:
        return self.temp_home / ".config" / "agent-console" / "runtime.env"

    def _web_unit_path(self) -> Path:
        return self.temp_home / ".config" / "systemd" / "user" / "agent-console-web.service"

    def _tunnel_unit_path(self) -> Path:
        return self.temp_home / ".config" / "systemd" / "user" / "agent-console-tailscale-tunnel.service"

    # -- success cases --

    def test_custom_port(self):
        self._create_tool("codex")
        result = self._run({
            "AGENT_CONSOLE_PORT": "8080",
            "AGENT_CONSOLE_TUNNEL_PORT": "9090",
        })
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_PORT=8080", env)
        self.assertIn("AGENT_CONSOLE_TUNNEL_PORT=9090", env)

        web = self._web_unit_path().read_text()
        self.assertIn("--port 8080", web)

        tun = self._tunnel_unit_path().read_text()
        self.assertIn("9090:", tun)
        self.assertIn(":8080", tun)

    def test_default_port(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_PORT=3210", env)
        self.assertIn("AGENT_CONSOLE_TUNNEL_PORT=13210", env)

        web = self._web_unit_path().read_text()
        self.assertIn("--port 3210", web)

    def test_discovered_absolute_paths(self):
        codex_path = self._create_tool("codex")
        claude_path = self._create_tool("claude")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn(f"AGCONSOLE_CODEX_BIN={codex_path}", env)
        self.assertIn(f"AGCONSOLE_CLAUDE_BIN={claude_path}", env)

    def test_missing_optional_tools_skipped(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertNotIn("AGCONSOLE_CODEX_BIN=", env)
        self.assertNotIn("AGCONSOLE_CLAUDE_BIN=", env)
        self.assertNotIn("AGCONSOLE_OPENCODE_BIN=", env)
        self.assertNotIn("AGCONSOLE_HERMES_BIN=", env)

    def test_user_provided_bin_override_validated(self):
        tool_path = self._create_tool("my-codex")
        result = self._run({"AGCONSOLE_CODEX_BIN": str(tool_path)})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn(f"AGCONSOLE_CODEX_BIN={tool_path}", env)

    def test_user_provided_bin_override_nonexistent_fails(self):
        result = self._run({"AGCONSOLE_CODEX_BIN": "/nonexistent/codex"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an executable file", result.stderr)

    def test_custom_workspace_root_keeps_checkout_profile_default(self):
        self._create_tool("codex")
        custom_ws = self.temp_home / "custom-workspace"
        custom_ws.mkdir(parents=True)
        result = self._run({"AGENT_CONSOLE_WORKSPACE_ROOT": str(custom_ws)})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn(f"AGENT_CONSOLE_WORKSPACE_ROOT={custom_ws}", env)
        default_profile = REPO_ROOT / "agent-profiles"
        self.assertIn(f"AGENT_CONSOLE_PROFILE_DIR={default_profile}", env)

    def test_skills_variables_persisted(self):
        self._create_tool("codex")
        skills_dir = self.temp_home / "myskills"
        skills_dir.mkdir(parents=True)
        result = self._run({
            "AGCONSOLE_SKILLS_ROOT": str(skills_dir),
            "AGCONSOLE_RETAINED_SKILLS": "skill-a,skill-b",
        })
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn(f"AGCONSOLE_SKILLS_ROOT={skills_dir}", env)
        self.assertIn("AGCONSOLE_RETAINED_SKILLS=skill-a,skill-b", env)

    def test_killmode_process_in_web_unit(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        web = self._web_unit_path().read_text()
        self.assertIn("KillMode=process", web)

    def test_canonical_and_legacy_socket_lines(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_TMUX_SOCKET_PATH=/run/user", env)
        self.assertIn("AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH=/tmp/tmux-", env)

    def test_no_state_deletion(self):
        self._create_tool("codex")
        state_dir = self.temp_home / ".local" / "share" / "agent-console"
        state_dir.mkdir(parents=True, exist_ok=True)
        existing = state_dir / "existing-file"
        existing.write_text("keep me")
        launchers = state_dir / "launchers"
        launchers.mkdir(parents=True, exist_ok=True)
        launcher_file = launchers / "my-launcher.sh"
        launcher_file.write_text("#!/bin/bash")

        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

        self.assertTrue(existing.is_file(), "existing state files must be preserved")
        self.assertTrue(launcher_file.is_file(), "existing launchers must be preserved")

    # -- failure cases --

    def test_profile_dir_missing_fails(self):
        result = self._run({"AGENT_CONSOLE_PROFILE_DIR": str(self.temp_home / "no-profiles")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)

    def test_profile_dir_empty_fails(self):
        empty_dir = self.temp_home / "empty-profiles"
        empty_dir.mkdir(parents=True)
        result = self._run({"AGENT_CONSOLE_PROFILE_DIR": str(empty_dir)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)

    def test_port_non_numeric_fails(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_PORT": "abc"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)

    def test_port_out_of_range_fails(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_PORT": "65536"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)

    def test_tunnel_port_non_numeric_fails(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_TUNNEL_PORT": "x99"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)

    def test_units_not_written_on_validation_failure(self):
        result = self._run({"AGENT_CONSOLE_PROFILE_DIR": str(self.temp_home / "nonexistent")})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self._web_unit_path().is_file(),
                         "web unit must not be written on failure")
        self.assertFalse(self._tunnel_unit_path().is_file(),
                         "tunnel unit must not be written on failure")

    def test_doctor_called_after_installation(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("ok", result.stdout.lower())

    def test_skills_steps_printed(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("agentctl skills doctor", result.stdout)

    def test_env_file_is_mode_600(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env_path = self._env_path()
        mode = oct(env_path.stat().st_mode)[-3:]
        self.assertEqual(mode, "600", f"expected mode 600, got {mode}")


if __name__ == "__main__":
    unittest.main()
