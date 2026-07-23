from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install.sh"
UPDATER = REPO_ROOT / "scripts" / "update.sh"


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
        self._stub("systemctl", "#!/bin/bash\necho \"SYSTEMCTL: $@\" >&2\nexit 0")
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

    def _runner_path(self) -> Path:
        return self.temp_home / ".local" / "share" / "agent-console" / "runner.sh"

    def _stub_runner_uvicorn(self) -> None:
        uvicorn = self.temp_home / ".local" / "share" / "agent-console" / "venv" / "bin" / "uvicorn"
        uvicorn.write_text('#!/bin/bash\nprintf "%s|%s|%s\\n" "$PWD" "$PYTHONPATH" "$*"\n')
        uvicorn.chmod(0o755)

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

        runner = self.temp_home / ".local" / "share" / "agent-console" / "runner.sh"
        content = runner.read_text()
        self.assertIn("runner_port=\"8080\"", content)

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

        runner = self.temp_home / ".local" / "share" / "agent-console" / "runner.sh"
        content = runner.read_text()
        self.assertIn("runner_port=\"3210\"", content)

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

    def test_systemctl_sequence_invoked(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        calls = [line for line in result.stderr.splitlines() if line.startswith("SYSTEMCTL:")]
        self.assertGreaterEqual(len(calls), 3, f"expected >=3 systemctl calls, got {calls}")
        self.assertIn("daemon-reload", calls[0])
        self.assertIn("enable", calls[1])
        self.assertIn("restart", calls[2])

    def test_validation_failure_invokes_no_systemctl(self):
        result = self._run({"AGENT_CONSOLE_PROFILE_DIR": str(self.temp_home / "nonexistent")})
        self.assertNotEqual(result.returncode, 0)
        calls = [line for line in result.stderr.splitlines() if line.startswith("SYSTEMCTL:")]
        self.assertEqual(len(calls), 0, f"expected no systemctl calls on failure, got {calls}")

    def test_user_provided_bin_override_relative_rejected(self):
        self._create_tool("codex")
        result = self._run({"AGCONSOLE_CODEX_BIN": "relative/path/codex"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)

    def test_env_file_is_mode_600(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env_path = self._env_path()
        mode = oct(env_path.stat().st_mode)[-3:]
        self.assertEqual(mode, "600", f"expected mode 600, got {mode}")

    # -- Pass 2 deployer/runner tests --

    def test_source_root_persisted(self):
        self._create_tool("codex")
        custom_source = self.temp_home / "custom-source"
        custom_source.mkdir()
        result = self._run({"AGENT_CONSOLE_SOURCE_ROOT": str(custom_source)})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn(f"AGENT_CONSOLE_SOURCE_ROOT={custom_source}", env)

    def test_relative_source_root_fails(self):
        result = self._run({"AGENT_CONSOLE_SOURCE_ROOT": "relative/source"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be an absolute path", result.stderr)

    def test_source_root_defaults_to_checkout(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_SOURCE_ROOT=", env)

    def test_bind_host_persisted(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_BIND_HOST": "0.0.0.0"})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_BIND_HOST=0.0.0.0", env)

    def test_canary_bind_and_port_persisted(self):
        self._create_tool("codex")
        result = self._run({
            "AGENT_CONSOLE_CANARY_BIND": "192.168.1.1",
            "AGENT_CONSOLE_CANARY_PORT": "9999",
        })
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_CANARY_BIND=192.168.1.1", env)
        self.assertIn("AGENT_CONSOLE_CANARY_PORT=9999", env)

    def test_tunnel_host_persisted(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_TUNNEL_HOST": "jump.example"})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_TUNNEL_HOST=jump.example", env)

    def test_deployment_mode_persisted(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_DEPLOYMENT_MODE": "staging"})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_DEPLOYMENT_MODE=staging", env)

    def test_deployment_mode_default_disabled(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_DEPLOYMENT_MODE=disabled", env)

    def test_invalid_deployment_mode_fails(self):
        result = self._run({"AGENT_CONSOLE_DEPLOYMENT_MODE": "invalid"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)
        self.assertIn("DEPLOYMENT_MODE", result.stderr)

    def test_custom_port_3220(self):
        self._create_tool("codex")
        result = self._run({"AGENT_CONSOLE_PORT": "3220"})
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        env = self._env_path().read_text()
        self.assertIn("AGENT_CONSOLE_PORT=3220", env)

    def test_runner_script_generated(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        runner = self.temp_home / ".local" / "share" / "agent-console" / "runner.sh"
        self.assertTrue(runner.is_file(), "runner.sh must exist")
        mode = oct(runner.stat().st_mode)[-3:]
        self.assertEqual(mode, "700", f"expected mode 700, got {mode}")
        content = runner.read_text()
        self.assertIn("PYTHONPATH", content)
        self.assertIn("uvicorn", content)
        self.assertIn("runner_root", content)
        self.assertIn("runner_bind", content)
        self.assertIn("runner_port", content)

    def test_runner_falls_back_to_bootstrap(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        runner = self.temp_home / ".local" / "share" / "agent-console" / "runner.sh"
        content = runner.read_text()
        # When releases/current does not exist, runner falls back to bootstrap root
        self.assertIn("runner_root", content)
        self.assertIn('PYTHONPATH="$runner_root"', content)

    def test_runner_executes_bootstrap_without_current_release(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self._stub_runner_uvicorn()
        executed = subprocess.run(
            [str(self._runner_path())], capture_output=True, text=True, env=self.base_env,
        )
        self.assertEqual(executed.returncode, 0, msg=executed.stderr)
        self.assertTrue(executed.stdout.startswith(f"{REPO_ROOT}|{REPO_ROOT}|"))

    def test_runner_selects_only_complete_contained_release(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self._stub_runner_uvicorn()
        releases = self.temp_home / ".local" / "share" / "agent-console" / "releases"
        release = releases / "release-test"
        (release / "agent_console").mkdir(parents=True)
        for relative in (
            "node_modules/@xterm/xterm/lib/xterm.mjs",
            "node_modules/@xterm/xterm/css/xterm.css",
            "node_modules/@xterm/addon-fit/lib/addon-fit.mjs",
        ):
            path = release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (releases / "current").symlink_to("release-test")
        executed = subprocess.run(
            [str(self._runner_path())], capture_output=True, text=True, env=self.base_env,
        )
        self.assertEqual(executed.returncode, 0, msg=executed.stderr)
        self.assertTrue(executed.stdout.startswith(f"{release}|{release}|"))

        (release / "node_modules/@xterm/addon-fit/lib/addon-fit.mjs").unlink()
        fallback = subprocess.run(
            [str(self._runner_path())], capture_output=True, text=True, env=self.base_env,
        )
        self.assertEqual(fallback.returncode, 0, msg=fallback.stderr)
        self.assertTrue(fallback.stdout.startswith(f"{REPO_ROOT}|{REPO_ROOT}|"))

    def test_web_unit_uses_runner(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        web = self._web_unit_path().read_text()
        runner_path = str(self.temp_home / ".local" / "share" / "agent-console" / "runner.sh")
        self.assertIn(runner_path, web)
        self.assertNotIn("WorkingDirectory=", web,
                         "unit must not set WorkingDirectory directly")
        self.assertNotIn("--host ", web,
                         "unit must not pass host directly to uvicorn")
        self.assertIn("KillMode=process", web)

    def test_canary_port_invalid_fails(self):
        result = self._run({"AGENT_CONSOLE_CANARY_PORT": "abc"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)
        self.assertIn("CANARY_PORT", result.stderr)

    def test_canary_port_out_of_range_fails(self):
        result = self._run({"AGENT_CONSOLE_CANARY_PORT": "65536"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL", result.stderr)
        self.assertIn("65536", result.stderr)

    def test_reinstall_restart_systemctl_sequence(self):
        self._create_tool("codex")
        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        # Simulate reinstall: rerun installer
        result2 = self._run()
        self.assertEqual(result2.returncode, 0, msg=result2.stderr + result2.stdout)
        calls = [line for line in result2.stderr.splitlines() if line.startswith("SYSTEMCTL:")]
        self.assertGreaterEqual(len(calls), 3, f"expected >=3 systemctl calls on reinstall, got {calls}")
        self.assertIn("daemon-reload", calls[0])
        self.assertIn("enable", calls[1])
        self.assertIn("restart", calls[2])


class UpdateScriptTests(unittest.TestCase):
    def test_updater_is_executable_and_valid_bash(self):
        self.assertTrue(os.access(UPDATER, os.X_OK))
        result = subprocess.run(["bash", "-n", str(UPDATER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_updater_rejects_missing_or_invalid_sha(self):
        for args in ([], ["not-a-sha"]):
            result = subprocess.run(
                [str(UPDATER), *args],
                capture_output=True,
                text=True,
                env={"HOME": tempfile.mkdtemp(), "PATH": os.defpath},
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("40-character-approved-main-sha", result.stderr)

    def test_updater_contains_required_safety_gates(self):
        text = UPDATER.read_text(encoding="utf-8")
        for expected in (
            "flock -n",
            "fetch --quiet origin main",
            "requested SHA",
            "source.backup(target)",
            "sessions-before.json",
            "sessions-after.json",
            "rollback()",
            "systemctl --user restart agent-console-web.service",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
