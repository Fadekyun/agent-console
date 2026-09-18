from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_console.config import Settings
from agent_console.database import Database
from agent_console.manager import SessionManager
from agent_console.providers import TOOL_BINARIES
from agent_console.skills import (
    assign_skill,
    get_shared_skills,
    isolate_skills,
    resolve_session_skills,
)


def _write_skill(
    root: Path,
    name: str,
    *,
    kind: str = "standard",
    tools: list[str] | None = None,
    allowed_profiles: list[str] | None = None,
    requires_approval: bool = False,
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: Test {name}", f"kind: {kind}"]
    if tools:
        lines.append("tools: " + ", ".join(tools))
    if allowed_profiles:
        lines.append("allowed_profiles: " + ", ".join(allowed_profiles))
    if requires_approval:
        lines.append("requires_approval: true")
    lines.append("---")
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class SharedSkillSettingsTests(unittest.TestCase):
    def test_from_env_defaults_to_an_empty_allowlist(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGCONSOLE_SHARED_SKILLS", None)
            self.assertEqual(Settings.from_env().shared_skills, ())

    def test_from_env_parses_and_dedupes_an_explicit_allowlist(self) -> None:
        with mock.patch.dict(
            os.environ, {"AGCONSOLE_SHARED_SKILLS": "typesafe-ai, other, typesafe-ai,,"}
        ):
            self.assertEqual(
                Settings.from_env().shared_skills, ("typesafe-ai", "other")
            )


class SharedSkillResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "canonical"
        self.root.mkdir()
        _write_skill(self.root, "shared-one")
        _write_skill(self.root, "shared-codex-only", tools=["codex"])
        _write_skill(self.root, "shared-super", kind="superpower")
        _write_skill(self.root, "shared-coder-only", allowed_profiles=["coder"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_includes_standard_allowlisted_skill_for_declared_tool(self) -> None:
        result = get_shared_skills(
            "general", "codex", allowlist=["shared-one"], canonical_root=self.root
        )
        self.assertEqual([skill["name"] for skill in result], ["shared-one"])
        self.assertTrue(result[0]["shared"])
        self.assertFalse(result[0]["requires_approval"])

    def test_strips_blanks_and_dedupes_the_allowlist(self) -> None:
        result = get_shared_skills(
            "general",
            "codex",
            allowlist=[" shared-one ", "shared-one", ""],
            canonical_root=self.root,
        )
        self.assertEqual([skill["name"] for skill in result], ["shared-one"])

    def test_skips_a_skill_that_does_not_declare_the_tool(self) -> None:
        result = get_shared_skills(
            "general", "hermes", allowlist=["shared-codex-only"], canonical_root=self.root
        )
        self.assertEqual(result, [])

    def test_absent_skill_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "not in the catalog"):
            get_shared_skills(
                "general", "codex", allowlist=["missing"], canonical_root=self.root
            )

    def test_superpower_is_never_shared(self) -> None:
        with self.assertRaisesRegex(ValueError, "only standard"):
            get_shared_skills(
                "general", "codex", allowlist=["shared-super"], canonical_root=self.root
            )

    def test_approval_required_standard_skill_is_rejected(self) -> None:
        _write_skill(self.root, "shared-approval", requires_approval=True)
        with self.assertRaisesRegex(ValueError, "requires explicit approval"):
            get_shared_skills(
                "general",
                "codex",
                allowlist=["shared-approval"],
                canonical_root=self.root,
            )
        with self.assertRaisesRegex(ValueError, "requires explicit approval"):
            resolve_session_skills(
                self.db_for_approval(),
                "general",
                "codex",
                shared_allowlist=["shared-approval"],
                canonical_root=self.root,
            )

    def db_for_approval(self) -> Database:
        db = Database(Path(self.temp.name) / "approval.sqlite3")
        db.migrate()
        return db

    def test_profile_disallowed_skill_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed for profile"):
            get_shared_skills(
                "general",
                "codex",
                allowlist=["shared-coder-only"],
                canonical_root=self.root,
            )
        allowed = get_shared_skills(
            "coder", "codex", allowlist=["shared-coder-only"], canonical_root=self.root
        )
        self.assertEqual([skill["name"] for skill in allowed], ["shared-coder-only"])

    def test_stale_source_fails_clearly(self) -> None:
        broken = self.root / "broken-source"
        broken.mkdir()
        # A directory without SKILL.md is not discovered at all; simulate a
        # discovered-but-unreadable source through a broken symlink instead.
        (broken / "SKILL.md").symlink_to(self.root / "does-not-exist.md")
        with self.assertRaisesRegex(ValueError, "broken-source"):
            get_shared_skills(
                "general", "codex", allowlist=["broken-source"], canonical_root=self.root
            )


class SharedSkillMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "canonical"
        self.root.mkdir()
        _write_skill(self.root, "assigned-one")
        _write_skill(self.root, "shared-two")
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.db.migrate()
        assign_skill(self.db, "coder", "assigned-one", canonical_root=self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_assignment_wins_the_name_collision_and_shared_skills_are_added(self) -> None:
        result = resolve_session_skills(
            self.db,
            "coder",
            "codex",
            shared_allowlist=["assigned-one", "shared-two"],
            canonical_root=self.root,
        )
        self.assertEqual(
            [skill["name"] for skill in result["validation"]["effective"]],
            ["assigned-one"],
        )
        self.assertEqual(
            [skill["name"] for skill in result["materialized"]],
            ["assigned-one", "shared-two"],
        )
        assigned = next(s for s in result["materialized"] if s["name"] == "assigned-one")
        self.assertNotIn("shared", assigned)
        shared = next(s for s in result["materialized"] if s["name"] == "shared-two")
        self.assertTrue(shared["shared"])

    def test_isolated_root_contains_merged_skills_only(self) -> None:
        result = resolve_session_skills(
            self.db,
            "coder",
            "codex",
            shared_allowlist=["shared-two"],
            canonical_root=self.root,
        )
        isolated = Path(self.temp.name) / "isolated"
        isolate_skills(isolated, self.root, result["materialized"])
        self.assertTrue((isolated / "assigned-one").is_symlink())
        self.assertTrue((isolated / "shared-two").is_symlink())
        self.assertEqual(sorted(p.name for p in isolated.iterdir()), ["assigned-one", "shared-two"])

    def test_no_allowlist_keeps_assignment_only_behaviour(self) -> None:
        result = resolve_session_skills(
            self.db, "coder", "codex", shared_allowlist=(), canonical_root=self.root
        )
        self.assertEqual(result["shared"], [])
        self.assertEqual(
            [skill["name"] for skill in result["materialized"]], ["assigned-one"]
        )


@unittest.skipUnless(shutil.which("tmux"), "tmux required")
class SharedSkillSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "bugfix"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.skills_root = root / "canonical"
        self.skills_root.mkdir()
        _write_skill(self.skills_root, "typesafe-ai")
        _write_skill(self.skills_root, "unrelated-skill")
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.fake_agent = self.bin_dir / "fake-agent"
        self.fake_agent.write_text("#!/bin/sh\nsleep 120\n", encoding="utf-8")
        self.fake_agent.chmod(0o755)
        self.socket = f"agent-console-shared-{os.getpid()}-{id(self)}"
        self.settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            legacy_tmux_socket_path=root / "legacy" / "tmux.sock",
            max_children_per_parent=2,
            max_managed_sessions=6,
            shared_skills=("typesafe-ai",),
        )
        self.manager = SessionManager(self.settings)
        (self.manager.auth.codex_home("default") / "auth.json").write_text("{}\n", encoding="utf-8")
        self.env_patch = mock.patch.dict(
            os.environ, {"AGCONSOLE_SKILLS_ROOT": str(self.skills_root)}
        )
        self.env_patch.start()
        self.binary_patch = mock.patch.dict(
            TOOL_BINARIES,
            {
                "codex": self.fake_agent,
                "codex-pro": self.fake_agent,
                "hermes": self.fake_agent,
                "opencode": self.fake_agent,
            },
        )
        self.binary_patch.start()

    def tearDown(self) -> None:
        self.binary_patch.stop()
        self.env_patch.stop()
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.temp.cleanup()

    def configure_commandcode(self) -> None:
        from agent_console.commandcode import BASE_URL, DEFAULT_MODEL

        data = self.manager.auth._read()
        data["contexts"]["hermes"]["commandcode-main"] = {
            "provider": "commandcode",
            "kind": "api-key",
            "secret_ref": "commandcode-main",
            "base_url": BASE_URL,
            "model": DEFAULT_MODEL,
            "models": [DEFAULT_MODEL],
            "enabled": True,
            "verified": True,
        }
        data["defaults"]["hermes"] = "commandcode-main"
        self.manager.auth._write(data)
        secret = self.manager.auth.secret_path("commandcode-main")
        secret.write_text("export CMD_API_KEY=fixture-key\n", encoding="utf-8")
        secret.chmod(0o600)

    def isolated_root(self, name: str) -> Path:
        return self.settings.state_dir / "skills-isolated" / name

    def test_codex_session_materializes_shared_skill_in_isolated_root(self) -> None:
        self.manager.create(
            tool="codex", profile="general", name="codex-shared", repository=str(self.workspace)
        )
        isolated = self.isolated_root("codex-shared")
        self.assertTrue((isolated / "typesafe-ai").is_symlink())
        self.assertFalse((isolated / "unrelated-skill").exists())
        overlay_skills = self.settings.state_dir / "tool-overlays" / "codex-shared" / "codex-home" / "skills"
        self.assertTrue((overlay_skills / "typesafe-ai" / "SKILL.md").is_file())

    def test_codex_restart_refreshes_materialized_shared_skill(self) -> None:
        self.manager.create(
            tool="codex", profile="general", name="codex-restart", repository=str(self.workspace)
        )
        isolated = self.isolated_root("codex-restart")
        (isolated / "typesafe-ai").unlink()
        self.manager.restart("codex-restart")
        self.assertTrue((isolated / "typesafe-ai").is_symlink())

    def test_hermes_session_writes_shared_external_dirs(self) -> None:
        self.configure_commandcode()
        self.manager.create(
            tool="hermes", profile="general", name="hermes-shared", repository=str(self.workspace)
        )
        hermes_home = self.settings.state_dir / "contexts" / "hermes-shared-hermes"
        config = json.loads((hermes_home / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            config["skills"]["external_dirs"], [str(self.isolated_root("hermes-shared"))]
        )
        isolated = self.isolated_root("hermes-shared")
        self.assertTrue((isolated / "typesafe-ai").is_symlink())
        self.assertFalse((isolated / "unrelated-skill").exists())

    def test_hermes_restart_adds_external_dirs_without_losing_config(self) -> None:
        self.configure_commandcode()
        self.manager.create(
            tool="hermes", profile="general", name="hermes-restart", repository=str(self.workspace)
        )
        hermes_home = self.settings.state_dir / "contexts" / "hermes-restart-hermes"
        config_path = hermes_home / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        # Simulate a session created before shared-skill support: no skills block,
        # plus an operator-added setting that must survive the restart.
        config.pop("skills", None)
        config["agent"] = {"reasoning_effort": "high", "custom_marker": "keep-me"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        self.manager.restart("hermes-restart")

        refreshed = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            refreshed["skills"]["external_dirs"],
            [str(self.isolated_root("hermes-restart"))],
        )
        self.assertEqual(refreshed["agent"]["custom_marker"], "keep-me")
        self.assertEqual(refreshed["agent"]["reasoning_effort"], "high")
        self.assertEqual(refreshed["model"], config["model"])

    def test_hermes_restart_preserves_operator_external_dir(self) -> None:
        self.configure_commandcode()
        self.manager.create(
            tool="hermes", profile="general", name="hermes-operator-dir", repository=str(self.workspace)
        )
        hermes_home = self.settings.state_dir / "contexts" / "hermes-operator-dir-hermes"
        config_path = hermes_home / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["skills"] = {"external_dirs": ["/opt/operator-skills"]}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        self.manager.restart("hermes-operator-dir")

        refreshed = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            refreshed["skills"]["external_dirs"],
            ["/opt/operator-skills", str(self.isolated_root("hermes-operator-dir"))],
        )

    def test_hermes_restart_is_idempotent(self) -> None:
        self.configure_commandcode()
        self.manager.create(
            tool="hermes", profile="general", name="hermes-idempotent", repository=str(self.workspace)
        )
        self.manager.restart("hermes-idempotent")
        self.manager.restart("hermes-idempotent")
        config = json.loads(
            (self.settings.state_dir / "contexts" / "hermes-idempotent-hermes" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            config["skills"]["external_dirs"],
            [str(self.isolated_root("hermes-idempotent"))],
        )

    def test_missing_shared_skill_blocks_create_without_side_effects(self) -> None:
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=self.settings.state_dir,
            database_path=self.settings.database_path,
            profile_dir=self.profile_dir,
            handoff_dir=self.settings.handoff_dir,
            worktree_root=self.settings.worktree_root,
            tmux_socket=self.socket,
            legacy_tmux_socket_path=self.settings.legacy_tmux_socket_path,
            max_managed_sessions=6,
            shared_skills=("missing-shared-skill",),
        )
        manager = SessionManager(settings)
        with self.assertRaisesRegex(ValueError, "not in the catalog"):
            manager.create(
                tool="codex", profile="general", name="codex-missing", repository=str(self.workspace)
            )
        self.assertFalse(manager.tmux.exists("codex-missing"))
        self.assertFalse((settings.state_dir / "skills-isolated" / "codex-missing").exists())

    def test_empty_allowlist_materializes_no_shared_skill(self) -> None:
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=self.settings.state_dir,
            database_path=self.settings.database_path,
            profile_dir=self.profile_dir,
            handoff_dir=self.settings.handoff_dir,
            worktree_root=self.settings.worktree_root,
            tmux_socket=self.socket,
            legacy_tmux_socket_path=self.settings.legacy_tmux_socket_path,
            max_managed_sessions=6,
            shared_skills=(),
        )
        manager = SessionManager(settings)
        manager.create(
            tool="codex", profile="general", name="codex-no-shared", repository=str(self.workspace)
        )
        isolated = settings.state_dir / "skills-isolated" / "codex-no-shared"
        self.assertEqual(list(isolated.iterdir()), [])

    def test_opencode_session_configures_isolated_skill_root(self) -> None:
        models = [{
            "id": "cheap", "model": "opencode-go/cheap", "provider": "opencode-go",
            "name": "Cheap", "status": "active", "selectable": True,
            "cost": {"input": 0.1, "output": 0.2, "cache_read": None, "reasoning": None},
            "limits": {"context": 1000, "output": 100},
            "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
        }]
        with mock.patch.object(self.manager.models, "list", return_value={"models": models}):
            self.manager.create(
                tool="opencode", profile="general", name="opencode-shared",
                repository=str(self.workspace),
            )
        launcher = (self.settings.state_dir / "launchers" / "opencode-shared.sh").read_text()
        exports = {}
        for line in launcher.splitlines():
            if line.startswith("export "):
                tokens = shlex.split(line)
                if len(tokens) >= 2 and "=" in tokens[1]:
                    key, _, value = tokens[1].partition("=")
                    exports[key] = value
        content = json.loads(exports["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content["skills"], [str(self.isolated_root("opencode-shared"))])
        self.assertTrue((self.isolated_root("opencode-shared") / "typesafe-ai").is_symlink())
        self.assertFalse((self.isolated_root("opencode-shared") / "unrelated-skill").exists())

    def test_assigned_skill_still_blocks_non_isolating_harness(self) -> None:
        self.configure_commandcode()
        _write_skill(self.skills_root, "assigned-hermes")
        assign_skill(
            self.manager.database,
            "general",
            "assigned-hermes",
            canonical_root=self.skills_root,
        )
        with self.assertRaisesRegex(RuntimeError, "cannot isolate per-session skills"):
            self.manager.create(
                tool="hermes", profile="general", name="hermes-assigned", repository=str(self.workspace)
            )


if __name__ == "__main__":
    unittest.main()
