from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from agent_console.config import Settings, safe_parse_port
from agent_console.manager import SessionManager
from agent_console.deployer import (
    DeploymentMode,
    Deployer,
    FakeServiceRunner,
    ProductionServiceRunner,
    ServiceConfig,
    ServiceRunner,
    validate_manifest_at,
)


def _make_source(temp_root: Path, name: str = "src", content_suffix: str = "") -> Path:
    src = temp_root / name
    src.mkdir()
    (src / "app.py").write_text(f"print('hello{content_suffix}')\n", encoding="utf-8")
    (src / "config.yaml").write_text(f"key: value{content_suffix}\n", encoding="utf-8")
    nested = src / "subdir"
    nested.mkdir()
    (nested / "util.py").write_text(f"# helper{content_suffix}\n", encoding="utf-8")
    return src





class DeployerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.releases_root = self.root / "releases"
        self.deployer = Deployer(self.releases_root, runner=FakeServiceRunner(), source_tracker=None)
        self.source = _make_source(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create(self, *, candidate_sha: str | None = None) -> dict:
        return self.deployer.create_release(
            self.source, candidate_sha=candidate_sha or uuid.uuid4().hex[:12]
        )

    # --- Manifest rejection ---

    def test_manifest_missing_rejected(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "manifest.json").unlink()
        result = self.deployer.validate_release(rel["release_name"])
        self.assertFalse(result["valid"])
        self.assertIn("manifest.json not found", result["error"])

    def test_manifest_tampered_file_rejected(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "app.py").write_text("TAMPERED\n", encoding="utf-8")
        result = self.deployer.validate_release(rel["release_name"])
        self.assertFalse(result["valid"])
        self.assertIn("SHA mismatch", result["error"])

    def test_manifest_missing_file_rejected(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "subdir" / "util.py").unlink()
        result = self.deployer.validate_release(rel["release_name"])
        self.assertFalse(result["valid"])
        self.assertIn("file missing", result["error"])

    def test_manifest_valid_passes(self) -> None:
        rel = self._create()
        result = self.deployer.validate_release(rel["release_name"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["file_count"], 3)

    def test_manifest_valid_with_extra_file(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "extra.txt").write_text("extra\n", encoding="utf-8")
        result = self.deployer.validate_release(rel["release_name"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["file_count"], 3)

    def test_manifest_corrupt_json_rejected(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "manifest.json").write_text("not json\n", encoding="utf-8")
        result = self.deployer.validate_release(rel["release_name"])
        self.assertFalse(result["valid"])
        self.assertIn("parse error", result["error"])

    # --- Blocker 4: Artifact build exclusions ---

    def test_excludes_git_directory(self) -> None:
        (self.source / ".git").mkdir()
        (self.source / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / ".git").exists(), ".git must be excluded")

    def test_excludes_pycache(self) -> None:
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "foo.cpython-313.pyc").write_text("x", encoding="utf-8")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / "__pycache__").exists())

    def test_excludes_pyc_file(self) -> None:
        (self.source / "compiled.pyc").write_text("x", encoding="utf-8")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / "compiled.pyc").exists())

    def test_excludes_env_file(self) -> None:
        (self.source / ".env").write_text("SECRET=key\n", encoding="utf-8")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / ".env").exists())

    def test_excludes_venv(self) -> None:
        (self.source / ".venv").mkdir()
        (self.source / ".venv" / "bin").mkdir(parents=True)
        (self.source / ".venv" / "bin" / "python").write_text("#!/bin/false")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / ".venv").exists())

    def test_excludes_node_modules(self) -> None:
        (self.source / "node_modules").mkdir(parents=True)
        (self.source / "node_modules" / "pkg").mkdir()
        (self.source / "node_modules" / "pkg" / "index.js").write_text("// x")
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / "node_modules").exists())

    def test_manifest_records_source_sha(self) -> None:
        sha = uuid.uuid4().hex[:12]
        rel = self._create(candidate_sha=sha)
        rel_path = Path(rel["release_path"])
        manifest = json.loads((rel_path / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("source_sha"), sha)
        result = self.deployer.validate_release(rel["release_name"])
        self.assertEqual(result.get("source_sha"), sha)

    # --- Blocker 3: Containment ---

    def test_rejects_path_traversal_release_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.deployer._release_path("../etc/passwd")
        self.assertIn("invalid release name", str(ctx.exception).lower())

    def test_rejects_symlink_escape_in_link_target(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "manifest.json").write_text(
            json.dumps({"files": {}}), encoding="utf-8"
        )
        bad_link = self.releases_root / "current"
        bad_link.symlink_to(outside)
        target = self.deployer._link_target("current")
        self.assertIsNone(target, "symlink escape must be rejected and cleaned up")
        self.assertFalse(bad_link.exists(), "escaped symlink must be removed")

    def test_rejects_release_name_with_dotdot(self) -> None:
        with self.assertRaises(ValueError):
            self.deployer._contained_release_name("release-../../foo")

    def test_list_releases_ignores_non_release_dirs(self) -> None:
        (self.releases_root / "not-a-release").mkdir()
        (self.releases_root / "tmp-file").write_text("x")
        self.assertEqual(len(self.deployer.list_releases()), 0)

    def test_select_rejects_nonexistent_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.deployer.select_release("release-no-such-thing")
        self.assertIn("not found", str(ctx.exception))

    # --- Blocker 1: deploy_apply must NOT change current ---

    def test_canary_does_not_change_current(self) -> None:
        rel = self._create()
        self.assertIsNone(self.deployer.current_release())
        self.deployer.promote_canary(rel["release_name"])
        self.assertIsNone(self.deployer.current_release(),
                          "promote_canary must NOT set current symlink")

    def test_deploy_apply_no_current_change(self) -> None:
        rel1 = self._create()
        self.deployer.select_release(rel1["release_name"])
        rel2 = self._create()
        current_before = self.deployer.current_release()
        self.deployer.promote_canary(rel2["release_name"])
        current_after = self.deployer.current_release()
        self.assertEqual(current_before["release_name"], current_after["release_name"],
                         "promote_canary must preserve existing current")

    # --- Atomic selection / rollback ---

    def test_select_release_creates_current_symlink(self) -> None:
        rel = self._create()
        result = self.deployer.select_release(rel["release_name"])
        self.assertEqual(result["release_name"], rel["release_name"])
        self.assertIsNone(result.get("previous_release"))
        self.assertTrue(result.get("is_first"))
        current_link = self.releases_root / "current"
        self.assertTrue(current_link.is_symlink())
        self.assertEqual(current_link.resolve(), Path(rel["release_path"]))

    def test_select_tmp_cleaned(self) -> None:
        rel = self._create()
        self.deployer.select_release(rel["release_name"])
        self.assertFalse((self.releases_root / ".current.tmp").exists())

    def test_select_validates_before_selection(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "app.py").write_text("TAMPERED\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.deployer.select_release(rel["release_name"])
        self.assertIn("validation failed", str(ctx.exception))
        self.assertFalse((self.releases_root / "current").is_symlink())

    def test_rollback_to_previous(self) -> None:
        src2 = _make_source(self.root, "src2", "2")
        rel1 = self._create()
        self.deployer.select_release(rel1["release_name"])
        time.sleep(1.1)
        rel2 = self.deployer.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        self.deployer.select_release(rel2["release_name"])
        self.assertEqual(self.deployer.current_release()["release_name"], rel2["release_name"])
        rollback = self.deployer.rollback("previous")
        self.assertEqual(rollback["release_name"], rel1["release_name"])
        self.assertEqual(self.deployer.current_release()["release_name"], rel1["release_name"])

    def test_rollback_no_previous_raises(self) -> None:
        rel = self._create()
        self.deployer.select_release(rel["release_name"])
        with self.assertRaises(ValueError) as ctx:
            self.deployer.rollback("previous")
        self.assertIn("no previous release", str(ctx.exception))

    def test_rollback_no_current_raises(self) -> None:
        self._create()
        with self.assertRaises(ValueError) as ctx:
            self.deployer.rollback("previous")
        self.assertIn("no current release", str(ctx.exception))

    def test_rollback_named_target(self) -> None:
        src2 = _make_source(self.root, "src2", "2")
        rel1 = self._create()
        self.deployer.select_release(rel1["release_name"])
        time.sleep(0.05)
        rel2 = self.deployer.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        rollback = self.deployer.rollback(target=rel1["release_name"])
        self.assertEqual(rollback["release_name"], rel1["release_name"])

    # --- Blocker 2: Service runner contract ---

    def test_user_service_calls_runner_restart_and_health(self) -> None:
        runner = FakeServiceRunner()
        d = Deployer(self.releases_root, source_tracker=None, runner=runner)
        src = _make_source(self.root, "run-src")
        rel = d.create_release(src, candidate_sha=uuid.uuid4().hex[:12])
        d.promote_canary(rel["release_name"])
        result = d.promote_user_service(rel["release_name"])
        self.assertEqual(result["status"], "user_service_active")
        self.assertEqual(len(runner.restart_calls), 1)
        self.assertEqual(len(runner.health_calls), 2,
                         "one from promote_canary explicit check, one from promote_user_service health check")

    def test_user_service_rolls_back_on_restart_failure(self) -> None:
        runner = FakeServiceRunner(restart_ok=False)
        d = Deployer(self.releases_root, source_tracker=None, runner=runner)
        rel1 = d.create_release(self.source, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel1["release_name"])
        src2 = _make_source(self.root, "src2", "2")
        rel2 = d.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        d.promote_canary(rel2["release_name"])
        result = d.promote_user_service(rel2["release_name"])
        self.assertIn("restart_failed", result["status"])
        self.assertEqual(self.deployer.current_release()["release_name"], rel1["release_name"],
                         "must roll back to previous current on restart failure")

    def test_user_service_rolls_back_on_health_failure(self) -> None:
        runner = FakeServiceRunner(restart_ok=True, health_ok=True)
        d = Deployer(self.releases_root, source_tracker=None, runner=runner)
        rel1 = d.create_release(self.source, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel1["release_name"])
        src2 = _make_source(self.root, "src2", "2")
        rel2 = d.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        d.promote_canary(rel2["release_name"])
        runner.health_ok = False
        result = d.promote_user_service(rel2["release_name"])
        self.assertIn("health_failed", result["status"])
        self.assertEqual(self.deployer.current_release()["release_name"], rel1["release_name"],
                         "must roll back to previous current on health failure")

    def test_rollback_calls_runner_restart(self) -> None:
        runner = FakeServiceRunner()
        d = Deployer(self.releases_root, source_tracker=None, runner=runner)
        rel1 = d.create_release(self.source, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel1["release_name"])
        time.sleep(1.1)
        src2 = _make_source(self.root, "src2", "2")
        rel2 = d.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel2["release_name"])
        result = d.rollback("previous")
        self.assertEqual(len(runner.restart_calls), 1)
        self.assertEqual(result["status"], "rollback_applied")

    def test_rollback_restores_prior_on_restart_failure(self) -> None:
        runner = FakeServiceRunner(restart_ok=False)
        d = Deployer(self.releases_root, source_tracker=None, runner=runner)
        rel1 = d.create_release(self.source, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel1["release_name"])
        time.sleep(1.1)
        rel2 = d.create_release(self.source, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel2["release_name"])
        result = d.rollback("previous")
        self.assertIn("restart_failed", result["status"])
        self.assertEqual(d.current_release()["release_name"], rel2["release_name"],
                         "must stay on current rollback target if restart fails")

    # --- Canary / user-service ---

    def test_canary_does_not_set_current(self) -> None:
        rel = self._create()
        self.deployer.promote_canary(rel["release_name"])
        self.assertIsNone(self.deployer.current_release())
        self.assertIsNotNone(self.deployer.canary_release())

    def test_canary_then_user_service_sets_current(self) -> None:
        rel = self._create()
        self.deployer.promote_canary(rel["release_name"])
        result = self.deployer.promote_user_service(rel["release_name"])
        self.assertEqual(result["release_name"], rel["release_name"])
        current = self.deployer.current_release()
        self.assertEqual(current["release_name"], rel["release_name"])

    def test_user_service_without_canary_raises(self) -> None:
        rel = self._create()
        with self.assertRaises(ValueError) as ctx:
            self.deployer.promote_user_service(rel["release_name"])
        self.assertIn("no canary release", str(ctx.exception))

    def test_user_service_wrong_release_raises(self) -> None:
        src2 = _make_source(self.root, "src2", "2")
        rel1 = self._create()
        rel2 = self.deployer.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        self.deployer.promote_canary(rel1["release_name"])
        with self.assertRaises(ValueError) as ctx:
            self.deployer.promote_user_service(rel2["release_name"])
        self.assertIn("not the current canary", str(ctx.exception))

    # --- Persistent state outside releases ---

    def test_release_dir_is_separate_from_source(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        self.assertTrue(rel_path.parent.samefile(self.releases_root))
        self.assertFalse(rel_path.samefile(self.source))

    def test_multiple_releases_independent(self) -> None:
        src2 = _make_source(self.root, "src2", "2")
        rel1 = self._create()
        rel2 = self.deployer.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        p1 = Path(rel1["release_path"])
        p2 = Path(rel2["release_path"])
        self.assertNotEqual(rel1["release_name"], rel2["release_name"])
        self.assertTrue(p1.is_dir())
        self.assertTrue(p2.is_dir())
        self.assertNotEqual((p1 / "app.py").read_text(), (p2 / "app.py").read_text())

    def test_release_not_mutated_by_source_changes(self) -> None:
        rel = self._create()
        (self.source / "new_file.txt").write_text("new\n", encoding="utf-8")
        rel_path = Path(rel["release_path"])
        self.assertFalse((rel_path / "new_file.txt").exists())

    def test_validation_rejects_outside_manifest(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "manifest.json").write_text(
            json.dumps({"files": {"ghost.py": "deadbeef"}}), encoding="utf-8"
        )
        result = self.deployer.validate_release(rel["release_name"])
        self.assertFalse(result["valid"])
        self.assertIn("file missing", result["error"])

    def test_current_returns_none_when_not_selected(self) -> None:
        self.assertIsNone(self.deployer.current_release())

    def test_current_returns_info(self) -> None:
        rel = self._create()
        self.deployer.select_release(rel["release_name"])
        current = self.deployer.current_release()
        self.assertIsNotNone(current)
        self.assertEqual(current["release_name"], rel["release_name"])
        self.assertTrue(current["valid"])

    def test_list_releases_all_present(self) -> None:
        src2 = _make_source(self.root, "src2", "2")
        rel1 = self._create()
        time.sleep(0.01)
        rel2 = self.deployer.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        releases = self.deployer.list_releases()
        self.assertEqual(len(releases), 2)
        names = {r["release_name"] for r in releases}
        self.assertIn(rel1["release_name"], names)
        self.assertIn(rel2["release_name"], names)

    def test_list_releases_marked_current_and_canary(self) -> None:
        rel = self._create()
        self.deployer.select_release(rel["release_name"])
        self.deployer.promote_canary(rel["release_name"])
        releases = self.deployer.list_releases()
        self.assertEqual(len(releases), 1)
        self.assertTrue(releases[0]["is_current"])
        self.assertTrue(releases[0]["is_canary"])

    def test_list_shows_source_sha(self) -> None:
        sha = uuid.uuid4().hex[:12]
        rel = self._create(candidate_sha=sha)
        releases = self.deployer.list_releases()
        self.assertIn("source_sha", releases[0])
        self.assertEqual(releases[0]["source_sha"], sha)

    def test_canary_promotion(self) -> None:
        rel = self._create()
        canary = self.deployer.promote_canary(rel["release_name"])
        self.assertEqual(canary["release_name"], rel["release_name"])
        self.assertIsNone(canary.get("previous_canary"))
        canary_info = self.deployer.canary_release()
        self.assertIsNotNone(canary_info)
        self.assertEqual(canary_info["release_name"], rel["release_name"])

    def test_validation_rejects_outside_manifest_source_sha(self) -> None:
        rel = self._create()
        rel_path = Path(rel["release_path"])
        (rel_path / "manifest.json").write_text(
            json.dumps({"files": {}, "source_sha": "bad"}), encoding="utf-8"
        )
        result = self.deployer.validate_release(rel["release_name"])
        self.assertTrue(result["valid"], "empty manifest with source_sha only is valid")
        self.assertEqual(result.get("source_sha"), "bad")


class DeployerManagerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "reviewer", "verifier", "scout", "release"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.releases_root = root / "releases"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            releases_root=self.releases_root,
            tmux_socket=None,
            max_children_per_parent=4,
            max_managed_sessions=12,
        )
        self.manager = SessionManager(settings)
        from agent_console.deployer import Deployer, FakeServiceRunner
        self.manager._deployer_override = Deployer(
            self.releases_root, runner=FakeServiceRunner(), source_tracker=None,
        )
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(self.workspace), "config", "user.email", "integ@test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workspace), "config", "user.name", "Integ Test"],
            check=True,
        )
        (self.workspace / "README.md").write_text("integ\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workspace), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "-qm", "initial"], check=True)
        self.sha = subprocess.run(
            ["git", "-C", str(self.workspace), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _setup_plan(self) -> str:
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        art = self.manager.settings.handoff_dir / plan_id
        art.mkdir(parents=True)
        (art / "plan.md").write_text("# Test plan\n", encoding="utf-8")
        (art / "metadata.json").write_text(
            json.dumps({
                "title": plan_id,
                "repository": str(self.workspace),
                "repository_revision": self.sha,
                "profile": "planner",
            }),
            encoding="utf-8",
        )
        self.manager.list_plans()
        return plan_id

    def _make_plan_pass_gate(self, plan_id: str) -> None:
        with self.manager.database.connect() as conn:
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, self.sha, "review", "pass",
                 "sess-mock", "mock-reviewer", "2026-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, self.sha, "scout", "pass",
                 "sess-mock", "mock-scout", "2026-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, self.sha, "verification", "pass",
                 "sess-mock", "mock-verifier", "2026-01-01T00:00:00"),
            )

    # --- Promotion gate refusal / no-deployer behavior ---

    def test_deploy_apply_without_gate_raises(self) -> None:
        plan_id = self._setup_plan()
        with self.assertRaises(ValueError) as ctx:
            self.manager.deploy_apply(plan_id, confirmed=True)
        self.assertIn("release gate is not passed", str(ctx.exception))

    def test_deploy_apply_plan_preview_no_confirmation(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        result = self.manager.deploy_apply(plan_id, confirmed=False)
        self.assertEqual(result["status"], "deploy_planned")
        self.assertIn("deployer_note", result)

    def test_deploy_apply_creates_release_and_canary_only(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        self.manager.promote_plan(plan_id)
        result = self.manager.deploy_apply(plan_id, confirmed=True)
        self.assertEqual(result["status"], "canary_selected")
        self.assertIn("release_name", result)
        self.assertIn("note", result)
        self.assertIn("current", result["note"])
        canary = self.manager.deployer.canary_release()
        self.assertIsNotNone(canary)
        self.assertEqual(canary["release_name"], result["release_name"])
        current = self.manager.deployer.current_release()
        self.assertIsNone(current,
                          "deploy_apply must NOT set current symlink; only canary")

    def test_deploy_apply_duplicate_raises(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        self.manager.promote_plan(plan_id)
        self.manager.deploy_apply(plan_id, confirmed=True)
        with self.assertRaises(FileExistsError) as ctx:
            self.manager.deploy_apply(plan_id, confirmed=True)
        self.assertIn("already exists", str(ctx.exception))

    def test_promote_plan_still_promotion_selected(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        result = self.manager.promote_plan(plan_id)
        self.assertEqual(result["status"], "promotion_selected")
        self.assertIn("deployer_note", result)
        self.assertNotEqual(result.get("status"), "released")

    def test_promote_user_service_requires_canary(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.promote_user_service("nonexistent")
        self.assertIn("no canary release", str(ctx.exception))

    def test_rollback_without_current_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.rollback_release()
        self.assertIn("no current release", str(ctx.exception))

    def test_promote_user_service_after_canary(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        self.manager.promote_plan(plan_id)
        result = self.manager.deploy_apply(plan_id, confirmed=True)
        promoted = self.manager.promote_user_service(result["release_name"])
        self.assertEqual(promoted["release_name"], result["release_name"])
        current = self.manager.deployer.current_release()
        self.assertIsNotNone(current)
        self.assertEqual(current["release_name"], result["release_name"])

    def test_releases_not_exposed_via_web_mutation(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi not available")
        from agent_console.web import create_app
        app = create_app(self.manager)
        deploy_paths = [
            route.path for route in app.routes
            if hasattr(route, "path") and "/api/deploy/" in route.path
        ]
        self.assertIn("/api/deploy/releases", deploy_paths)
        self.assertIn("/api/deploy/current", deploy_paths)
        self.assertIn("/api/deploy/canary", deploy_paths)
        non_get = [
            route.path for route in app.routes
            if hasattr(route, "methods") and route.methods and not route.methods.issubset({"GET", "HEAD"})
            and "/api/deploy/" in route.path
        ]
        self.assertEqual(non_get, [], "no mutating deploy endpoints should exist")

    def test_persistent_state_outside_release(self) -> None:
        plan_id = self._setup_plan()
        self._make_plan_pass_gate(plan_id)
        self.manager.promote_plan(plan_id)
        self.manager.deploy_apply(plan_id, confirmed=True)
        db_stat = self.manager.database.path.stat()
        src2 = _make_source(self.workspace, "other")
        d = self.manager.deployer
        rel2 = d.create_release(src2, candidate_sha=uuid.uuid4().hex[:12])
        d.select_release(rel2["release_name"])
        db_stat2 = self.manager.database.path.stat()
        self.assertEqual(db_stat.st_ino, db_stat2.st_ino,
                         "DB inode unchanged = state persisted outside releases")


class DeploymentModeTests(unittest.TestCase):
    """ProductionServiceRunner must reject all actions unless mode is exactly staging."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.release_path = self.root / "release-some"
        self.release_path.mkdir()
        (self.release_path / "app.py").write_text("x", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    # --- disabled mode ---

    def _assert_disabled(self, runner: ProductionServiceRunner) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            runner.start_canary(self.release_path, "127.0.0.1", 33100)
        self.assertIn("disabled", str(ctx.exception).lower())
        with self.assertRaises(RuntimeError) as ctx:
            runner.stop_canary()
        self.assertIn("disabled", str(ctx.exception).lower())
        with self.assertRaises(RuntimeError) as ctx:
            runner.check_health()
        self.assertIn("disabled", str(ctx.exception).lower())
        with self.assertRaises(RuntimeError) as ctx:
            runner.restart()
        self.assertIn("disabled", str(ctx.exception).lower())

    def test_disabled_mode_rejects_all_runner_actions(self) -> None:
        runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.DISABLED))
        self._assert_disabled(runner)

    def test_default_mode_is_disabled(self) -> None:
        runner = ProductionServiceRunner()
        self._assert_disabled(runner)

    # --- staging mode ---

    def test_staging_mode_does_not_raise_runtime_error(self) -> None:
        runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
        runner._check_mode()

    def test_staging_mode_proceeds_to_runner_implementation(self) -> None:
        runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("mock no uvicorn")):
            result = runner.start_canary(self.release_path, "127.0.0.1", 33100)
            self.assertFalse(result, "staging: reaches Popen but subprocess fails")
        with mock.patch("httpx.get", side_effect=Exception("mock connection refused")):
            result = runner.check_health()
            self.assertFalse(result, "staging: reaches httpx.get but connection fails")

    def test_restart_waits_for_service_health(self) -> None:
        runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch.object(subprocess, "run", return_value=completed), \
             mock.patch.object(runner, "check_health", side_effect=[False, True]) as health, \
             mock.patch("agent_console.deployer.time.sleep") as sleep:
            self.assertTrue(runner.restart())
        self.assertEqual(health.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_restart_fails_when_service_never_becomes_healthy(self) -> None:
        runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch.object(subprocess, "run", return_value=completed), \
             mock.patch.object(runner, "check_health", return_value=False) as health, \
             mock.patch("agent_console.deployer.time.sleep"):
            self.assertFalse(runner.restart())
        self.assertEqual(health.call_count, 30)

    # --- production / unrecognized mode ---

    def test_production_mode_rejected_in_settings(self) -> None:
        settings = Settings(
            workspace_root=self.root,
            state_dir=self.root / "state",
            database_path=self.root / "state" / "test.sqlite3",
            profile_dir=self.root / "profiles",
            handoff_dir=self.root / "handoffs",
            worktree_root=self.root / "worktrees",
            tmux_socket=None,
            deployment_mode="production",
        )
        with self.assertRaises(ValueError):
            DeploymentMode(settings.deployment_mode)

    def test_unrecognized_mode_rejected_in_settings(self) -> None:
        settings = Settings(
            workspace_root=self.root,
            state_dir=self.root / "state",
            database_path=self.root / "state" / "test.sqlite3",
            profile_dir=self.root / "profiles",
            handoff_dir=self.root / "handoffs",
            worktree_root=self.root / "worktrees",
            tmux_socket=None,
            deployment_mode="unrecognized",
        )
        with self.assertRaises(ValueError):
            DeploymentMode(settings.deployment_mode)


class DeployerModeFailClosedTests(unittest.TestCase):
    """ProductionServiceRunner in non-staging mode (disabled/production/unrecognized)
    must block all runner actions and prevent canary/current symlink mutation
    through the public Deployer path."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.releases_root = self.root / "releases"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_source(self) -> Path:
        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("print('ok')\n", encoding="utf-8")
        return src

    def _assert_fail_closed(self, config: ServiceConfig) -> None:
        runner = ProductionServiceRunner(config)
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        rel = d.create_release(src, candidate_sha="deadbeef0001")
        self.assertTrue(d.validate_release(rel["release_name"])["valid"])
        with self.assertRaises(RuntimeError) as ctx:
            d.promote_canary(rel["release_name"])
        self.assertIn("disabled", str(ctx.exception).lower())
        self.assertIsNone(d.canary_release(),
                          "canary symlink must not be set when runner is disabled")
        self.assertIsNone(d.current_release(),
                          "current symlink must not be set when runner is disabled")
        with self.assertRaises(RuntimeError):
            runner.start_canary(Path(rel["release_path"]), "127.0.0.1", 33100)
        with self.assertRaises(RuntimeError):
            runner.stop_canary()
        with self.assertRaises(RuntimeError):
            runner.check_health()
        with self.assertRaises(RuntimeError):
            runner.restart()

    def test_disabled_mode_fail_closed(self) -> None:
        self._assert_fail_closed(ServiceConfig(deployment_mode=DeploymentMode.DISABLED))

    def test_default_mode_fail_closed(self) -> None:
        self._assert_fail_closed(ServiceConfig())

    def test_staging_failure_does_not_set_link(self) -> None:
        runner = ProductionServiceRunner(
            ServiceConfig(deployment_mode=DeploymentMode.STAGING)
        )
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("mock")):
            with mock.patch("httpx.get", side_effect=Exception("mock")):
                rel = d.create_release(src, candidate_sha="deadbeef0002")
                with self.assertRaises(RuntimeError) as ctx:
                    d.promote_canary(rel["release_name"])
                self.assertIn("canary verification failed", str(ctx.exception).lower())
        self.assertIsNone(d.canary_release(),
                          "canary symlink must not be set on start failure even in staging")

    def test_staging_mocked_success_sets_canary_link(self) -> None:
        runner = ProductionServiceRunner(
            ServiceConfig(deployment_mode=DeploymentMode.STAGING)
        )
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        rel = d.create_release(src, candidate_sha="deadbeef0003")
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        with mock.patch.object(subprocess, "Popen", return_value=mock_proc):
            with mock.patch("httpx.get", return_value=mock_resp):
                canary = d.promote_canary(rel["release_name"])
        self.assertEqual(canary["release_name"], rel["release_name"])
        self.assertIsNotNone(d.canary_release())
        self.assertEqual(d.canary_release()["release_name"], rel["release_name"])
        self.assertIsNone(d.current_release(),
                          "promote_canary must NOT set current symlink")


class DeployerManagerFailClosedTests(unittest.TestCase):
    """SessionManager with deployment_mode='production' or unrecognized must
    produce a disabled ProductionServiceRunner that blocks actions and prevents
    canary/current symlink mutation through the public deployer path."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "reviewer", "verifier", "scout", "release"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.releases_root = root / "releases"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_source(self) -> Path:
        src = self.workspace / "src"
        src.mkdir()
        (src / "app.py").write_text("print('ok')\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(src), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(src), "commit", "-qm", "fixture"], check=True)
        return src

    def _assert_manager_fail_closed(self, deployment_mode: str) -> None:
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=self.temp.name / Path("state"),
            database_path=self.temp.name / Path("state") / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=self.temp.name / Path("handoffs"),
            worktree_root=self.workspace / "worktrees",
            releases_root=self.releases_root,
            tmux_socket=None,
            max_children_per_parent=4,
            max_managed_sessions=12,
            deployment_mode=deployment_mode,
        )
        manager = SessionManager(settings)
        self.assertIsNone(manager._deployer_override,
                          "manager must NOT have deployer override for this test")
        deployer = manager.deployer
        runner = deployer.runner
        self.assertIsInstance(runner, ProductionServiceRunner)
        src = self._make_source()
        sha = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        rel = deployer.create_release(src, candidate_sha=sha)
        self.assertTrue(deployer.validate_release(rel["release_name"])["valid"])
        with self.assertRaises(RuntimeError) as ctx:
            deployer.promote_canary(rel["release_name"])
        self.assertIn("disabled", str(ctx.exception).lower())
        self.assertIsNone(deployer.canary_release(),
                          "canary symlink must not be set when manager deployer is disabled")
        self.assertIsNone(deployer.current_release(),
                          "current symlink must not be set when manager deployer is disabled")

    def test_production_mode_fail_closed(self) -> None:
        self._assert_manager_fail_closed("production")

    def test_unrecognized_mode_fail_closed(self) -> None:
        self._assert_manager_fail_closed("unrecognized")

    def test_nonsense_mode_fail_closed(self) -> None:
        self._assert_manager_fail_closed("garbage")


class CanaryHealthCheckRegressionTests(unittest.TestCase):
    """promote_canary must stop on health check failure and preserve current/canary links."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.releases_root = self.root / "releases"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_source(self) -> Path:
        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("print('hello')\n", encoding="utf-8")
        return src

    def test_health_check_failure_before_canary_link_preserves_links(self) -> None:
        runner = FakeServiceRunner(canary_ok=True, health_ok=False)
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        rel = d.create_release(src, candidate_sha="test00000001")
        self.assertIsNone(d.canary_release(), "no canary before promote")
        with self.assertRaises(RuntimeError) as ctx:
            d.promote_canary(rel["release_name"])
        self.assertIn("health check failed", str(ctx.exception))
        self.assertIsNone(d.canary_release(), "canary link must not be set on failure")
        self.assertIsNone(d.current_release(), "current link must not change on failure")
        self.assertEqual(len(runner.canary_starts), 1, "start_canary was called")
        self.assertEqual(len(runner.canary_stops), 1, "stop_canary was called after failure")
        self.assertIn(33100, runner.health_calls, "check_health was called on the canary port")

    def test_health_check_passes_before_canary_link(self) -> None:
        runner = FakeServiceRunner(canary_ok=True, health_ok=True)
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        rel = d.create_release(src, candidate_sha="test00000002")
        canary = d.promote_canary(rel["release_name"])
        self.assertEqual(canary["release_name"], rel["release_name"])
        self.assertIsNotNone(d.canary_release(), "canary link was set")
        self.assertEqual(len(runner.health_calls), 1, "check_health called explicitly before canary link change")
        self.assertEqual(len(runner.canary_stops), 1, "canary stopped after health check passed")

    def test_health_check_failure_with_existing_canary_preserves_previous_canary(self) -> None:
        runner = FakeServiceRunner(canary_ok=True, health_ok=True)
        d = Deployer(self.releases_root, runner=runner, source_tracker=None)
        src = self._make_source()
        rel1 = d.create_release(src, candidate_sha="test00000003")
        d.promote_canary(rel1["release_name"])
        self.assertEqual(d.canary_release()["release_name"], rel1["release_name"])
        runner.health_ok = False
        rel2 = d.create_release(src, candidate_sha="test00000004")
        with self.assertRaises(RuntimeError) as ctx:
            d.promote_canary(rel2["release_name"])
        self.assertIn("health check failed", str(ctx.exception))
        self.assertEqual(d.canary_release()["release_name"], rel1["release_name"],
                         "existing canary must be preserved on failure")


class DeployConfigWireTests(unittest.TestCase):
    class SequenceHealthRunner(FakeServiceRunner):
        def __init__(self, results: list[bool]):
            super().__init__(health_ok=True, restart_ok=True, canary_ok=True)
            self.results = list(results)

        def check_health(self, *, port: int | None = None) -> bool:
            self.health_calls.append(port)
            return self.results.pop(0)

    def test_defaults_wired_to_service_config(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        workspace = root / "workspace"
        workspace.mkdir()
        profile_dir = root / "profiles"
        profile_dir.mkdir()
        for p in ("general", "planner", "coder", "reviewer", "scout", "release"):
            (profile_dir / f"{p}.md").write_text(f"# {p}\n", encoding="utf-8")
        settings = Settings(
            workspace_root=workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=workspace / "worktrees",
            tmux_socket=None,
            user_service_name="test-web.service",
            service_bind="0.0.0.0",
            service_port=3220,
            canary_bind="192.168.1.1",
            canary_port=9999,
            source_root=root / "custom-source",
        )
        manager = SessionManager(settings)
        runner = manager.deployer.runner
        self.assertEqual(runner.config.user_service_name, "test-web.service")
        self.assertEqual(runner.config.uvicorn_bin, str(settings.state_dir / "venv" / "bin" / "uvicorn"))
        self.assertEqual(runner.config.service_bind, "0.0.0.0")
        self.assertEqual(runner.config.service_port, 3220)
        self.assertEqual(runner.config.canary_bind, "192.168.1.1")
        self.assertEqual(runner.config.canary_port, 9999)

    def test_source_root_distinct_from_workspace(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        workspace = root / "ws"
        workspace.mkdir()
        source = root / "src"
        source.mkdir()
        profile_dir = root / "profiles"
        profile_dir.mkdir()
        for p in ("general", "planner", "coder", "reviewer", "scout", "release"):
            (profile_dir / f"{p}.md").write_text(f"# {p}\n", encoding="utf-8")
        settings = Settings(
            workspace_root=workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=workspace / "worktrees",
            tmux_socket=None,
            source_root=source,
        )
        self.assertNotEqual(settings.source_root, settings.workspace_root)

    def test_deploy_apply_uses_source_root(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        workspace = root / "ws"
        workspace.mkdir()
        source = workspace / "agent-console-source"
        source.mkdir()
        (source / "main.py").write_text("# source\n", encoding="utf-8")
        profile_dir = root / "profiles"
        profile_dir.mkdir()
        for p in ("general", "planner", "coder", "reviewer", "verifier", "scout", "release"):
            (profile_dir / f"{p}.md").write_text(f"# {p}\n", encoding="utf-8")
        releases_root = root / "releases"
        settings = Settings(
            workspace_root=workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=workspace / "worktrees",
            releases_root=releases_root,
            tmux_socket=None,
            source_root=source,
        )
        manager = SessionManager(settings)
        from agent_console.deployer import Deployer, FakeServiceRunner
        manager._deployer_override = Deployer(
            releases_root, runner=FakeServiceRunner(), source_tracker=None,
        )
        (workspace / "unrelated.txt").write_text("workspace-only\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "init source"], check=True)
        sha = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        art = manager.settings.handoff_dir / plan_id
        art.mkdir(parents=True)
        (art / "plan.md").write_text("# test\n", encoding="utf-8")
        (art / "metadata.json").write_text(
            json.dumps({"title": plan_id, "repository": str(source), "repository_revision": sha, "profile": "planner"}),
            encoding="utf-8",
        )
        manager.list_plans()
        with manager.database.connect() as conn:
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) VALUES(?,?,?,?,?,?,?,?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, sha, "review", "pass", "sess-m", "mock-r", "2026-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) VALUES(?,?,?,?,?,?,?,?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, sha, "scout", "pass", "sess-m", "mock-s", "2026-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO plan_evidence(id, plan_id, candidate_sha, evidence_type, result, session_id, session_name, recorded_at) VALUES(?,?,?,?,?,?,?,?)",
                (f"ev-{uuid.uuid4().hex}", plan_id, sha, "verification", "pass", "sess-m", "mock-v", "2026-01-01T00:00:00"),
            )
        manager.promote_plan(plan_id, candidate_sha=sha)
        result = manager.deploy_apply(plan_id, confirmed=True)
        self.assertEqual(result["status"], "canary_selected")
        self.assertEqual(result["source_root"], str(source))

    def test_custom_port_3220(self) -> None:
        config = ServiceConfig(service_port=3220)
        self.assertEqual(config.service_port, 3220)

    def test_invalid_port_fallback(self) -> None:
        config = ServiceConfig(service_port=0)
        self.assertEqual(config.service_port, 0)
        p = safe_parse_port("0", 3210)
        self.assertEqual(p, 3210)
        p2 = safe_parse_port("99999", 3210)
        self.assertEqual(p2, 3210)
        p3 = safe_parse_port("65536", 3210)
        self.assertEqual(p3, 3210)
        p4 = safe_parse_port("abc", 3210)
        self.assertEqual(p4, 3210)
        p5 = safe_parse_port("8080", 3210)
        self.assertEqual(p5, 8080)

    def _make_app_source(self, root: Path, name: str = "app") -> Path:
        src = root / name
        src.mkdir()
        (src / "agent_console").mkdir()
        (src / "agent_console" / "__init__.py").write_text("", encoding="utf-8")
        (src / "web" / "static").mkdir(parents=True)
        (src / "web" / "static" / "index.html").write_text("<!DOCTYPE html>\n", encoding="utf-8")
        node_modules = src / "node_modules" / "@xterm"
        (node_modules / "xterm" / "lib").mkdir(parents=True)
        (node_modules / "xterm" / "css").mkdir(parents=True)
        (node_modules / "addon-fit" / "lib").mkdir(parents=True)
        (node_modules / "xterm" / "lib" / "xterm.mjs").write_text("// xterm\n")
        (node_modules / "xterm" / "css" / "xterm.css").write_text("/* xterm */\n")
        (node_modules / "addon-fit" / "lib" / "addon-fit.mjs").write_text("// fit\n")
        return src

    def test_runtime_assets_in_manifest(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        src = self._make_app_source(root, "src")
        from agent_console.deployer import RUNTIME_ASSETS
        d = Deployer(root / "releases", runner=FakeServiceRunner(), source_tracker=None)
        rel = d.create_release(src, candidate_sha="assets001")
        self.assertIn("release_name", rel)
        rel_path = Path(rel["release_path"])
        manifest_path = rel_path / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text())
        for asset_rel in RUNTIME_ASSETS:
            self.assertIn(asset_rel, manifest["files"],
                          f"runtime asset {asset_rel} missing from manifest")
            self.assertTrue((rel_path / asset_rel).is_file(),
                            f"runtime asset {asset_rel} not copied to release")

    def test_runtime_assets_missing_raises(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        src = root / "src2"
        src.mkdir()
        (src / "agent_console").mkdir()
        (src / "agent_console" / "__init__.py").write_text("", encoding="utf-8")
        d = Deployer(root / "releases", runner=FakeServiceRunner(), source_tracker=None)
        with self.assertRaises(RuntimeError) as ctx:
            d.create_release(src, candidate_sha="missing")
        self.assertIn("missing required runtime asset", str(ctx.exception))

    def test_git_release_requires_exact_clean_sha_and_tracked_files(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        src = self._make_app_source(root, "git-src")
        (src / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        (src / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(src), "add", ".gitignore", "agent_console", "web", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(src), "commit", "-qm", "fixture"], check=True)
        sha = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        deployer = Deployer(root / "releases", runner=FakeServiceRunner(), source_tracker="git")

        with self.assertRaisesRegex(ValueError, "does not match source HEAD"):
            deployer.create_release(src, candidate_sha="0" * 40)

        (src / "untracked.txt").write_text("do not package\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must be clean"):
            deployer.create_release(src, candidate_sha=sha)
        (src / "untracked.txt").unlink()

        release = deployer.create_release(src, candidate_sha=sha)
        release_path = Path(release["release_path"])
        self.assertTrue((release_path / "tracked.txt").is_file())
        self.assertFalse((release_path / ".git").exists())

    def test_canary_uses_configured_bind_and_port(self) -> None:
        runner = FakeServiceRunner()
        runner.config = ServiceConfig(
            canary_bind="127.0.0.2",
            canary_port=33999,
            deployment_mode=DeploymentMode.STAGING,
        )
        root = Path(tempfile.mkdtemp())
        deployer = Deployer(root / "releases", runner=runner, source_tracker=None)
        source = self._make_app_source(root, "canary-src")
        release = deployer.create_release(source, candidate_sha="canaryconfig")
        deployer.promote_canary(release["release_name"])
        self.assertEqual(
            runner.canary_starts,
            [(Path(release["release_path"]), "127.0.0.2", 33999)],
        )

    def test_promotion_port_wired(self) -> None:
        runner = FakeServiceRunner()
        config = ServiceConfig(service_port=3220, deployment_mode=DeploymentMode.STAGING)
        runner.config = config
        d = Deployer(
            Path(tempfile.mkdtemp()) / "releases",
            runner=runner,
            source_tracker=None,
        )
        src_root = Path(tempfile.mkdtemp())
        src = self._make_app_source(src_root, "src")
        rel = d.create_release(src, candidate_sha="porttest01")
        d.promote_canary(rel["release_name"])
        d.select_release(rel["release_name"])
        d.promote_user_service(rel["release_name"])
        self.assertIn(3220, runner.health_calls)

    def test_first_promotion_health_failure_clears_current(self) -> None:
        runner = self.SequenceHealthRunner([True, False])
        runner.config = ServiceConfig(
            service_port=3220,
            deployment_mode=DeploymentMode.STAGING,
        )
        root = Path(tempfile.mkdtemp())
        deployer = Deployer(root / "releases", runner=runner, source_tracker=None)
        source = self._make_app_source(root, "first-release")
        release = deployer.create_release(source, candidate_sha="firstfailure")
        deployer.promote_canary(release["release_name"])
        result = deployer.promote_user_service(release["release_name"])
        self.assertEqual(result["status"], "health_failed_unselected")
        self.assertIsNone(deployer.current_release())

    def test_rollback_verifies_restored_release_health(self) -> None:
        runner = self.SequenceHealthRunner([True, False, True])
        runner.config = ServiceConfig(
            service_port=3220,
            deployment_mode=DeploymentMode.STAGING,
        )
        root = Path(tempfile.mkdtemp())
        deployer = Deployer(root / "releases", runner=runner, source_tracker=None)
        first = deployer.create_release(
            self._make_app_source(root, "restore-v1"), candidate_sha="restorev1"
        )
        deployer.select_release(first["release_name"])
        second = deployer.create_release(
            self._make_app_source(root, "restore-v2"), candidate_sha="restorev2"
        )
        deployer.promote_canary(second["release_name"])
        deployer.select_release(second["release_name"])
        result = deployer.rollback()
        self.assertEqual(result["status"], "rollback_health_failed_restored")
        self.assertEqual(deployer.current_release()["release_name"], second["release_name"])
        self.assertEqual(runner.health_calls, [33100, 3220, 3220])

    def test_rollback_health_failure_restores(self) -> None:
        runner = FakeServiceRunner(health_ok=True, restart_ok=True)
        config = ServiceConfig(service_port=3220, deployment_mode=DeploymentMode.STAGING)
        runner.config = config
        d = Deployer(
            Path(tempfile.mkdtemp()) / "releases",
            runner=runner,
            source_tracker=None,
        )
        src_root = Path(tempfile.mkdtemp())
        src1 = self._make_app_source(src_root, "v1")
        (src1 / "a.py").write_text("v1\n", encoding="utf-8")
        src2 = self._make_app_source(src_root, "v2")
        (src2 / "a.py").write_text("v2\n", encoding="utf-8")
        rel1 = d.create_release(src1, candidate_sha="rlv10001")
        d.select_release(rel1["release_name"])
        rel2 = d.create_release(src2, candidate_sha="rlv20001")
        d.promote_canary(rel2["release_name"])
        d.select_release(rel2["release_name"])
        runner.health_ok = False
        result = d.rollback()
        self.assertEqual(result["status"], "rollback_health_failed_restore_unhealthy")
        self.assertEqual(result["previous_release"], rel2["release_name"])
        current_after = d.current_release()
        self.assertEqual(current_after["release_name"], rel2["release_name"],
                         "must restore original release after health failure")


class TempPathSafetyTests(unittest.TestCase):
    def test_temp_paths_no_default_live_state(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        releases = root / "releases"
        d = Deployer(releases, runner=FakeServiceRunner(), source_tracker=None)
        src = root / "src"
        src.mkdir()
        (src / "f.txt").write_text("hello\n", encoding="utf-8")
        rel = d.create_release(src, candidate_sha="testsha00001")
        self.assertFalse(str(rel["release_path"]).startswith("/opt"))
        self.assertFalse(str(rel["release_path"]).startswith(str(Path.home() / ".local" / "share")))
        temp.cleanup()


if __name__ == "__main__":
    unittest.main()
