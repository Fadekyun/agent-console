"""Console/broker integration using only temporary state and fake upstreams."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from agent_console.auth import AuthRegistry
from agent_console.broker_client import BROKER_URL, CAPABILITY, BrokerClient, BrokerUnavailable, PROTECTED_NAMES
from agent_console.broker_launch import argv_for, opencode_config, refresh_codex, refresh_native
from agent_console.commandcode import BASE_URL, DEFAULT_MODEL
from agent_console.config import Settings
from agent_console.credential_broker import BrokerStore, create_app
from agent_console.environment import EnvironmentStore, read_private
from agent_console.environment_api import environment_routes
from agent_console.manager import SessionManager
from agent_console.models import ModelCatalogue
from agent_console.providers import TOOL_BINARIES, LaunchSpec, provider_adapter
from agent_console.tmux import TmuxSession

REPO = Path(__file__).resolve().parents[1]
ADMIN = "synthetic-integration-admin-sentinel"


class BrokerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.host_env = {"PATH": os.environ["PATH"], "HOME": str(self.root / "home")}
        self.env_patch = patch.dict(os.environ, self.host_env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        Path(self.host_env["HOME"]).mkdir()
        self.store = BrokerStore(self.root / "private-broker")
        self.upstream_requests = []
        self.upstream_payload = {"ok": True}
        for name in ["httpx", "agent_console.credential_broker"]:
            logger_patch = patch.object(logging.getLogger(name), "level", logging.WARNING)
            logger_patch.start()
            self.addCleanup(logger_patch.stop)
        def upstream(request):
            self.upstream_requests.append(request)
            return httpx.Response(200, json=self.upstream_payload)
        app = create_app(self.store, ADMIN, transport=httpx.MockTransport(upstream))
        self.data_client = TestClient(app).__enter__()
        self.addCleanup(lambda: self.data_client.__exit__(None, None, None))
        self.admin_file = self.root / "broker-admin-token"
        self.admin_file.write_text(ADMIN)
        self.admin_file.chmod(0o600)
        def forward(request):
            response = self.data_client.request(request.method, str(request.url), content=request.content,
                                                headers=dict(request.headers))
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)
        self.broker = BrokerClient("http://127.0.0.1:8792", self.admin_file, transport=httpx.MockTransport(forward))
        self.environment = EnvironmentStore(self.root / "console-config", broker=self.broker)

    def manager(self):
        profiles = self.root / "profiles"
        profiles.mkdir(exist_ok=True)
        (profiles / "general.md").write_text("# General\n")
        settings = Settings(workspace_root=self.root, state_dir=self.root / "console-state",
                            database_path=self.root / "console-state/db.sqlite", profile_dir=profiles,
                            handoff_dir=self.root / "handoffs", worktree_root=self.root / "worktrees",
                            tmux_socket="broker-tests-" + str(os.getpid()) + "-" + self.root.name)
        with patch.object(BrokerClient, "configured", return_value=self.broker):
            manager = SessionManager(settings)
        data = manager.auth._read()
        for tool in ["pi", "hermes"]:
            data["contexts"][tool]["commandcode-main"] = {
                "provider": "commandcode", "kind": "api-key", "secret_ref": "commandcode-main",
                "base_url": BASE_URL, "model": DEFAULT_MODEL, "models": [DEFAULT_MODEL],
                "enabled": True, "verified": True,
            }
            data["defaults"][tool] = "commandcode-main"
        data["contexts"]["claude"]["default"].update(enabled=True, verified=True)
        manager.auth._write(data)
        for tool in ["codex", "codex-pro"]:
            (manager.auth.codex_home("default", tool=tool) / "auth.json").write_text("{}")
        return manager


class BrokerEnvironmentIntegrationTests(BrokerFixture):
    def test_protected_ui_write_is_only_persisted_in_broker_and_not_a_refresh(self):
        manager = self.manager()
        app = FastAPI()
        def owner(request: Request):
            if request.headers.get("owner") != "yes":
                raise HTTPException(403)
            return SimpleNamespace(actor="owner", access_surface="test")
        app.include_router(environment_routes(manager, owner, self.root))
        with TestClient(app) as client:
            headers = {"owner": "yes"}
            before = manager.environment.revision()
            response = client.put("/api/environment/CMD_API_KEY", json={"value": "upstream-secret-sentinel"}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("upstream-secret-sentinel", response.text)
            row = next(e for e in response.json()["entries"] if e["name"] == "CMD_API_KEY")
            self.assertTrue(row["protected"])
            self.assertTrue(row["immediate"])
            self.assertEqual(manager.environment.revision(), before)
            self.assertEqual(response.json()["broker_revision"], 1)
            self.assertFalse(manager.environment.path.exists())
            self.assertNotIn(b"upstream-secret-sentinel", manager.database.path.read_bytes())
            self.assertIn("upstream-secret-sentinel", self.store.path.read_text())
            self.assertEqual(client.put("/api/environment/CMD_API_KEY", json={"value": "denied-sentinel"}).status_code, 403)

    def test_scopes_replacement_disable_suppress_and_delete_apply_to_existing_token(self):
        self.environment.put("APP_MODE", value="ordinary")
        self.environment.put("N8N_MCP_TOKEN", value="global-sentinel")
        values, revision = self.environment.resolve("one", baseline={}, account_ref="account")
        token = values[CAPABILITY]
        headers = {"Authorization": "Bearer " + token}
        def call_expected(value):
            response = self.data_client.post("/mcp/n8n", headers=headers)
            if value is None:
                self.assertEqual(response.status_code, 503)
            else:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.upstream_requests[-1].headers["authorization"], "Bearer " + value)
        call_expected("global-sentinel")
        self.environment.put("N8N_MCP_TOKEN", value="project-sentinel", project_id="one")
        call_expected("project-sentinel")
        self.environment.put("N8N_MCP_TOKEN", state="disabled", project_id="one")
        call_expected("global-sentinel")
        self.environment.put("N8N_MCP_TOKEN", state="suppressed", project_id="one")
        call_expected(None)
        self.environment.delete("N8N_MCP_TOKEN", project_id="one")
        call_expected("global-sentinel")
        self.environment.delete("N8N_MCP_TOKEN")
        call_expected(None)
        self.assertEqual(self.environment.resolve("one", baseline={}, account_ref="account")[0][CAPABILITY], token)
        self.assertEqual(self.environment.revision("one"), revision)
        self.assertEqual(read_private(self.environment.path)["scopes"]["global"]["APP_MODE"]["value"], "ordinary")
        self.assertNotIn("sentinel", self.environment.path.read_text())

    def test_resolved_environment_drops_protected_values_and_admin_control(self):
        baseline = {name: "ambient-" + name for name in PROTECTED_NAMES}
        baseline.update(AGENT_CONSOLE_BROKER_ADMIN_FILE=str(self.admin_file),
                        AGENT_CONSOLE_BROKER_ADMIN_TOKEN=ADMIN, APP_MODE="keep")
        resolved, _ = self.environment.resolve(baseline=baseline)
        self.assertEqual(resolved["APP_MODE"], "keep")
        self.assertFalse(PROTECTED_NAMES.intersection(resolved))
        self.assertNotIn("AGENT_CONSOLE_BROKER_ADMIN_FILE", resolved)
        self.assertNotIn("AGENT_CONSOLE_BROKER_ADMIN_TOKEN", resolved)
        self.assertIn(CAPABILITY, resolved)
        self.assertNotIn(ADMIN, json.dumps(resolved))
        self.assertEqual(self.broker.describe()["routes"], [])

    def test_no_legacy_credentials_are_read_for_protected_accounts_and_readiness_uses_broker(self):
        manager = self.manager()
        for tool in ["pi", "hermes", "opencode"]:
            account = "openrouter-main" if tool == "opencode" else "commandcode-main"
            context = manager.auth.get_context(tool, account)
            with self.subTest(tool=tool):
                self.assertEqual(provider_adapter(tool, manager.auth).secret_files(context), [])
                with self.assertRaisesRegex(ValueError, "Environment"):
                    self.broker.require_model(context)
                name = "OPENROUTER_API_KEY" if tool == "opencode" else "CMD_API_KEY"
                self.environment.put(name, value="provider-sentinel")
                self.broker.require_model(context)
                self.environment.delete(name)

    def test_outage_fails_closed_without_protected_local_write_or_raw_fallback(self):
        self.broker.transport = httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "fake outage"}))
        with self.assertRaises(BrokerUnavailable):
            self.environment.put("CMD_API_KEY", value="upstream-secret-sentinel")
        with self.assertRaises(BrokerUnavailable):
            self.environment.resolve(baseline={"CMD_API_KEY": "host-raw-sentinel"})
        self.assertFalse(self.environment.path.exists())

    def test_child_data_context_does_not_enable_control_client(self):
        with patch.dict(os.environ, {BROKER_URL: self.broker.url, CAPABILITY: "synthetic-data-only"}):
            child = BrokerClient.configured()
            self.assertIsNotNone(child)
            self.assertIsNone(child.admin_file)
            with self.assertRaises(BrokerUnavailable):
                child.context()
        with patch.dict(os.environ, {BROKER_URL: self.broker.url}):
            with self.assertRaises(BrokerUnavailable):
                BrokerClient.configured()

    def test_fresh_commandcode_provisioning_uses_only_broker_catalogue(self):
        from agent_console.commandcode import provision_broker
        config = self.root / "fresh-config"
        self.store.put("CMD_API_KEY", value="upstream-model-sentinel")
        self.upstream_payload = {"data": [{"id": DEFAULT_MODEL, "context_length": 64000}]}
        real_client = httpx.Client
        def fake_client(**kwargs):
            kwargs.setdefault("transport", self.broker.transport)
            return real_client(**kwargs)
        with patch("httpx.Client", side_effect=fake_client):
            result = provision_broker(config, self.broker)
        self.assertEqual(result["credential_storage"], "broker")
        self.assertEqual(result["catalogue_models"], 1)
        self.assertEqual(self.upstream_requests[-1].url.path, "/provider/v1/models")
        self.assertEqual(self.upstream_requests[-1].headers["authorization"], "Bearer upstream-model-sentinel")
        registry = AuthRegistry(config, broker=self.broker)
        for tool in ["pi", "hermes"]:
            context = registry.get_context(tool, require_ready=True)
            self.assertTrue(context["verified"])
            self.assertEqual(context["model"], DEFAULT_MODEL)
            self.assertEqual(context["model_catalogue"][0]["context_length"], 64000)
        self.assertEqual(list((config / "secrets.d").iterdir()), [])
        for path in config.rglob("*"):
            if path.is_file():
                self.assertNotIn("upstream-model-sentinel", path.read_text())

    def test_malformed_success_responses_are_generic_broker_failures(self):
        for response in [httpx.Response(200, text="untrusted-secret-sentinel"),
                         httpx.Response(200, json=[]), httpx.Response(200, json={"routes": []}),
                         httpx.Response(200, json={"token": "bad\nheader", "routes": []})]:
            with self.subTest(body_type=response.headers.get("content-type")):
                self.broker.transport = httpx.MockTransport(lambda _, response=response: response)
                with self.assertRaises(BrokerUnavailable) as exc:
                    self.broker.context()
                self.assertNotIn("untrusted-secret-sentinel", str(exc.exception))


class BrokerHarnessIntegrationTests(BrokerFixture):
    def test_real_adapter_launcher_matrix_has_references_without_upstream_keys(self):
        manager = self.manager()
        for name in PROTECTED_NAMES:
            manager.environment.put(name, value="upstream-sentinel-" + name)
        keys = [*sorted(PROTECTED_NAMES), CAPABILITY, BROKER_URL, "AGENT_CONSOLE_BROKER_ADMIN_FILE", "OPENCODE_CONFIG_CONTENT"]
        executable = self.root / "fake-native"
        executable.write_text("#!" + sys.executable + "\nimport os,json,sys\n"
                              'if "--help" in sys.argv: print("--approve-for-me"); sys.exit(0)\n'
                              "print(json.dumps({k:os.getenv(k) for k in " + repr(keys) + "}))\n")
        executable.chmod(0o700)
        selected_skills = self.root / "selected-skills"
        selected_skills.mkdir()
        with patch.dict(TOOL_BINARIES, {tool: executable for tool in TOOL_BINARIES}):
            for tool in ["codex", "codex-pro", "claude", "opencode", "pi", "hermes"]:
                with self.subTest(tool=tool):
                    name = "broker-" + tool
                    kwargs = {"auth_context": "openrouter-main", "model": "openrouter/test/model"} if tool == "opencode" else {}
                    spec = manager._launch_spec(tool, "general", self.root, None, session_name=name, session_id=name, **kwargs)
                    context = manager.auth.get_context(tool, kwargs.get("auth_context"))
                    overlay = manager._create_session_tool_overlay(name, tool, context, selected_skills,
                        session_id=name, resolved_environment=spec.resolved_environment)
                    launcher = manager._write_launcher(name, name, spec, overlay_env=overlay)
                    process = subprocess.run(["bash", str(launcher)], capture_output=True, text=True, check=True, timeout=10,
                                             env={**self.host_env, "CMD_API_KEY": "tmux-ambient-sentinel"})
                    actual = json.loads(process.stdout)
                    self.assertTrue(actual[CAPABILITY])
                    self.assertEqual(actual[BROKER_URL], self.broker.url)
                    for key in PROTECTED_NAMES:
                        self.assertIsNone(actual[key])
                    self.assertIsNone(actual["AGENT_CONSOLE_BROKER_ADMIN_FILE"])
                    snapshot = manager.settings.state_dir / "environment-launches" / (name + ".json")
                    self.assertNotIn("upstream-sentinel", snapshot.read_text())
                    self.assertNotIn("tmux-ambient-sentinel", snapshot.read_text())
                    self.assertNotIn(actual[CAPABILITY], launcher.read_text())
                    self.assertEqual(spec.secret_files, [])
                    if tool in {"codex", "codex-pro"}:
                        overrides = [spec.argv[i + 1] for i, arg in enumerate(spec.argv[:-1]) if arg == "-c" and spec.argv[i + 1].startswith("mcp_servers.")]
                        self.assertEqual(len(overrides), 4)
                        self.assertTrue(all("bearer_token_env_var" in value and CAPABILITY in value for value in overrides))
                    elif tool == "claude":
                        config = json.loads(Path(spec.argv[spec.argv.index("--mcp-config") + 1]).read_text())
                        self.assertEqual(len(config["mcpServers"]), 4)
                    elif tool == "opencode":
                        config = json.loads(actual["OPENCODE_CONFIG_CONTENT"])
                        self.assertTrue(config["instructions"])
                        self.assertTrue(config["skills"])
                        self.assertEqual(len(config["mcp"]), 4)
                        self.assertEqual(config["provider"]["openrouter"]["options"]["baseURL"], self.broker.url + "/model/openrouter/v1")
                    elif tool == "pi":
                        home = Path(spec.environment["PI_CODING_AGENT_DIR"])
                        provider = json.loads((home / "models.json").read_text())["providers"]["commandcode"]
                        self.assertEqual(provider["baseUrl"], self.broker.url + "/model/commandcode/v1")
                        self.assertEqual(provider["apiKey"], "$" + CAPABILITY)
                        self.assertEqual(json.loads((home / "mcp.json").read_text())["mcpServers"]["n8n"]["headers"]["Authorization"], "Bearer ${" + CAPABILITY + "}")
                    else:
                        config = json.loads((Path(spec.environment["HERMES_HOME"]) / "config.yaml").read_text())
                        self.assertEqual(config["model"]["api_key"], "${" + CAPABILITY + "}")
                        self.assertEqual(len(config["mcp_servers"]), 4)
        for path in manager.settings.state_dir.rglob("*"):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(b"upstream-sentinel", path.read_bytes(), str(path))

    def test_restart_refreshes_native_urls_and_preserves_unrelated_settings(self):
        manager = self.manager()
        resolved, _ = manager.environment.resolve(baseline={})
        for tool in ["pi", "hermes"]:
            context = manager.auth.get_context(tool)
            context_path = self.root / (tool + ".md")
            context_path.write_text("test")
            spec = provider_adapter(tool, manager.auth).build_launch_spec(
                context=context, context_path=context_path, model=None, role="general", profile="general",
                cwd=self.root, read_only=False, agent_mode=None, resolved_environment=resolved)
            home = Path(spec.environment["PI_CODING_AGENT_DIR" if tool == "pi" else "HERMES_HOME"])
            path = home / ("mcp.json" if tool == "pi" else "config.yaml")
            data = json.loads(path.read_text())
            table = "mcpServers" if tool == "pi" else "mcp_servers"
            data[table]["unrelated"] = {"url": "https://fixture.invalid/mcp"}
            data["custom_setting"] = "preserve"
            path.write_text(json.dumps(data))
            refresh_native(tool, {**resolved, **spec.environment, BROKER_URL: "http://127.0.0.1:8888"})
            after = json.loads(path.read_text())
            self.assertEqual(after[table]["unrelated"]["url"], "https://fixture.invalid/mcp")
            self.assertEqual(after["custom_setting"], "preserve")
            self.assertEqual(after[table]["n8n"]["url"], "http://127.0.0.1:8888/mcp/n8n")

    def test_live_pi_restart_reuses_broker_token_and_outage_preserves_process(self):
        manager = self.manager()
        def stop_isolated_tmux():
            pids = manager.tmux.pane_pids("broker-restart-pi")
            manager.tmux.run("kill-server", check=False)
            # tmux closes asynchronously; let its shell finish writing history
            # before TemporaryDirectory removes this test's private HOME.
            for _ in range(100):
                if not any(Path("/proc", str(pid)).exists() for pid in pids):
                    break
                time.sleep(0.02)
        self.addCleanup(stop_isolated_tmux)
        manager.environment.put("CMD_API_KEY", value="first-model-sentinel")
        executable = self.root / "inert-pi"
        executable.write_text("#!" + sys.executable + "\nimport time\nprint('BROKER_FAKE_READY', flush=True)\ntime.sleep(90)\n")
        executable.chmod(0o700)
        name = "broker-restart-pi"
        project = manager.create_project(name="Project exec review")
        # Keep the lifecycle test independent of the host tmux version's format
        # escaping: some versions sanitize TAB fields into underscores. Observe
        # the actual temporary socket and use a known live record for the manager.
        def observed_sessions():
            if not manager.tmux.exists(name):
                return {}
            return {name: ("canonical", TmuxSession(name, int(time.time()), int(time.time()), 0, 1, "python"))}
        with patch.dict(TOOL_BINARIES, {"pi": executable}), patch("agent_console.manager.require_launch_resources"), \
                patch.object(manager, "_live_sessions", side_effect=observed_sessions):
            session = manager.create(tool="pi", profile="general", name=name, project_id=project["id"])
            for _ in range(60):
                output, _ = manager.tmux.capture(name)
                if "BROKER_FAKE_READY" in output:
                    break
                time.sleep(0.05)
            self.assertIn("BROKER_FAKE_READY", output)
            path = manager.settings.state_dir / "environment-launches" / (session["id"] + ".json")
            first = read_private(path)
            token = first["environment"][CAPABILITY]
            # Older snapshots/exports must not reintroduce raw keys on restart.
            first["environment"]["CMD_API_KEY"] = "legacy-model-sentinel"
            first["launcher_keys"] += ["CMD_API_KEY", "AGENT_CONSOLE_BROKER_ADMIN_FILE"]
            first["secret_files"] = [str(self.root / "old-credential.env")]
            path.write_text(json.dumps(first))
            launcher_path = Path(session["launcher_path"])
            launcher_path.write_text(launcher_path.read_text().replace("\nexec ",
                "\nexport CMD_API_KEY=legacy-model-sentinel\nexport AGENT_CONSOLE_BROKER_ADMIN_FILE=/private/old-admin\nexec "))
            native = Path(first["environment"]["PI_CODING_AGENT_DIR"]) / "mcp.json"
            config = json.loads(native.read_text())
            config["mcpServers"]["unrelated"] = {"url": "https://fixture.invalid/mcp"}
            native.write_text(json.dumps(config))
            manager.environment.put("CMD_API_KEY", value="second-model-sentinel")
            manager.restart(session["tmux_name"])
            after = read_private(path)
            self.assertEqual(after["environment"][CAPABILITY], token)
            self.assertNotIn("model-sentinel", path.read_text())
            self.assertNotIn("CMD_API_KEY", after["launcher_keys"])
            self.assertNotIn("AGENT_CONSOLE_BROKER_ADMIN_FILE", after["launcher_keys"])
            self.assertEqual(after["secret_files"], [])
            self.assertNotIn("legacy-model-sentinel", launcher_path.read_text())
            self.assertIn("Project exec review", launcher_path.read_text())
            subprocess.run(["bash", "-n", str(launcher_path)], check=True, capture_output=True)
            self.assertIn("unrelated", json.loads(native.read_text())["mcpServers"])
            self.assertTrue(manager.tmux.exists(session["tmux_name"]))
            self.broker.transport = httpx.MockTransport(lambda _: httpx.Response(503))
            with patch.object(manager.tmux, "restart") as stop:
                with self.assertRaises(BrokerUnavailable):
                    manager.restart(session["tmux_name"])
                stop.assert_not_called()
            self.assertTrue(manager.tmux.exists(session["tmux_name"]))

    def test_codex_config_refresh_removes_old_nested_auth_and_preserves_other_servers(self):
        import tomllib
        path = self.root / "config.toml"
        path.write_text('# Keep this comment\nmodel="pinned-model"\n'
                        '[mcp_servers.n8n]\nurl="http://old.invalid/mcp"\n'
                        '[mcp_servers.n8n.http_headers]\nAuthorization="Bearer old-synthetic-token"\n'
                        '[mcp_servers.unrelated]\nurl="http://other.invalid/mcp"\n')
        env = {BROKER_URL: self.broker.url, CAPABILITY: "broker-token"}
        refresh_codex(self.root, env)
        text = path.read_text()
        config = tomllib.loads(text)
        self.assertIn("# Keep this comment", text)
        self.assertEqual(config["model"], "pinned-model")
        self.assertEqual(config["mcp_servers"]["unrelated"]["url"], "http://other.invalid/mcp")
        self.assertEqual(config["mcp_servers"]["n8n"]["bearer_token_env_var"], CAPABILITY)
        self.assertNotIn("old-synthetic-token", text)
        self.assertNotIn("http_headers", config["mcp_servers"]["n8n"])
        refresh_codex(self.root, env)
        self.assertEqual(tomllib.loads(path.read_text()), config)

    def test_codex_refresh_does_not_duplicate_managed_overrides(self):
        env = {BROKER_URL: self.broker.url, CAPABILITY: "broker-token"}
        original = ["codex", "-c", 'model="pinned-model"', "resume", "native-session"]
        once = argv_for("codex", original, env, self.root)
        twice = argv_for("codex", once, env, self.root)
        self.assertEqual(once, twice)
        self.assertIn('model="pinned-model"', twice)
        self.assertEqual(twice[-2:], ["resume", "native-session"])

    def test_opencode_composes_provider_mcp_skills_and_instructions(self):
        env = {BROKER_URL: self.broker.url, CAPABILITY: "broker-token"}
        original = {"skills": ["/selected/skills"], "instructions": ["/context.md"],
                    "provider": {"other": {"options": {"keep": True}}},
                    "mcp": {"unrelated": {"type": "local", "command": ["fixture"]}}}
        result = opencode_config(env, original)
        self.assertEqual(result["skills"], original["skills"])
        self.assertEqual(result["instructions"], original["instructions"])
        self.assertIn("unrelated", result["mcp"])
        self.assertTrue(result["provider"]["other"]["options"]["keep"])
        self.assertNotIn("openrouter", original["provider"])

    def test_wrappers_do_not_reinject_legacy_keys_into_broker_launch(self):
        home = Path(self.host_env["HOME"])
        secret_dir = home / ".config/agent-console/secrets.d"
        secret_dir.mkdir(parents=True)
        marker = self.root / "legacy-file-was-executed"
        for filename in ["openrouter-main.env", "bushi-mcp.env"]:
            (secret_dir / filename).write_text("touch " + shlex.quote(str(marker)) + "\nexport OPENROUTER_API_KEY=legacy-sentinel\n")
        output_script = "#!" + sys.executable + "\nimport os,json\nprint(json.dumps({k:os.getenv(k) for k in " + repr([*PROTECTED_NAMES, CAPABILITY]) + "}))\n"
        binaries = [home / ".opencode/bin/opencode", self.root / "fake-node", self.root / "hermes/venv/bin/python"]
        for binary in binaries:
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text(output_script)
            binary.chmod(0o700)
        cli = self.root / "fake-cli.js"
        cli.write_text("// dummy CLI")
        context = self.root / "context.md"
        context.write_text("dummy context")
        env = {**self.host_env, CAPABILITY: "broker-sentinel", BROKER_URL: self.broker.url,
               "AGENT_CONSOLE_CONTEXT_FILE": str(context), "AGENT_CONSOLE_SESSION_ID": "session-test",
               "AGCONSOLE_PI_NODE": str(binaries[1]), "AGCONSOLE_PI_CLI": str(cli),
               "AGCONSOLE_HERMES_ROOT": str(self.root / "hermes")}
        for tool in ["opencode", "pi", "hermes"]:
            with self.subTest(tool=tool):
                process = subprocess.run(["bash", str(REPO / "scripts" / (tool + "-wrapper"))], env=env,
                                         capture_output=True, text=True, check=True, timeout=10)
                values = json.loads(process.stdout)
                self.assertEqual(values[CAPABILITY], "broker-sentinel")
                self.assertTrue(all(values[key] is None for key in PROTECTED_NAMES))
        self.assertFalse(marker.exists())

    def test_model_catalogue_subprocess_uses_broker_without_raw_credentials(self):
        captured = []
        def runner(*args, **kwargs):
            captured.append(kwargs["env"])
            record = {"id": "fixture", "providerID": "openrouter", "name": "fixture", "status": "active",
                      "cost": {"input": 0, "output": 0}, "limit": {"context": 1000, "output": 100}, "capabilities": {}}
            return subprocess.CompletedProcess(args[0], 0, "openrouter/fixture\n" + json.dumps(record) + "\n", "")
        catalogue = ModelCatalogue(self.root / "model-cache", runner=runner, broker=self.broker)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "host-secret-sentinel", "AGENT_CONSOLE_BROKER_ADMIN_FILE": str(self.admin_file)}):
            catalogue.list("openrouter", refresh=True)
        self.assertFalse(PROTECTED_NAMES.intersection(captured[0]))
        self.assertNotIn("AGENT_CONSOLE_BROKER_ADMIN_FILE", captured[0])
        config = json.loads(captured[0]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["provider"]["openrouter"]["options"]["apiKey"], "{env:" + CAPABILITY + "}")


if __name__ == "__main__":
    unittest.main()
