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
            "id": "cheap", "model": "opencode-go/cheap", "provider": "opencode-go",
            "name": "Cheap", "status": "active", "selectable": True,
            "cost": {"input": 0.1, "output": 0.2, "cache_read": None, "reasoning": None},
            "limits": {"context": 1000, "output": 100},
            "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
        }]
        with patch.object(self.manager.models, "list", return_value={"provider": "opencode-go", "models": models}):
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
