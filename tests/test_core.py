from __future__ import annotations

import os
import shlex
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.database import Database
from agent_console.manager import SessionManager
from agent_console.providers import LaunchSpec
from agent_console.skills import assign_skill
from agent_console.tmux import Tmux
from agent_console.validation import contained_path, validate_session_name


class ValidationTests(unittest.TestCase):
    def test_session_names(self) -> None:
        self.assertEqual(validate_session_name("codex-test_1.2"), "codex-test_1.2")
        for invalid in ("", "-starts-dash", "has space", "bad;command", "x" * 81):
            with self.assertRaises(ValueError):
                validate_session_name(invalid)

    def test_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "child"
            child.mkdir()
            self.assertEqual(contained_path(child, root), child.resolve())
            with self.assertRaises(ValueError):
                contained_path(root.parent, root)


@unittest.skipUnless(subprocess.run(["sh", "-c", "command -v tmux"], capture_output=True).returncode == 0, "tmux required")
class SessionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "bugfix"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"agent-console-test-{os.getpid()}-{id(self)}"
        self.legacy_socket_path = root / "legacy" / "tmux.sock"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            legacy_tmux_socket_path=self.legacy_socket_path,
            max_children_per_parent=2,
            max_managed_sessions=4,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        if self.manager.legacy_tmux:
            self.manager.legacy_tmux.run("kill-server", check=False)
        self.temp.cleanup()

    def test_legacy_reconciliation(self) -> None:
        subprocess.run(
            ["tmux", "-L", self.socket, "new-session", "-d", "-s", "legacy-test", "sleep", "60"],
            check=True,
        )
        sessions = self.manager.list_sessions()
        legacy = next(item for item in sessions if item["tmux_name"] == "legacy-test")
        self.assertFalse(legacy["managed"])
        self.assertEqual(legacy["status"], "legacy")
        with self.assertRaises(PermissionError):
            self.manager.kill("legacy-test")
        killed = self.manager.kill("legacy-test", allow_unmanaged=True)
        self.assertEqual(killed["status"], "process-exited")

    def test_attention_state_is_explicit_and_survives_reconciliation_and_exit(self) -> None:
        session = self.manager.create(
            tool="shell",
            profile="general",
            name="attention-test",
            repository=str(self.workspace),
        )
        updated = self.manager.set_attention(
            session["tmux_name"],
            state="blocked",
            note="Waiting for a reviewed deployment choice",
            actor="unit-test",
            surface="test",
        )
        self.assertEqual(updated["attention_state"], "blocked")
        self.assertEqual(updated["attention_note"], "Waiting for a reviewed deployment choice")
        self.assertEqual(updated["attention_updated_by"], "unit-test")
        self.manager.reconcile()
        self.assertEqual(self.manager.inspect("attention-test")["attention_state"], "blocked")
        killed = self.manager.kill("attention-test")
        self.assertFalse(killed["running"])
        self.assertEqual(killed["attention_state"], "blocked")
        normalized = self.manager.set_attention("attention-test", state="normal", note="ignored")
        self.assertEqual(normalized["attention_state"], "normal")
        self.assertIsNone(normalized["attention_note"])
        with self.manager.database.connect() as conn:
            audit = conn.execute(
                "SELECT details_json FROM audit_events WHERE action='session.attention.updated' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertNotIn("ignored", audit["details_json"])

    def test_attention_state_supports_legacy_sessions_and_validates_notes(self) -> None:
        assert self.manager.legacy_tmux is not None
        launcher = Path(self.temp.name) / "legacy-attention.sh"
        launcher.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
        launcher.chmod(0o700)
        self.manager.legacy_tmux.create("legacy-attention", self.workspace, launcher)
        self.manager.reconcile()
        result = self.manager.set_attention(
            "legacy-attention", state="needs_input", note="Needs operator input"
        )
        self.assertFalse(result["managed"])
        self.assertEqual(result["socket_scope"], "legacy")
        self.assertEqual(result["attention_state"], "needs_input")
        with self.assertRaises(ValueError):
            self.manager.set_attention("legacy-attention", state="invalid")
        with self.assertRaises(ValueError):
            self.manager.set_attention(
                "legacy-attention", state="blocked", note="x" * 1001
            )

    def test_managed_shell_lifecycle(self) -> None:
        session = self.manager.create(
            tool="shell",
            profile="general",
            name="managed-test",
            repository=str(self.workspace),
        )
        self.assertTrue(session["managed"])
        self.assertEqual(session["auth_context"], "default")
        self.assertEqual(session["provider"], "local")
        self.assertTrue(self.manager.tmux.exists("managed-test"))
        renamed = self.manager.rename("managed-test", "managed-renamed")
        self.assertEqual(renamed["tmux_name"], "managed-renamed")
        killed = self.manager.kill("managed-renamed")
        self.assertEqual(killed["status"], "process-exited")
        self.assertFalse(killed["running"])
        self.assertEqual(killed["socket_scope"], "canonical")

    def test_session_tree_review_and_launcher_context(self) -> None:
        parent = self.manager.create(
            tool="shell",
            profile="general",
            name="tree-parent",
            task="coordinate review",
            repository=str(self.workspace),
        )
        child = self.manager.delegate(
            profile="planner",
            parent=parent["id"],
            task="inspect only",
            tool="shell",
            auth_context="default",
        )["session"]
        self.manager.tmux.run("send-keys", "-t", child["tmux_name"], "-l", "printf PEER_REVIEW_OK")
        self.manager.tmux.run("send-keys", "-t", child["tmux_name"], "Enter")
        for _ in range(20):
            review = self.manager.review_session(child["tmux_name"], lines=50)
            if "PEER_REVIEW_OK" in review["content"]:
                break
            time.sleep(0.05)
        self.assertEqual(review["source"], "live-pane")
        self.assertIn("PEER_REVIEW_OK", review["content"])
        self.assertIn("untrusted data", review["notice"])
        self.assertTrue(self.manager.tmux.exists(child["tmux_name"]))
        tree = self.manager.session_tree()
        root = next(node for node in tree["roots"] if node["tmux_name"] == "tree-parent")
        self.assertEqual(root["child_count"], 1)
        self.assertEqual(root["children"][0]["tmux_name"], child["tmux_name"])
        launcher = Path(parent["launcher_path"]).read_text(encoding="utf-8")
        self.assertIn("AGENT_CONSOLE_SESSION_NAME=tree-parent", launcher)
        self.assertIn(f"AGENT_CONSOLE_SESSION_ID={parent['id']}", launcher)
        self.assertNotIn("coordinate review", launcher)
        self.assertEqual(self.manager.session_brief("tree-parent")["brief"], "coordinate review")
        context = self.manager.session_context("tree-parent")
        self.assertIn("Managed Agent Console Context", context["context"])
        self.assertNotIn("coordinate review", context["context"])
        with self.assertRaises(ValueError):
            self.manager.review_session(child["tmux_name"], lines=1001)

    def test_legacy_socket_review_and_archived_fallback(self) -> None:
        assert self.manager.legacy_tmux is not None
        launcher = Path(self.temp.name) / "legacy-review.sh"
        launcher.write_text(
            "#!/bin/sh\nprintf 'LEGACY_REVIEW_OK\\n'\nsleep 60\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)
        self.manager.legacy_tmux.create("legacy-review", self.workspace, launcher)
        self.manager.reconcile()
        for _ in range(20):
            review = self.manager.review_session("legacy-review", lines=50)
            if "LEGACY_REVIEW_OK" in review["content"]:
                break
            time.sleep(0.05)
        self.assertEqual(review["session"]["socket_scope"], "legacy")
        self.assertIn("LEGACY_REVIEW_OK", review["content"])
        archived = self.manager.archive("legacy-review", kill=True, allow_unmanaged=True)
        self.assertFalse(archived["running"])
        fallback = self.manager.review_session("legacy-review", lines=50)
        self.assertEqual(fallback["source"], "archived-transcript")
        self.assertIn("LEGACY_REVIEW_OK", fallback["content"])

    def test_write_capable_delegation_is_rejected(self) -> None:
        parent = self.manager.create(
            tool="shell",
            profile="general",
            name="parent-test",
            repository=str(self.workspace),
        )
        with self.assertRaises(ValueError):
            self.manager.delegate(
                profile="coder",
                parent=parent["id"],
                task="write something",
                tool="shell",
            )
        with self.assertRaisesRegex(ValueError, "Plan mode"):
            self.manager.delegate(
                profile="planner",
                parent=parent["id"],
                task="do not build",
                tool="opencode",
                agent_mode="build",
            )

    def test_hermes_launcher_is_interactive_without_one_shot_prompt(self) -> None:
        args = self.manager._launcher_args(
            "hermes",
            "general",
            self.workspace,
            "Reply exactly HERMES_OK",
        )
        self.assertTrue(args[0].endswith("hermes-agent-console-general"))
        self.assertEqual(args[1:], ["--cli"])
        self.assertNotIn("Reply exactly HERMES_OK", args)

    def test_opencode_launcher_defaults_to_plan(self) -> None:
        models = [{
            "id": "cheap", "model": "opencode/cheap", "provider": "opencode",
            "name": "Cheap", "status": "active", "selectable": True,
            "cost": {"input": 0.1, "output": 0.2, "cache_read": None, "reasoning": None},
            "limits": {"context": 1000, "output": 100},
            "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
        }]
        with patch.object(self.manager.models, "list", return_value={"models": models}):
            args = self.manager._launcher_args("opencode", "general", self.workspace, None)
        self.assertIn("--agent", args)
        self.assertIn("plan", args)
        self.assertIn("--model", args)
        self.assertIn("--auto", args)
        self.assertNotIn("--prompt", args)
        self.assertIn("opencode/cheap", args)
        with patch.object(self.manager.models, "list", return_value={"models": models}):
            build = self.manager._launcher_args(
                "opencode", "general", self.workspace, None, agent_mode="build"
            )
        self.assertIn("build", build)
        self.assertIn("--auto", build)

    def test_codex_plan_and_auto_launch_modes(self) -> None:
        auto = self.manager._launcher_args(
            "codex", "general", self.workspace, None, agent_mode="auto"
        )
        self.assertIn("workspace-write", auto)
        self.assertIn("on-request", auto)
        self.assertNotIn("danger-full-access", auto)
        plan = self.manager._launcher_args(
            "codex", "general", self.workspace, None, agent_mode="plan"
        )
        self.assertIn("read-only", plan)
        self.assertIn("never", plan)
        self.assertIn("Plan mode", plan[-1])
        with self.assertRaisesRegex(ValueError, "Plan mode"):
            self.manager.create(
                tool="codex", profile="planner", name="bad-codex-auto",
                repository=str(self.workspace), agent_mode="auto",
            )

    def test_claude_creation_is_disabled_but_not_removed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "subscription inactive"):
            self.manager.create(
                tool="claude",
                profile="general",
                name="claude-disabled",
                repository=str(self.workspace),
            )

    def test_canonical_create_recovers_owned_stale_socket(self) -> None:
        stale_path = Path(self.temp.name) / "stale" / "tmux.sock"
        stale_path.parent.mkdir()
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(str(stale_path))
        stale.close()
        launcher = Path(self.temp.name) / "stale-launcher.sh"
        launcher.write_text("#!/bin/sh\nexec sleep 60\n", encoding="utf-8")
        launcher.chmod(0o700)
        tmux = Tmux(socket_path=stale_path)
        tmux.create("stale-recovery", self.workspace, launcher)
        self.assertTrue(tmux.exists("stale-recovery"))
        tmux.kill("stale-recovery")

    def test_rename_updates_context_and_launcher(self) -> None:
        session = self.manager.create(
            tool="shell",
            profile="general",
            name="ctx-rename-test",
            repository=str(self.workspace),
        )
        ctx_path = self.manager.settings.state_dir / "contexts" / "ctx-rename-test.md"
        launcher_path = self.manager.settings.state_dir / "launchers" / "ctx-rename-test.sh"
        old_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "ctx-rename-test.md")
        new_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "ctx-renamed.md")
        self.assertTrue(ctx_path.is_file())
        self.assertTrue(launcher_path.is_file())
        original_ctx = ctx_path.read_text(encoding="utf-8")
        self.assertIn("Session name: ctx-rename-test", original_ctx)
        self.assertIn("ctx-rename-test", original_ctx)
        launcher_text = launcher_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_CONSOLE_SESSION_NAME=ctx-rename-test", launcher_text)

        self.manager.rename("ctx-rename-test", "ctx-renamed")

        new_ctx_path = self.manager.settings.state_dir / "contexts" / "ctx-renamed.md"
        new_launcher_path = self.manager.settings.state_dir / "launchers" / "ctx-renamed.sh"
        self.assertFalse(ctx_path.exists(), "old context file should be removed on rename")
        self.assertFalse(launcher_path.exists(), "old launcher should be removed on rename")
        self.assertTrue(new_ctx_path.is_file(), "new context file should exist")
        self.assertTrue(new_launcher_path.is_file(), "new launcher should exist")
        updated_ctx = new_ctx_path.read_text(encoding="utf-8")
        self.assertNotIn("ctx-rename-test", updated_ctx,
                         "stale session name must not appear in renamed context")
        self.assertIn("Session name: ctx-renamed", updated_ctx)
        updated_launcher = new_launcher_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_CONSOLE_SESSION_NAME=ctx-renamed", updated_launcher)
        self.assertNotIn("AGENT_CONSOLE_SESSION_NAME=ctx-rename-test", updated_launcher,
                         "stale env var must not appear in renamed launcher")
        self.assertNotIn(old_ctx_abs, updated_launcher,
                         "stale context-file path must not appear in renamed launcher")
        self.manager.kill("ctx-renamed")

    def test_rename_launcher_context_path_is_wired_for_restart(self) -> None:
        session = self.manager.create(
            tool="shell",
            profile="general",
            name="restart-ctx-test",
            repository=str(self.workspace),
        )
        launcher_path = self.manager.settings.state_dir / "launchers" / "restart-ctx-test.sh"
        old_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "restart-ctx-test.md")
        new_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "restart-ctx-renamed.md")
        launcher_text = launcher_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_CONSOLE_SESSION_NAME=restart-ctx-test", launcher_text)

        # Inject a context-path reference into the launcher (simulating what OpenCode/Hermes
        # providers embed in launcher env vars) so we can verify rename rewrites it.
        injected = f"export TEST_CTX_FILE={shlex.quote(old_ctx_abs)}\n"
        launcher_path.write_text(launcher_text + injected, encoding="utf-8")
        self.assertIn(old_ctx_abs, launcher_path.read_text(encoding="utf-8"))

        self.manager.rename("restart-ctx-test", "restart-ctx-renamed")

        new_launcher_path = self.manager.settings.state_dir / "launchers" / "restart-ctx-renamed.sh"
        self.assertTrue(new_launcher_path.is_file())
        new_text = new_launcher_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_CONSOLE_SESSION_NAME=restart-ctx-renamed", new_text)
        self.assertNotIn(old_ctx_abs, new_text,
                         "old context-file path must be purged from launcher on rename")
        self.assertIn(new_ctx_abs, new_text,
                      "renamed launcher must reference renamed context-file path")
        new_ctx_path = self.manager.settings.state_dir / "contexts" / "restart-ctx-renamed.md"
        self.assertTrue(new_ctx_path.is_file())
        ctx_text = new_ctx_path.read_text(encoding="utf-8")
        self.assertIn("Session name: restart-ctx-renamed", ctx_text)
        self.assertNotIn("restart-ctx-test", ctx_text)

        # Restart re-executes the launcher; verify the launcher is internally consistent.
        self.manager.restart("restart-ctx-renamed")
        self.assertTrue(self.manager.tmux.exists("restart-ctx-renamed"))
        self.assertEqual(self.manager.inspect("restart-ctx-renamed")["launcher_path"],
                         str(new_launcher_path))
        self.manager.kill("restart-ctx-renamed")

    def test_delegated_session_names_in_context_and_launcher(self) -> None:
        parent = self.manager.create(
            tool="shell",
            profile="general",
            name="deleg-ctx-parent",
            repository=str(self.workspace),
        )
        child = self.manager.delegate(
            profile="planner",
            parent=parent["id"],
            task="verify delegate session name",
            tool="shell",
        )["session"]
        child_name = child["tmux_name"]
        child_ctx_path = self.manager.settings.state_dir / "contexts" / f"{child_name}.md"
        child_launcher_path = self.manager.settings.state_dir / "launchers" / f"{child_name}.sh"
        self.assertTrue(child_ctx_path.is_file(), "delegated child context file must exist")
        self.assertTrue(child_launcher_path.is_file(), "delegated child launcher must exist")
        child_ctx = child_ctx_path.read_text(encoding="utf-8")
        self.assertIn(f"Session name: {child_name}", child_ctx)
        self.assertIn(f"Session ID: {child['id']}", child_ctx)
        self.assertNotIn("deleg-ctx-parent", child_ctx,
                         "child context must not leak parent session name")
        child_launcher = child_launcher_path.read_text(encoding="utf-8")
        self.assertIn(f"AGENT_CONSOLE_SESSION_NAME={child_name}", child_launcher)
        self.assertIn(f"AGENT_CONSOLE_SESSION_ID={child['id']}", child_launcher)
        self.assertNotIn("deleg-ctx-parent", child_launcher,
                         "child launcher must not leak parent session name")
        self.manager.kill(child_name)
        self.manager.kill("deleg-ctx-parent")

    def test_opencode_rename_updates_config_content_env(self) -> None:
        """OpenCode provider embeds context path in OPENCODE_CONFIG_CONTENT; rename must
        update the env-var value to point at the renamed context file."""
        from agent_console.providers import TOOL_BINARIES
        original_bin = TOOL_BINARIES.get("opencode")
        TOOL_BINARIES["opencode"] = Path("/usr/bin/zsh")
        try:
            models = [{
                "id": "test-model",
                "model": "opencode/test-model",
                "provider": "opencode",
                "name": "Test Model",
                "status": "active",
                "selectable": True,
                "cost": {"input": 0.1, "output": 0.2, "cache_read": None, "reasoning": None},
                "limits": {"context": 1000, "output": 100},
                "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
            }]
            with patch.object(self.manager.models, "list", return_value={"models": models}):
                session = self.manager.create(
                    tool="opencode",
                    profile="general",
                    name="oc-rename-ctx",
                    repository=str(self.workspace),
                )
            old_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "oc-rename-ctx.md")
            new_ctx_abs = str(self.manager.settings.state_dir / "contexts" / "oc-renamed-ctx.md")

            launcher_path = self.manager.settings.state_dir / "launchers" / "oc-rename-ctx.sh"
            launcher_text = launcher_path.read_text(encoding="utf-8")
            self.assertIn("OPENCODE_CONFIG_CONTENT", launcher_text,
                          "OpenCode launcher must contain OPENCODE_CONFIG_CONTENT")
            self.assertIn(old_ctx_abs, launcher_text,
                          "OPENCODE_CONFIG_CONTENT must reference current context file")

            self.manager.rename("oc-rename-ctx", "oc-renamed-ctx")

            new_launcher_path = self.manager.settings.state_dir / "launchers" / "oc-renamed-ctx.sh"
            self.assertTrue(new_launcher_path.is_file())
            new_launcher = new_launcher_path.read_text(encoding="utf-8")
            self.assertIn("OPENCODE_CONFIG_CONTENT", new_launcher,
                          "renamed launcher must retain OPENCODE_CONFIG_CONTENT")
            self.assertNotIn(old_ctx_abs, new_launcher,
                             "OPENCODE_CONFIG_CONTENT must not reference old context path after rename")
            self.assertIn(new_ctx_abs, new_launcher,
                          "OPENCODE_CONFIG_CONTENT must reference renamed context file path")

            new_ctx = self.manager.settings.state_dir / "contexts" / "oc-renamed-ctx.md"
            self.assertTrue(new_ctx.is_file())
            ctx_text = new_ctx.read_text(encoding="utf-8")
            self.assertIn("Session name: oc-renamed-ctx", ctx_text)
            self.assertNotIn("oc-rename-ctx", ctx_text)

            self.manager.kill("oc-renamed-ctx")
        finally:
            if original_bin is not None:
                TOOL_BINARIES["opencode"] = original_bin
            else:
                TOOL_BINARIES.pop("opencode", None)

    def test_hermes_delegate_rename_updates_context_file_env(self) -> None:
        """Hermes provider embeds context path in AGENT_CONSOLE_CONTEXT_FILE; rename of a
        delegated Hermes child must update the env-var value."""
        from agent_console.providers import TOOL_BINARIES
        original_bin = TOOL_BINARIES.get("hermes")
        TOOL_BINARIES["hermes"] = Path("/usr/bin/zsh")
        try:
            secret_path = self.manager.auth.secrets_dir / "openrouter-main.env"
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            secret_path.write_text("export OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
            secret_path.chmod(0o600)

            parent = self.manager.create(
                tool="shell",
                profile="general",
                name="hermes-ctx-parent",
                repository=str(self.workspace),
            )
            child = self.manager.delegate(
                profile="planner",
                parent=parent["id"],
                task="verify hermes context file env on rename",
                tool="hermes",
            )["session"]
            child_name = child["tmux_name"]

            old_ctx_abs = str(self.manager.settings.state_dir / "contexts" / f"{child_name}.md")
            new_name = "hermes-child-renamed"
            new_ctx_abs = str(self.manager.settings.state_dir / "contexts" / f"{new_name}.md")

            child_launcher_path = self.manager.settings.state_dir / "launchers" / f"{child_name}.sh"
            child_launcher = child_launcher_path.read_text(encoding="utf-8")
            self.assertIn("AGENT_CONSOLE_CONTEXT_FILE", child_launcher,
                          "Hermes launcher must contain AGENT_CONSOLE_CONTEXT_FILE")
            self.assertIn(old_ctx_abs, child_launcher,
                          "AGENT_CONSOLE_CONTEXT_FILE must reference child context file")

            self.manager.rename(child_name, new_name)

            new_launcher = self.manager.settings.state_dir / "launchers" / f"{new_name}.sh"
            self.assertTrue(new_launcher.is_file())
            new_text = new_launcher.read_text(encoding="utf-8")
            self.assertIn("AGENT_CONSOLE_CONTEXT_FILE", new_text,
                          "renamed launcher must retain AGENT_CONSOLE_CONTEXT_FILE")
            self.assertNotIn(old_ctx_abs, new_text,
                             "AGENT_CONSOLE_CONTEXT_FILE must not reference old path after rename")
            self.assertIn(new_ctx_abs, new_text,
                          "AGENT_CONSOLE_CONTEXT_FILE must reference renamed context file path")

            new_ctx = self.manager.settings.state_dir / "contexts" / f"{new_name}.md"
            self.assertTrue(new_ctx.is_file())
            ctx_text = new_ctx.read_text(encoding="utf-8")
            self.assertIn(f"Session name: {new_name}", ctx_text)
            self.assertNotIn(child_name, ctx_text)

            self.manager.kill(new_name)
            self.manager.kill("hermes-ctx-parent")
        finally:
            if original_bin is not None:
                TOOL_BINARIES["hermes"] = original_bin
            else:
                TOOL_BINARIES.pop("hermes", None)

    def test_reconcile_does_not_overwrite_exit_reason(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="exit-reason-idempotent",
            repository=str(self.workspace),
        )
        killed = self.manager.kill(session["tmux_name"])
        self.assertEqual(killed["exit_reason"], "killed by user")
        self.manager.reconcile()
        inspected = self.manager.inspect(session["tmux_name"])
        self.assertEqual(inspected["exit_reason"], "killed by user")

    def test_reconcile_does_not_reactivate_archived(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="archived-no-reactivate",
            repository=str(self.workspace),
        )
        archived = self.manager.archive(session["tmux_name"], kill=True)
        self.assertEqual(archived["status"], "archived")
        launcher = Path(self.temp.name) / "archived-reappear.sh"
        launcher.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
        launcher.chmod(0o700)
        self.manager.tmux.create(session["tmux_name"], self.workspace, launcher)
        self.assertTrue(self.manager.tmux.exists(session["tmux_name"]))
        self.manager.reconcile()
        inspected = self.manager.inspect(session["tmux_name"])
        self.assertEqual(inspected["status"], "archived")
        self.manager.tmux.kill(session["tmux_name"])

    def test_failed_create_cleans_up_new_artifacts(self) -> None:
        launcher_p = self.manager.settings.state_dir / "launchers" / "fresh-fail-test.sh"
        context_p = self.manager.settings.state_dir / "contexts" / "fresh-fail-test.md"
        with patch.object(self.manager.database, 'audit', side_effect=RuntimeError("mock audit failure")):
            with self.assertRaises(RuntimeError):
                self.manager.create(
                    tool="shell", profile="general", name="fresh-fail-test",
                    repository=str(self.workspace),
                )
        self.assertFalse(launcher_p.exists(), "newly-created launcher should be cleaned up on failed create")
        self.assertFalse(context_p.exists(), "newly-created context should be cleaned up on failed create")

    def test_failed_create_preserves_pre_existing_artifacts(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="preserve-test",
            repository=str(self.workspace),
        )
        launcher_p = self.manager.settings.state_dir / "launchers" / "preserve-test.sh"
        context_p = self.manager.settings.state_dir / "contexts" / "preserve-test.md"
        self.assertTrue(launcher_p.exists())
        self.assertTrue(context_p.exists())
        self.manager.kill(session["tmux_name"])
        with patch.object(self.manager.database, 'audit', side_effect=RuntimeError("mock audit failure")):
            with self.assertRaises(RuntimeError):
                self.manager.create(
                    tool="shell", profile="general", name="preserve-test",
                    repository=str(self.workspace),
                )
        self.assertTrue(launcher_p.exists(), "pre-existing launcher must survive failed create")
        self.assertTrue(context_p.exists(), "pre-existing context must survive failed create")

    def test_agent_exit_returns_to_persistent_tmux_shell(self) -> None:
        launcher = Path(self.temp.name) / "short-agent.sh"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o700)
        self.manager.tmux.create("persistent-agent", self.workspace, launcher)
        for _ in range(20):
            session = self.manager.tmux.list_sessions().get("persistent-agent")
            if session and session.current_command in {"bash", "sh", "zsh"}:
                break
            time.sleep(0.05)
        self.assertTrue(self.manager.tmux.exists("persistent-agent"))
        before = self.manager.tmux.pane_pids("persistent-agent")
        self.manager.tmux.restart("persistent-agent", launcher)
        after = self.manager.tmux.pane_pids("persistent-agent")
        self.assertNotEqual(before, after)
        self.assertTrue(self.manager.tmux.exists("persistent-agent"))
        self.manager.tmux.kill("persistent-agent")

    def test_plan_execution_creates_distinct_worktree_session(self) -> None:
        repository = self.workspace / "repository"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Agent Console Test"], check=True)
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
        revision = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        artifact = self.manager.settings.handoff_dir / "test-plan"
        artifact.mkdir(parents=True)
        (artifact / "plan.md").write_text("# Implement fixture\n", encoding="utf-8")
        (artifact / "metadata.json").write_text(
            '{"title":"Fixture","repository":"%s","repository_revision":"%s"}\n'
            % (repository, revision),
            encoding="utf-8",
        )
        with patch.object(
            self.manager,
            "_launch_spec",
            return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
        ):
            session = self.manager.execute_plan("test-plan", name="plan-implementation")
        inspected = self.manager.inspect_plan("test-plan")
        self.assertEqual(inspected["revision_state"], "current")
        self.assertEqual(session["linked_plan_id"], "test-plan")
        self.assertNotEqual(session["worktree"], str(repository))
        self.assertTrue(Path(session["worktree"]).is_dir())
        self.manager.kill("plan-implementation")
        self.assertTrue(Path(session["worktree"]).is_dir())

    def test_create_rejects_invalid_skill_assignments(self) -> None:
        skills_root = Path(self.temp.name) / "skills"
        skills_root.mkdir()
        skill_name = "test-core-skill"
        (skills_root / skill_name).mkdir(exist_ok=True)
        (skills_root / skill_name / "SKILL.md").write_text(
            "---\nname: test-core-skill\ndescription: Core test\nkind: standard\n---\n",
            encoding="utf-8",
        )
        old_env = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)
        try:
            assign_skill(self.manager.database, "general", skill_name)
            with patch.object(
                self.manager,
                "_launch_spec",
                return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
            ):
                session = self.manager.create(
                    tool="codex",
                    profile="general",
                    name="skill-valid-test",
                    repository=str(self.workspace),
                )
                self.assertEqual(session["profile"], "general")
                self.manager.kill("skill-valid-test")
            (skills_root / skill_name / "SKILL.md").unlink()
            with self.assertRaises(ValueError) as ctx:
                self.manager.create(
                    tool="codex",
                    profile="general",
                    name="skill-invalid-test",
                    repository=str(self.workspace),
                )
            self.assertIn("invalid skill assignments", str(ctx.exception))
        finally:
            if old_env is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_env
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_restart_rejects_invalid_skill_assignments(self) -> None:
        skills_root = Path(self.temp.name) / "skills-restart"
        skills_root.mkdir()
        skill_name = "restart-core-skill"
        (skills_root / skill_name).mkdir(exist_ok=True)
        (skills_root / skill_name / "SKILL.md").write_text(
            "---\nname: restart-core-skill\ndescription: Restart test\nkind: standard\n---\n",
            encoding="utf-8",
        )
        old_env = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)
        try:
            assign_skill(self.manager.database, "coder", skill_name)
            with patch.object(
                self.manager,
                "_launch_spec",
                return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
            ):
                session = self.manager.create(
                    tool="codex",
                    profile="coder",
                    name="restart-valid-test",
                    repository=str(self.workspace),
                )
            (skills_root / skill_name / "SKILL.md").unlink()
            with self.assertRaises(ValueError) as ctx:
                self.manager.restart("restart-valid-test")
            self.assertIn("invalid skill assignments preventing restart", str(ctx.exception))
            self.manager.kill("restart-valid-test")
        finally:
            if old_env is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_env
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_launcher_overlay_contains_isolated_skills(self) -> None:
        skills_root = Path(self.temp.name) / "overlay-skills"
        skills_root.mkdir()
        skill_name = "overlay-skill"
        (skills_root / skill_name).mkdir(exist_ok=True)
        (skills_root / skill_name / "SKILL.md").write_text(
            "---\nname: overlay-skill\ndescription: Overlay test\nkind: standard\n---\n",
            encoding="utf-8",
        )
        old_env = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)
        try:
            assign_skill(self.manager.database, "general", skill_name)
            with patch.object(
                self.manager,
                "_launch_spec",
                return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
            ):
                session = self.manager.create(
                    tool="codex",
                    profile="general",
                    name="overlay-test",
                    repository=str(self.workspace),
                )
            launcher = Path(self.manager.settings.state_dir) / "launchers" / "overlay-test.sh"
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertIn("CODEX_HOME=", launcher_text)
            overlay_line = next(
                l for l in launcher_text.splitlines() if l.startswith("export CODEX_HOME=")
            )
            overlay_path = overlay_line.split("=", 1)[1].strip().strip("'\"")
            overlay_dir = Path(overlay_path)
            self.assertTrue(overlay_dir.is_dir())
            overlay_skills = overlay_dir / "skills"
            self.assertTrue(overlay_skills.is_symlink())
            self.assertTrue((overlay_skills / skill_name).is_dir())
            isolated_root = Path(
                self.manager.settings.state_dir / "skills-isolated" / "overlay-test"
            )
            self.assertEqual(overlay_skills.resolve(), isolated_root.resolve())
            unassigned_entries = [p for p in overlay_skills.iterdir()]
            self.assertEqual(len(unassigned_entries), 1)
            self.assertEqual(unassigned_entries[0].name, skill_name)
            self.manager.kill("overlay-test")
        finally:
            if old_env is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_env
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_real_codex_launcher_overlay_wins_over_global(self) -> None:
        skills_root = Path(self.temp.name) / "real-overlay-skills"
        skills_root.mkdir()
        skill_name = "real-skill"
        (skills_root / skill_name).mkdir(exist_ok=True)
        (skills_root / skill_name / "SKILL.md").write_text(
            "---\nname: real-skill\ndescription: Real overlay test\nkind: standard\n---\n",
            encoding="utf-8",
        )
        codex_bin = Path(self.temp.name) / "codex-stub"
        codex_bin.write_text("#!/usr/bin/env bash\necho stub\n", encoding="utf-8")
        codex_bin.chmod(0o755)
        old_bin = os.environ.get("AGCONSOLE_CODEX_BIN")
        os.environ["AGCONSOLE_CODEX_BIN"] = str(codex_bin)
        old_skill_env = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)
        global_codex_home = self.manager.auth.codex_home("default")
        try:
            assign_skill(self.manager.database, "general", skill_name)
            session = self.manager.create(
                tool="codex",
                profile="general",
                name="real-overlay-test",
                repository=str(self.workspace),
            )
            launcher = Path(self.manager.settings.state_dir) / "launchers" / "real-overlay-test.sh"
            launcher_text = launcher.read_text(encoding="utf-8")
            cod_ex_lines = [
                l for l in launcher_text.splitlines()
                if l.startswith("export CODEX_HOME=")
            ]
            self.assertEqual(
                len(cod_ex_lines), 1,
                f"expected exactly one CODEX_HOME export, got {len(cod_ex_lines)}: {cod_ex_lines}"
            )
            overlay_path_str = cod_ex_lines[0].split("=", 1)[1].strip().strip("'\"")
            overlay_path = Path(overlay_path_str)
            self.assertTrue(
                overlay_path.is_dir(),
                f"overlay path {overlay_path} should exist",
            )
            self.assertNotEqual(
                str(overlay_path), str(global_codex_home),
                "overlay CODEX_HOME must differ from global codex home",
            )
            overlay_skills = overlay_path / "skills"
            self.assertTrue(
                overlay_skills.is_symlink(),
                f"overlay skills should be a symlink: {overlay_skills}",
            )
            isolated_root = Path(
                self.manager.settings.state_dir / "skills-isolated" / "real-overlay-test"
            )
            self.assertEqual(
                overlay_skills.resolve(), isolated_root.resolve(),
                "overlay skills symlink must resolve to isolated root",
            )
            self.assertTrue(
                (overlay_skills / skill_name).is_dir(),
                "assigned skill must be present in overlay skills",
            )
            global_skills = global_codex_home / "skills"
            if global_skills.is_symlink():
                self.assertNotEqual(
                    overlay_skills.resolve(), global_skills.resolve(),
                    "overlay skills must NOT resolve to the global skills root",
                )
            self.manager.kill("real-overlay-test")
        finally:
            if old_skill_env is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_skill_env
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)
            if old_bin is not None:
                os.environ["AGCONSOLE_CODEX_BIN"] = old_bin
            else:
                os.environ.pop("AGCONSOLE_CODEX_BIN", None)

    def test_empty_assignments_creates_empty_overlay(self) -> None:
        with patch.object(
            self.manager,
            "_launch_spec",
            return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
        ):
            session = self.manager.create(
                tool="codex",
                profile="general",
                name="empty-overlay-test",
                repository=str(self.workspace),
            )
        launcher = Path(self.manager.settings.state_dir) / "launchers" / "empty-overlay-test.sh"
        launcher_text = launcher.read_text(encoding="utf-8")
        overlay_line = next(
            l for l in launcher_text.splitlines() if l.startswith("export CODEX_HOME=")
        )
        overlay_path = overlay_line.split("=", 1)[1].strip().strip("'\"")
        overlay_skills = Path(overlay_path) / "skills"
        self.assertTrue(overlay_skills.is_symlink())
        self.assertEqual(len(list(overlay_skills.iterdir())), 0,
                         "empty-assignment overlay must be empty to prevent global skill leak")
        self.manager.kill("empty-overlay-test")

    def test_tool_without_isolation_fails_closed_for_assignments(self) -> None:
        skills_root = Path(self.temp.name) / "fail-closed-skills"
        skills_root.mkdir()
        skill_name = "fail-closed-skill"
        (skills_root / skill_name).mkdir(exist_ok=True)
        (skills_root / skill_name / "SKILL.md").write_text(
            "---\nname: fail-closed-skill\ndescription: FC\ntools: opencode, hermes, claude, codex\nkind: standard\n---\n",
            encoding="utf-8",
        )
        old_env = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)
        try:
            assign_skill(self.manager.database, "general", skill_name)
            for bad_tool in ("opencode", "hermes", "claude"):
                with self.assertRaises(RuntimeError) as ctx:
                    with patch.object(
                        self.manager,
                        "_launch_spec",
                        return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
                    ):
                        self.manager.create(
                            tool=bad_tool,
                            profile="general",
                            name=f"fail-{bad_tool}",
                            repository=str(self.workspace),
                        )
                err = str(ctx.exception)
                self.assertIn("cannot isolate", err, f"{bad_tool} should be rejected")
                self.assertIn(bad_tool, err)
            with patch.object(
                self.manager,
                "_launch_spec",
                return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
            ):
                codex_session = self.manager.create(
                    tool="codex", profile="general",
                    name="fail-closed-codex-ok",
                    repository=str(self.workspace),
                )
                self.assertEqual(codex_session["profile"], "general")
                self.manager.kill("fail-closed-codex-ok")
        finally:
            if old_env is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_env
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_tool_without_isolation_passes_without_assignments(self) -> None:
        models = [{
            "id": "cheap", "model": "opencode/cheap", "provider": "opencode",
            "name": "Cheap", "status": "active", "selectable": True,
            "cost": {"input": 0.1, "output": 0.2, "cache_read": None, "reasoning": None},
            "limits": {"context": 1000, "output": 100},
            "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
        }]
        with patch.object(self.manager.models, "list", return_value={"provider": "opencode", "models": models}):
            with patch.object(
                self.manager,
                "_launch_spec",
                return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
            ):
                session = self.manager.create(
                    tool="opencode",
                    profile="general",
                    name="no-assign-opencode-pass",
                    repository=str(self.workspace),
                )
                self.assertEqual(session["profile"], "general")
                self.manager.kill("no-assign-opencode-pass")


class WaitProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"agent-console-wait-test-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            max_children_per_parent=4,
            max_managed_sessions=8,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.temp.cleanup()

    def test_wait_children_all_ready_for_review(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-parent-success",
            repository=str(self.workspace),
        )
        child1 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="plan only",
            tool="shell",
        )["session"]
        child2 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="plan more",
            tool="shell",
        )["session"]

        self.manager.set_attention(child1["tmux_name"], state="ready_for_review", actor="test")
        self.manager.set_attention(child2["tmux_name"], state="ready_for_review", actor="test")

        self.manager.kill(child1["tmux_name"])
        self.manager.kill(child2["tmux_name"])

        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=15, poll_interval=1
        )
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["exit_code"], 0)
        for child in result["children"]:
            self.assertEqual(child["wait_status"], "success")

    def test_wait_timeout(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-parent-timeout",
            repository=str(self.workspace),
        )
        self.manager.delegate(
            profile="planner", parent=parent["id"], task="plan only",
            tool="shell",
        )

        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=3, poll_interval=1
        )
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual(result["exit_code"], 1)

    def test_wait_validation_rejects_invalid_params(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-validation",
            repository=str(self.workspace),
        )
        with self.assertRaises(ValueError):
            self.manager.wait_for_children(parent["tmux_name"], timeout=0)
        with self.assertRaises(ValueError):
            self.manager.wait_for_children(parent["tmux_name"], poll_interval=0)

    def test_wait_status_returns_latest_wait(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-status-test",
            repository=str(self.workspace),
        )
        child = self.manager.delegate(
            profile="planner", parent=parent["id"], task="test wait status",
            tool="shell",
        )["session"]
        self.manager.set_attention(child["tmux_name"], state="ready_for_review", actor="test")
        self.manager.kill(child["tmux_name"])
        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=15, poll_interval=1
        )
        status = self.manager.wait_status(parent["tmux_name"])
        self.assertIsNotNone(status)
        self.assertEqual(status["outcome"], "success")

    def test_context_file_includes_wait_protocol(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-ctx-parent",
            repository=str(self.workspace),
        )
        ctx = self.manager.session_context("wait-ctx-parent")
        self.assertIn("wait-for-children", ctx["context"])

        child = self.manager.delegate(
            profile="planner", parent=parent["id"], task="wait context test",
            tool="shell",
        )["session"]
        child_ctx = self.manager.session_context(child["tmux_name"])
        self.assertIn("ready_for_review", child_ctx["context"])
        self.assertIn("wait-for-children", child_ctx["context"])

    def test_wait_detects_blocked_child_immediately(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-blocked-parent",
            repository=str(self.workspace),
        )
        child1 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="blocked child",
            tool="shell",
        )["session"]
        child2 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="running child",
            tool="shell",
        )["session"]

        self.manager.set_attention(child1["tmux_name"], state="blocked", note="needs help", actor="test")
        self.manager.kill(child1["tmux_name"])

        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=15, poll_interval=1
        )
        self.assertEqual(result["outcome"], "intervention")
        self.assertEqual(result["exit_code"], 2)
        blocked = next(c for c in result["children"] if c["tmux_name"] == child1["tmux_name"])
        self.assertEqual(blocked["wait_status"], "intervention")
        self.assertEqual(blocked["attention_state"], "blocked")
        self.assertEqual(blocked["attention_note"], "needs help")

        self.manager.kill(child2["tmux_name"])

    def test_wait_detects_failed_child_after_all_terminal(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-fail-parent",
            repository=str(self.workspace),
        )
        child1 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="fail child",
            tool="shell",
        )["session"]
        child2 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="also fail child",
            tool="shell",
        )["session"]

        self.manager.kill(child1["tmux_name"])
        self.manager.kill(child2["tmux_name"])

        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=15, poll_interval=1
        )
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["exit_code"], 3)
        for child in result["children"]:
            self.assertEqual(child["wait_status"], "completed")

    def test_wait_failed_child_while_sibling_waiting(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="wait-fail-sibling-parent",
            repository=str(self.workspace),
        )
        child1 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="fail child",
            tool="shell",
        )["session"]
        child2 = self.manager.delegate(
            profile="planner", parent=parent["id"], task="waiting sibling",
            tool="shell",
        )["session"]

        self.manager.kill(child1["tmux_name"])

        t0 = time.monotonic()
        result = self.manager.wait_for_children(
            parent["tmux_name"], timeout=15, poll_interval=1
        )
        elapsed = time.monotonic() - t0
        self.assertLess(
            elapsed, 10,
            f"should break immediately, not wait for timeout or sibling; took {elapsed:.1f}s",
        )
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["exit_code"], 3)

        self.manager.kill(child2["tmux_name"])

    def test_wait_web_endpoint(self) -> None:
        os.environ["AGENT_CONSOLE_TAILSCALE_LOGIN"] = "test@example.com"
        from fastapi.testclient import TestClient
        from agent_console.web import create_app
        client = TestClient(create_app(self.manager), base_url="http://localhost")
        headers = {"Tailscale-User-Login": "test@example.com"}

        parent = self.manager.create(
            tool="shell", profile="general", name="wait-web-parent",
            repository=str(self.workspace),
        )
        child = self.manager.delegate(
            profile="planner", parent=parent["id"], task="web wait child",
            tool="shell",
        )["session"]

        status_resp = client.get(
            f"/api/sessions/{parent['tmux_name']}/wait-status", headers=headers
        )
        self.assertEqual(status_resp.status_code, 200)
        self.assertIsNone(status_resp.json())

        self.manager.set_attention(child["tmux_name"], state="ready_for_review", actor="test")
        self.manager.kill(child["tmux_name"])

        wait_resp = client.post(
            f"/api/sessions/{parent['tmux_name']}/wait-for-children",
            headers=headers,
            json={"timeout": 15, "poll_interval": 1},
        )
        self.assertEqual(wait_resp.status_code, 200)
        data = wait_resp.json()
        self.assertEqual(data["outcome"], "success")
        self.assertEqual(data["exit_code"], 0)


class SessionGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "scout", "reviewer", "researcher", "verifier", "bugfix", "release", "operator"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"agent-console-group-test-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            max_children_per_parent=10,
            max_managed_sessions=12,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.temp.cleanup()

    def test_create_group(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="group-parent",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("test-group", purpose="coordinate work", parent_session="group-parent")
        self.assertEqual(group["name"], "test-group")
        self.assertEqual(group["purpose"], "coordinate work")
        self.assertEqual(group["status"], "active")

    def test_list_groups(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="list-group-parent",
            repository=str(self.workspace),
        )
        self.manager.create_group("group-a", purpose="task A", parent_session="list-group-parent")
        self.manager.create_group("group-b", purpose="task B", parent_session="list-group-parent")
        groups = self.manager.list_groups()
        self.assertGreaterEqual(len(groups), 2)

    def test_group_without_parent(self) -> None:
        group = self.manager.create_group("standalone", purpose="no parent")
        self.assertEqual(group["name"], "standalone")
        self.assertIsNone(group["parent_session_id"])

    def test_add_session_to_group(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="add-to-group",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("member-test")
        updated = self.manager.add_group_session(group["id"], "add-to-group")
        self.assertEqual(updated["member_count"], 1)
        self.assertEqual(updated["sessions"][0]["tmux_name"], "add-to-group")

    def test_add_duplicate_session_to_group_raises(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="dup-group-test",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("dup-test")
        self.manager.add_group_session(group["id"], "dup-group-test")
        with self.assertRaises(ValueError):
            self.manager.add_group_session(group["id"], "dup-group-test")

    def test_remove_session_from_group(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="remove-from-group",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("remove-test")
        self.manager.add_group_session(group["id"], "remove-from-group")
        self.assertEqual(self.manager.get_group(group["id"])["member_count"], 1)
        updated = self.manager.remove_group_session(group["id"], "remove-from-group")
        self.assertEqual(updated["member_count"], 0)

    def test_remove_nonexistent_member_raises(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="no-remove-test",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("no-remove")
        with self.assertRaises(ValueError):
            self.manager.remove_group_session(group["id"], "no-remove-test")

    def test_create_group_with_unknown_parent_session_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.create_group("bad-parent", parent_session="nonexistent-session")

    def test_create_group_with_valid_parent_session(self) -> None:
        session = self.manager.create(
            tool="shell", profile="general", name="valid-parent-session",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("good-parent", purpose="with parent", parent_session="valid-parent-session")
        self.assertIsNotNone(group["parent_session_id"])

    def test_create_group_audit_uses_provided_actor(self) -> None:
        group = self.manager.create_group("audit-actor-test", actor="test-actor", surface="test-surface")
        with self.manager.database.connect() as conn:
            audit = conn.execute(
                "SELECT actor, surface, details_json FROM audit_events WHERE action='group.created' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(audit["actor"], "test-actor")
        self.assertEqual(audit["surface"], "test-surface")

    def test_add_session_to_nonexistent_group_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.add_group_session("grp-nonexistent", "any-session")

    def test_get_group_returns_members(self) -> None:
        s1 = self.manager.create(
            tool="shell", profile="general", name="get-group-s1",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("get-group")
        self.manager.add_group_session(group["id"], "get-group-s1")
        fetched = self.manager.get_group(group["id"])
        self.assertEqual(fetched["name"], "get-group")
        self.assertEqual(fetched["member_count"], 1)

    def test_list_groups_includes_members(self) -> None:
        s1 = self.manager.create(
            tool="shell", profile="general", name="list-groups-s1",
            repository=str(self.workspace),
        )
        group = self.manager.create_group("list-group-test")
        self.manager.add_group_session(group["id"], "list-groups-s1")
        groups = self.manager.list_groups()
        match = next(g for g in groups if g["id"] == group["id"])
        self.assertEqual(match["member_count"], 1)

    def test_list_groups_multi_group_calls_live_sessions_once(self) -> None:
        s1 = self.manager.create(
            tool="shell", profile="general", name="multi-grp-s1",
            repository=str(self.workspace),
        )
        s2 = self.manager.create(
            tool="shell", profile="general", name="multi-grp-s2",
            repository=str(self.workspace),
        )
        self.manager.kill("multi-grp-s2")
        g1 = self.manager.create_group("multi-group-a")
        self.manager.add_group_session(g1["id"], "multi-grp-s1")
        g2 = self.manager.create_group("multi-group-b")
        self.manager.add_group_session(g2["id"], "multi-grp-s2")
        with patch.object(self.manager, "_live_sessions",
                          wraps=self.manager._live_sessions) as spy:
            groups = self.manager.list_groups()
            spy.assert_called_once()
        self.assertEqual(len(groups), 2)
        for g in groups:
            self.assertEqual(g["member_count"], 1)
        s1_group = next(g for g in groups if any(m["tmux_name"] == "multi-grp-s1" for m in g["sessions"]))
        self.assertTrue(s1_group["sessions"][0]["running"])
        s2_group = next(g for g in groups if any(m["tmux_name"] == "multi-grp-s2" for m in g["sessions"]))
        self.assertFalse(s2_group["sessions"][0]["running"])

    def test_open_group_returns_running_and_stopped(self) -> None:
        s1 = self.manager.create(
            tool="shell", profile="general", name="open-group-s1",
            repository=str(self.workspace),
        )
        s2 = self.manager.create(
            tool="shell", profile="general", name="open-group-s2",
            repository=str(self.workspace),
        )
        self.manager.kill("open-group-s2")
        group = self.manager.create_group("open-group")
        self.manager.add_group_session(group["id"], "open-group-s1")
        self.manager.add_group_session(group["id"], "open-group-s2")
        result = self.manager.open_group(group["id"])
        self.assertEqual(result["member_count"], 2)
        available_names = {s["tmux_name"] for s in result["available"]}
        unavailable_names = {s["tmux_name"] for s in result["unavailable"]}
        self.assertIn("open-group-s1", available_names)
        self.assertIn("open-group-s2", unavailable_names)

    def test_delegation_guard_uses_schema(self) -> None:
        parent = self.manager.create(
            tool="shell", profile="general", name="deleg-guard-parent",
            repository=str(self.workspace),
        )
        from agent_console.profiles import PROFILE_SCHEMA
        parent_profile_meta = PROFILE_SCHEMA.get("general", {})
        delegatable = parent_profile_meta.get("allowed_delegation_profiles", frozenset())
        for name, meta in PROFILE_SCHEMA.items():
            if name in delegatable:
                result = self.manager.delegate(
                    profile=name, parent=parent["id"], task="test delegation with schema guard", tool="shell",
                )
                self.assertIn("session", result)
            else:
                with self.assertRaises(ValueError):
                    self.manager.delegate(
                        profile=name, parent=parent["id"], task="should fail", tool="shell",
                    )


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"agent-console-proj-test-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            max_children_per_parent=4,
            max_managed_sessions=8,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.temp.cleanup()

    def _audit_events(self) -> list[dict]:
        with self.manager.database.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM audit_events ORDER BY id"
            ).fetchall()]

    def test_create_project(self) -> None:
        (self.workspace / "repo").mkdir(exist_ok=True)
        p = self.manager.create_project(
            "test-proj", repository=str(self.workspace / "repo"), description="A test",
            actor="test-user", surface="web",
        )
        self.assertEqual(p["name"], "test-proj")
        expected_repo = str((self.workspace / "repo").resolve())
        self.assertEqual(p["repository"], expected_repo)
        self.assertEqual(p["status"], "active")
        audits = self._audit_events()
        create_audit = next(a for a in audits if a["action"] == "project.created")
        self.assertEqual(create_audit["actor"], "test-user")
        self.assertEqual(create_audit["surface"], "web")

    def test_create_project_canonicalizes_relative_path(self) -> None:
        (self.workspace / "sub").mkdir(exist_ok=True)
        p = self.manager.create_project("rel-proj", repository="sub")
        self.assertEqual(p["repository"], str((self.workspace / "sub").resolve()))

    def test_create_project_rejects_outside_repo(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create_project("outside-proj", repository="/etc")

    def test_create_project_without_existing_repo(self) -> None:
        p = self.manager.create_project(
            "future-proj", repository=str(self.workspace / "not-yet-cloned"),
            description="Repo does not exist yet",
        )
        expected_repo = str((self.workspace / "not-yet-cloned").resolve())
        self.assertEqual(p["repository"], expected_repo)
        self.assertEqual(p["status"], "active")

    def test_create_project_rejects_outside_repo_without_existing_path(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create_project(
                "outside-future-proj", repository="/tmp/nonexistent-outside",
            )

    def test_deferred_repo_validation_on_session_create(self) -> None:
        nonexistent = str(self.workspace / "not-yet-cloned")
        proj = self.manager.create_project(
            "deferred-valid-proj", repository=nonexistent,
        )
        self.assertEqual(proj["repository"], nonexistent)
        self.assertFalse(Path(nonexistent).exists())
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.manager.create(
                tool="shell", profile="general", name="deferred-valid-sess",
                repository=nonexistent, project_id=proj["id"],
            )

    def test_list_projects(self) -> None:
        repo_b = str(self.workspace / "repo-b")
        (self.workspace / "repo-b").mkdir(exist_ok=True)
        self.manager.create_project("proj-a")
        self.manager.create_project("proj-b", repository=repo_b)
        projects = self.manager.list_projects()
        self.assertGreaterEqual(len(projects), 2)

    def test_get_project(self) -> None:
        p = self.manager.create_project("get-proj")
        got = self.manager.get_project(p["id"])
        self.assertEqual(got["name"], "get-proj")
        self.assertIn("sessions", got)

    def test_update_project_with_audit(self) -> None:
        p = self.manager.create_project("upd-proj")
        updated = self.manager.update_project(
            p["id"], status="paused", actor="test-user", surface="web",
        )
        self.assertEqual(updated["status"], "paused")
        audits = self._audit_events()
        update_audit = next(a for a in audits if a["action"] == "project.updated")
        self.assertEqual(update_audit["actor"], "test-user")
        self.assertEqual(update_audit["surface"], "web")

    def test_delete_project_with_audit(self) -> None:
        p = self.manager.create_project("del-proj")
        self.manager.delete_project(p["id"], actor="test-user", surface="web")
        with self.assertRaises(KeyError):
            self.manager.get_project(p["id"])
        audits = self._audit_events()
        delete_audit = next(a for a in audits if a["action"] == "project.deleted")
        self.assertEqual(delete_audit["actor"], "test-user")

    def test_update_project_canonicalizes_repo(self) -> None:
        (self.workspace / "new-repo").mkdir(exist_ok=True)
        p = self.manager.create_project("upd-can-proj")
        updated = self.manager.update_project(p["id"], repository="new-repo")
        self.assertEqual(updated["repository"], str((self.workspace / "new-repo").resolve()))

    def test_update_project_repo_to_nonexistent_path(self) -> None:
        p = self.manager.create_project("upd-nonexist-proj")
        updated = self.manager.update_project(
            p["id"], repository=str(self.workspace / "future-clone"),
        )
        self.assertEqual(updated["repository"], str((self.workspace / "future-clone").resolve()))
        self.assertEqual(updated["name"], "upd-nonexist-proj")

    def test_update_project_rejects_repo_change_with_assigned_sessions(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("upd-repo-block-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="upd-repo-block-sess",
            repository=repo, project_id=proj["id"],
        )
        (self.workspace / "other-repo").mkdir(exist_ok=True)
        with self.assertRaises(ValueError):
            self.manager.update_project(proj["id"], repository=str(self.workspace / "other-repo"))

    def test_update_project_allows_name_change_with_assigned_sessions(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("upd-name-ok-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="upd-name-ok-sess",
            repository=repo, project_id=proj["id"],
        )
        updated = self.manager.update_project(proj["id"], name="renamed-proj")
        self.assertEqual(updated["name"], "renamed-proj")
        self.assertEqual(updated["repository"], repo)

    def test_delete_project_rejects_assigned_sessions(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("del-assigned-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="del-assigned-sess",
            repository=repo, project_id=proj["id"],
        )
        with self.assertRaises(ValueError):
            self.manager.delete_project(proj["id"])

    def test_delete_bad_id_returns_404(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.delete_project("proj-nonexistent")

    def test_create_project_duplicate_name_ok(self) -> None:
        p1 = self.manager.create_project("dup-proj")
        p2 = self.manager.create_project("dup-proj")
        self.assertNotEqual(p1["id"], p2["id"])

    def test_assign_session_to_project(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("assign-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="assign-sess",
            repository=repo, project_id=proj["id"],
        )
        self.assertIsNotNone(session.get("id"))
        self.assertEqual(session.get("project_id"), proj["id"])

    def test_assign_session_repo_mismatch(self) -> None:
        repo_a = str(self.workspace / "repo-a")
        repo_b = str(self.workspace / "repo-b")
        (self.workspace / "repo-a").mkdir(exist_ok=True)
        (self.workspace / "repo-b").mkdir(exist_ok=True)
        proj = self.manager.create_project("mismatch-proj", repository=repo_a)
        session = self.manager.create(
            tool="shell", profile="general", name="mismatch-sess",
            repository=repo_b,
        )
        with self.assertRaises(ValueError):
            self.manager.assign_session_to_project(
                "mismatch-sess", proj["id"], actor="test-user", surface="web",
            )

    def test_assign_session_without_project_repo_to_project_with_repo_rejected(self) -> None:
        proj_repo = str(self.workspace / "proj-repo")
        sess_repo = str(self.workspace)
        (self.workspace / "proj-repo").mkdir(exist_ok=True)
        proj = self.manager.create_project("sess-no-proj-repo-proj", repository=proj_repo)
        self.manager.create(
            tool="shell", profile="general", name="sess-no-proj-repo-sess",
            repository=sess_repo,
        )
        with self.assertRaises(ValueError):
            self.manager.assign_session_to_project("sess-no-proj-repo-sess", proj["id"])

    def test_assign_session_not_found(self) -> None:
        proj = self.manager.create_project("no-sess-proj")
        with self.assertRaises(KeyError):
            self.manager.assign_session_to_project("no-such-session", proj["id"])

    def test_assign_to_bad_project_id(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.assign_session_to_project("any-session", "proj-bad-id")

    def test_unassign_session(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("unassign-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="unassign-sess",
            repository=repo, project_id=proj["id"],
        )
        proj_after = self.manager.unassign_session_from_project(
            "unassign-sess", proj["id"], actor="test-user", surface="web",
        )
        self.assertEqual(len(proj_after.get("sessions", [])), 0)
        audits = self._audit_events()
        unassign_audit = next(a for a in audits if a["action"] == "project.session.unassigned")
        self.assertEqual(unassign_audit["actor"], "test-user")
        self.assertIn("unassign-sess", unassign_audit["target"])

    def test_unassign_not_assigned(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("unassign-not-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="unassign-not",
            repository=repo,
        )
        with self.assertRaises(ValueError):
            self.manager.unassign_session_from_project("unassign-not", proj["id"])

    def test_unassign_wrong_project_id(self) -> None:
        repo = str(self.workspace)
        proj_a = self.manager.create_project("unassign-wrong-a", repository=repo)
        proj_b = self.manager.create_project("unassign-wrong-b", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="unassign-wrong-sess",
            repository=repo, project_id=proj_a["id"],
        )
        with self.assertRaises(ValueError):
            self.manager.unassign_session_from_project("unassign-wrong-sess", proj_b["id"])

    def test_unassign_bad_session(self) -> None:
        proj = self.manager.create_project("unassign-bad-sess-proj")
        with self.assertRaises(KeyError):
            self.manager.unassign_session_from_project("no-such-session", proj["id"])

    def test_unassign_bad_project_id(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.unassign_session_from_project("any-session", "proj-bad-id")

    def test_create_session_with_project_repo_enforced(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("enforce-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="enforce-sess",
            repository=repo, project_id=proj["id"],
        )
        self.assertEqual(session["project_id"], proj["id"])

    def test_create_session_rejects_incompatible_repo(self) -> None:
        repo_proj = str(self.workspace / "proj-repo")
        repo_sess = str(self.workspace / "sess-repo")
        (self.workspace / "proj-repo").mkdir(exist_ok=True)
        (self.workspace / "sess-repo").mkdir(exist_ok=True)
        proj = self.manager.create_project("repo-enforce-proj", repository=repo_proj)
        with self.assertRaises(ValueError):
            self.manager.create(
                tool="shell", profile="general", name="bad-repo-sess",
                repository=repo_sess, project_id=proj["id"],
            )

    def test_create_session_with_bad_project_id(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.create(
                tool="shell", profile="general", name="bad-proj-sess",
                repository=str(self.workspace), project_id="proj-no-such",
            )

    def test_create_session_with_paused_project_rejected(self) -> None:
        proj = self.manager.create_project("paused-proj")
        self.manager.update_project(proj["id"], status="paused")
        with self.assertRaises(ValueError):
            self.manager.create(
                tool="shell", profile="general", name="paused-sess",
                repository=str(self.workspace), project_id=proj["id"],
            )

    def test_unassign_then_delete_ok(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("unassign-then-del-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="unassign-then-del-sess",
            repository=repo, project_id=proj["id"],
        )
        self.manager.unassign_session_from_project("unassign-then-del-sess", proj["id"])
        self.manager.delete_project(proj["id"])
        with self.assertRaises(KeyError):
            self.manager.get_project(proj["id"])

    def test_restart_rejects_paused_project(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("restart-paused-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="restart-paused-sess",
            repository=repo, project_id=proj["id"],
        )
        self.manager.update_project(proj["id"], status="paused")
        with self.assertRaises(ValueError):
            self.manager.restart("restart-paused-sess")

    def test_launcher_includes_project_env_vars(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("launcher-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="launcher-proj-sess",
            repository=repo, project_id=proj["id"],
        )
        launcher = Path(session["launcher_path"]).read_text(encoding="utf-8")
        self.assertIn(f"AGENT_CONSOLE_PROJECT_ID={proj['id']}", launcher)
        self.assertIn("AGENT_CONSOLE_PROJECT_NAME=launcher-proj", launcher)
        self.assertIn(f"AGENT_CONSOLE_PROJECT_REPOSITORY={shlex.quote(repo)}", launcher)

    def test_context_includes_project_identity(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("ctx-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="ctx-proj-sess",
            repository=repo, project_id=proj["id"],
        )
        context = self.manager.session_context("ctx-proj-sess")
        self.assertIn(f"Project ID: {proj['id']}", context["context"])
        self.assertIn("Project name: ctx-proj", context["context"])
        self.assertIn(f"Project repository: {repo}", context["context"])

    def test_assign_audit_shows_actor_and_surface(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("audit-assign-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="audit-assign-sess",
            repository=repo,
        )
        self.manager.assign_session_to_project(
            "audit-assign-sess", proj["id"], actor="cli-admin", surface="CLI",
        )
        audits = self._audit_events()
        assign_audit = next(a for a in audits if a["action"] == "project.session.assigned")
        self.assertEqual(assign_audit["actor"], "cli-admin")
        self.assertEqual(assign_audit["surface"], "CLI")

    def test_assign_to_paused_project_rejected(self) -> None:
        proj = self.manager.create_project("paused-assign-proj")
        self.manager.update_project(proj["id"], status="paused")
        self.manager.create(
            tool="shell", profile="general", name="paused-assign-sess",
            repository=str(self.workspace),
        )
        with self.assertRaises(ValueError):
            self.manager.assign_session_to_project("paused-assign-sess", proj["id"])

    def test_assign_duplicate_to_same_project_rejected(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("dup-assign-proj", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="dup-assign-sess",
            repository=repo, project_id=proj["id"],
        )
        with self.assertRaises(ValueError):
            self.manager.assign_session_to_project("dup-assign-sess", proj["id"])

    def test_assign_to_different_project_rejected(self) -> None:
        repo = str(self.workspace)
        proj_a = self.manager.create_project("diff-assign-a", repository=repo)
        proj_b = self.manager.create_project("diff-assign-b", repository=repo)
        self.manager.create(
            tool="shell", profile="general", name="diff-assign-sess",
            repository=repo, project_id=proj_a["id"],
        )
        with self.assertRaises(ValueError):
            self.manager.assign_session_to_project("diff-assign-sess", proj_b["id"])

    def test_restart_with_active_project_ok(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("restart-ok-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="restart-ok-sess",
            repository=repo, project_id=proj["id"],
        )
        self.assertTrue(session["running"])
        restarted = self.manager.restart("restart-ok-sess")
        self.assertTrue(restarted["running"])
        self.assertEqual(restarted.get("project_id"), proj["id"])


if __name__ == "__main__":
    unittest.main()
