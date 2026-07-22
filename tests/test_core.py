from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.providers import LaunchSpec
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
            "id": "cheap", "model": "opencode-go/cheap", "provider": "opencode-go",
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
        self.assertIn("opencode-go/cheap", args)
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

    def test_create_project(self) -> None:
        p = self.manager.create_project("test-proj", repository="/workspace/repo", description="A test")
        self.assertEqual(p["name"], "test-proj")
        self.assertEqual(p["repository"], "/workspace/repo")
        self.assertEqual(p["status"], "active")

    def test_list_projects(self) -> None:
        self.manager.create_project("proj-a")
        self.manager.create_project("proj-b", repository="/workspace/repo")
        projects = self.manager.list_projects()
        self.assertGreaterEqual(len(projects), 2)

    def test_get_project(self) -> None:
        p = self.manager.create_project("get-proj")
        got = self.manager.get_project(p["id"])
        self.assertEqual(got["name"], "get-proj")
        self.assertIn("sessions", got)

    def test_update_project(self) -> None:
        p = self.manager.create_project("upd-proj")
        updated = self.manager.update_project(p["id"], status="paused")
        self.assertEqual(updated["status"], "paused")

    def test_delete_project(self) -> None:
        p = self.manager.create_project("del-proj")
        self.manager.delete_project(p["id"])
        with self.assertRaises(KeyError):
            self.manager.get_project(p["id"])

    def test_assign_session_to_project(self) -> None:
        repo = str(self.workspace)
        proj = self.manager.create_project("assign-proj", repository=repo)
        session = self.manager.create(
            tool="shell", profile="general", name="assign-sess",
            repository=repo, project_id=proj["id"],
        )
        self.assertIsNotNone(session.get("id"))

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
            self.manager.assign_session_to_project("mismatch-sess", proj["id"])


if __name__ == "__main__":
    unittest.main()
