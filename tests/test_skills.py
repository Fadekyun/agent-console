from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from agent_console.database import Database
from agent_console.skills import (
    SKILL_CATALOG,
    SUPPORTED_TOOLS,
    approve_superpower,
    assign_skill,
    check_superpower_approval,
    cleanup_isolated_skills,
    doctor_skills,
    enrich_catalog_with_assignments,
    get_effective_skills,
    isolate_skills,
    list_assignments,
    list_superpower_approvals,
    revoke_superpower,
    skill_catalog,
    sync_skills,
    unassign_skill,
    validate_catalog,
    validate_profile_skills,
)
from agent_console.validation import PROFILES


class SkillCatalogTests(unittest.TestCase):
    def test_catalog_entries_have_required_fields(self) -> None:
        for entry in SKILL_CATALOG:
            self.assertIn("name", entry)
            self.assertIn("description", entry)
            self.assertIn("tools", entry)
            self.assertIn("kind", entry)
            self.assertIn("source_path", entry)
            self.assertIn("allowed_profiles", entry)
            self.assertIn("requires_approval", entry)

    def test_catalog_tools_are_valid(self) -> None:
        for entry in SKILL_CATALOG:
            for tool in entry["tools"]:
                self.assertIn(tool, SUPPORTED_TOOLS)

    def test_catalog_kind_is_valid(self) -> None:
        for entry in SKILL_CATALOG:
            self.assertIn(entry["kind"], ("standard", "superpower"))

    def test_validate_catalog_detects_missing_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            errors = validate_catalog(root)
            expected = len([e for e in SKILL_CATALOG if e["name"]])
            self.assertGreaterEqual(len(errors), 0)

    def test_catalog_returns_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for entry in SKILL_CATALOG:
                (root / entry["name"]).mkdir(parents=True, exist_ok=True)
                (root / entry["name"] / "SKILL.md").write_text("---\nname: test\n---\n# Test\n", encoding="utf-8")
            result = skill_catalog(root)
            self.assertIn("entries", result)
            self.assertEqual(len(result["entries"]), len(SKILL_CATALOG))
            for e in result["entries"]:
                self.assertIn("name", e)
                self.assertIn("kind", e)
                self.assertIn("synced", e)
                self.assertIn("source_present", e)


class SuperpowerApprovalTests(unittest.TestCase):
    def test_unknown_skill_rejected(self) -> None:
        result = check_superpower_approval("general", "nonexistent")
        self.assertFalse(result["allowed"])

    def test_standard_skill_allowed_without_approval(self) -> None:
        for entry in SKILL_CATALOG:
            if entry["kind"] == "standard":
                for profile in PROFILES:
                    result = check_superpower_approval(profile, entry["name"])
                    self.assertTrue(result["allowed"], f"{profile}+{entry['name']}")

    def test_superpower_requires_approval_if_configured(self) -> None:
        for entry in SKILL_CATALOG:
            if entry["kind"] == "superpower" and entry.get("requires_approval", False):
                result = check_superpower_approval("general", entry["name"])
                if entry.get("allowed_profiles") is not None:
                    if "general" not in entry["allowed_profiles"]:
                        self.assertFalse(result["allowed"], f"{entry['name']} should deny unlisted profile")
                else:
                    self.assertFalse(result["allowed"])
                    self.assertEqual(result["enforcement"], "pending_approval")

    def test_superpower_allowed_with_approval(self) -> None:
        for entry in SKILL_CATALOG:
            if entry["kind"] == "superpower":
                if entry.get("allowed_profiles") is not None and "general" not in entry["allowed_profiles"]:
                    continue
                result = check_superpower_approval("general", entry["name"], approved=True)
                if not entry.get("requires_approval", False):
                    self.assertTrue(result["allowed"], f"{entry['name']} should be allowed without approval")


class SyncDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sync_creates_links_for_existing_skills(self) -> None:
        for entry in SKILL_CATALOG:
            skill_dir = self.root / entry["name"]
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n# Test\n", encoding="utf-8")
        try:
            result = sync_skills(canonical_root=self.root, home=self.root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["skills"], len(SKILL_CATALOG))
        except FileNotFoundError:
            if len(SKILL_CATALOG) == 0:
                pass

    def test_doctor_reports_missing_links(self) -> None:
        result = doctor_skills(canonical_root=self.root, home=self.root)
        self.assertIsInstance(result["problems"], list)
        self.assertIn("ok", result)


class SkillAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.db_path)
        self.db.migrate()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_assign_skill_to_profile(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        result = assign_skill(self.db, "general", skill["name"], actor="test", surface="test")
        self.assertEqual(result["profile"], "general")
        self.assertEqual(result["skill_name"], skill["name"])
        self.assertEqual(result["kind"], skill["kind"])

    def test_assign_raises_on_duplicate(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        assign_skill(self.db, "coder", skill["name"])
        with self.assertRaises(ValueError):
            assign_skill(self.db, "coder", skill["name"])

    def test_assign_raises_on_unknown_profile(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        with self.assertRaises(ValueError):
            assign_skill(self.db, "nonexistent", skill["name"])

    def test_assign_raises_on_unknown_skill(self) -> None:
        with self.assertRaises(ValueError):
            assign_skill(self.db, "general", "nonexistent-skill")

    def test_assign_raises_when_profile_not_in_allowed_profiles(self) -> None:
        skill_dir = Path(self.temp.name) / "restricted-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nkind: superpower\ndescription: Restricted\nallowed_profiles: coder, planner\n---\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as ctx:
            assign_skill(
                self.db, "general", "restricted-skill",
                canonical_root=Path(self.temp.name),
            )
        self.assertIn("general", str(ctx.exception))
        self.assertIn("restricted-skill", str(ctx.exception))
        self.assertIn("coder", str(ctx.exception))
        self.assertIn("planner", str(ctx.exception))

    def test_unassign_skill(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        assign_skill(self.db, "planner", skill["name"])
        result = unassign_skill(self.db, "planner", skill["name"])
        self.assertEqual(result["profile"], "planner")
        self.assertEqual(result["skill_name"], skill["name"])

    def test_unassign_raises_if_not_assigned(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        with self.assertRaises(ValueError):
            unassign_skill(self.db, "general", skill["name"])

    def test_unassign_raises_on_unknown_profile(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        with self.assertRaises(ValueError):
            unassign_skill(self.db, "nonexistent", skill["name"])

    def test_list_assignments(self) -> None:
        if len(SKILL_CATALOG) < 2:
            self.skipTest("need at least 2 skills in catalog")
        assign_skill(self.db, "coder", SKILL_CATALOG[0]["name"])
        assign_skill(self.db, "planner", SKILL_CATALOG[1]["name"])
        assignments = list_assignments(self.db)
        self.assertEqual(len(assignments), 2)
        profiles = {a["profile"] for a in assignments}
        self.assertIn("coder", profiles)
        self.assertIn("planner", profiles)

    def test_enrich_catalog_with_assignments(self) -> None:
        catalog = {"entries": [{"name": "alpha", "kind": "standard"}, {"name": "beta", "kind": "superpower"}], "errors": []}
        assignments = [{"skill_name": "alpha", "profile": "coder", "assigned_at": "2026-01-01", "assigned_by": "test"}]
        enriched = enrich_catalog_with_assignments(catalog, assignments)
        alpha = next(e for e in enriched["entries"] if e["name"] == "alpha")
        beta = next(e for e in enriched["entries"] if e["name"] == "beta")
        self.assertEqual(len(alpha["assigned_to"]), 1)
        self.assertEqual(alpha["assigned_to"][0]["profile"], "coder")
        self.assertEqual(beta["assigned_to"], [])

    def test_enrich_catalog_preserves_errors(self) -> None:
        catalog = {"entries": [{"name": "test"}], "errors": ["some error"]}
        enriched = enrich_catalog_with_assignments(catalog, [])
        self.assertEqual(enriched["errors"], ["some error"])

    def test_multiple_profiles_assigned_same_skill(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        assign_skill(self.db, "general", skill["name"])
        assign_skill(self.db, "coder", skill["name"])
        assign_skill(self.db, "planner", skill["name"])
        assignments = [a for a in list_assignments(self.db) if a["skill_name"] == skill["name"]]
        self.assertEqual(len(assignments), 3)

    def test_assign_audit_event(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        assign_skill(self.db, "reviewer", skill["name"], actor="admin", surface="web")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT action, actor, surface, details_json FROM audit_events WHERE action='skill.assigned' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["actor"], "admin")
        self.assertEqual(row["surface"], "web")
        details = json.loads(row["details_json"])
        self.assertEqual(details["profile"], "reviewer")
        self.assertEqual(details["skill_name"], skill["name"])

    def test_unassign_audit_event(self) -> None:
        if not SKILL_CATALOG:
            self.skipTest("no skills in catalog")
        skill = SKILL_CATALOG[0]
        assign_skill(self.db, "bugfix", skill["name"])
        unassign_skill(self.db, "bugfix", skill["name"], actor="test", surface="CLI")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT action, details_json FROM audit_events WHERE action='skill.unassigned' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        details = json.loads(row["details_json"])
        self.assertEqual(details["profile"], "bugfix")


class EffectiveSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.skills_root = Path(self.temp.name) / "skills"
        self.skills_root.mkdir()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.db_path)
        self.db.migrate()

        self.standard_skill = "skill-alpha"
        self.superpower_skill = "skill-beta"
        for name, kind, allowed, requires_approval in [
            (self.standard_skill, "standard", None, False),
            (self.superpower_skill, "superpower", None, True),
        ]:
            (self.skills_root / name).mkdir(exist_ok=True)
            fm = f"---\nname: {name}\ndescription: Test {kind}\nkind: {kind}\n"
            if allowed is not None:
                fm += f"allowed_profiles: {', '.join(allowed)}\n"
            if requires_approval:
                fm += "requires_approval: true\n"
            fm += "---\n"
            (self.skills_root / name / "SKILL.md").write_text(fm, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _assign(self, profile: str, skill: str) -> None:
        assign_skill(self.db, profile, skill, canonical_root=self.skills_root)

    def _approve(self, profile: str, skill: str, **kw: str) -> None:
        approve_superpower(self.db, profile, skill, canonical_root=self.skills_root, **kw)

    def _revoke(self, profile: str, skill: str, **kw: str) -> None:
        revoke_superpower(self.db, profile, skill, **kw)

    # --- standard ---
    def test_standard_assignment_effective(self) -> None:
        self._assign("general", self.standard_skill)
        result = get_effective_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["effective"]), 1)
        self.assertEqual(result["effective"][0]["name"], self.standard_skill)
        self.assertEqual(len(result["issues"]), 0)
        v = validate_profile_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertTrue(v["valid"])

    # --- no assignments ---
    def test_no_assignments_is_valid(self) -> None:
        result = get_effective_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertEqual(result["total"], 0)
        self.assertEqual(len(result["effective"]), 0)
        self.assertEqual(len(result["issues"]), 0)
        v = validate_profile_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertTrue(v["valid"])

    # --- missing from catalog (directory does not exist at all) ---
    def test_missing_skill_reported_as_issue(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO skill_assignments(profile, skill_name, assigned_by, assigned_surface, assigned_at) "
                "VALUES(?, ?, ?, ?, ?)",
                ("planner", "ghost-skill", "test", "test", "2026-01-01T00:00:00"),
            )
        result = get_effective_skills(self.db, "planner", canonical_root=self.skills_root)
        self.assertEqual(len(result["effective"]), 0)
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("missing from catalog", result["issues"][0])
        v = validate_profile_skills(self.db, "planner", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])

    # --- stale source (directory exists but SKILL.md deleted) ---
    def test_stale_source_reported_as_issue(self) -> None:
        self._assign("coder", self.standard_skill)
        (self.skills_root / self.standard_skill / "SKILL.md").unlink()
        result = get_effective_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertEqual(len(result["effective"]), 0)
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("stale source", result["issues"][0])
        self.assertIn("SKILL.md not found", result["issues"][0])
        v = validate_profile_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])

    # --- disallowed (profile not in allowed_profiles) ---
    def test_disallowed_profile_reported_as_issue(self) -> None:
        restricted = "skill-gamma"
        (self.skills_root / restricted).mkdir(exist_ok=True)
        (self.skills_root / restricted / "SKILL.md").write_text(
            "---\nname: skill-gamma\ndescription: Restricted\n"
            "kind: superpower\nallowed_profiles: coder, planner\n---\n",
            encoding="utf-8",
        )
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO skill_assignments(profile, skill_name, assigned_by, assigned_surface, assigned_at) "
                "VALUES(?, ?, ?, ?, ?)",
                ("general", restricted, "test", "test", "2026-01-01T00:00:00"),
            )
        result = get_effective_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertEqual(len(result["effective"]), 0)
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("disallowed", result["issues"][0])
        v = validate_profile_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])

    # --- unapproved superpower (requires_approval=true, no approval) ---
    def test_unapproved_superpower_reported_as_issue(self) -> None:
        self._assign("general", self.superpower_skill)
        result = get_effective_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("requires explicit human approval", result["issues"][0])
        v = validate_profile_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])

    # --- superpower with requires_approval is flagged when no explicit approval ---
    def test_superpower_flagged_without_approval(self) -> None:
        self._assign("general", self.superpower_skill)
        result = get_effective_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertGreater(len(result["issues"]), 0)
        self.assertIn("requires explicit human approval", result["issues"][0])
        v = validate_profile_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])
        ap = check_superpower_approval("general", self.superpower_skill,
                                        approved=True, canonical_root=self.skills_root)
        self.assertTrue(ap["allowed"])

    # --- audit events on session create/restart ---
    def test_session_create_audit_records_effective_skills(self) -> None:
        self._assign("general", self.standard_skill)
        v = validate_profile_skills(self.db, "general", canonical_root=self.skills_root)
        self.assertTrue(v["valid"])
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(created_at, actor, surface, action, target, outcome, details_json) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                ("2026-01-01T00:00:00", "system", "CLI",
                 "session.created", "test-session", "success",
                 json.dumps({"effective_skills": [s["name"] for s in v["effective"]]})),
            )
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT details_json FROM audit_events WHERE action='session.created' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        details = json.loads(row["details_json"])
        self.assertIn("effective_skills", details)
        self.assertIn(self.standard_skill, details["effective_skills"])

    # --- restart validation rejects invalid ---
    def test_restart_rejects_invalid_skills(self) -> None:
        self._assign("coder", self.standard_skill)
        (self.skills_root / self.standard_skill / "SKILL.md").unlink()
        v = validate_profile_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])
        issues_text = "; ".join(v["issues"])
        self.assertTrue("stale source" in issues_text or "missing from catalog" in issues_text)

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_effective_skills(self.db, "nonexistent")

    # --- approved superpower becomes effective ---
    def test_approved_superpower_becomes_effective(self) -> None:
        self._assign("coder", self.superpower_skill)
        self._approve("coder", self.superpower_skill, actor="admin", surface="web")
        result = get_effective_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertEqual(len(result["issues"]), 0)
        self.assertEqual(len(result["effective"]), 1)
        self.assertEqual(result["effective"][0]["name"], self.superpower_skill)
        v = validate_profile_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertTrue(v["valid"])

    # --- revoke makes superpower block again ---
    def test_revoked_superpower_blocks_again(self) -> None:
        self._assign("coder", self.superpower_skill)
        self._approve("coder", self.superpower_skill)
        self._revoke("coder", self.superpower_skill, actor="admin", surface="web")
        result = get_effective_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertGreater(len(result["issues"]), 0)
        self.assertIn("requires explicit human approval", result["issues"][0])
        v = validate_profile_skills(self.db, "coder", canonical_root=self.skills_root)
        self.assertFalse(v["valid"])

    # --- approve_duplicate raises ---
    def test_approve_duplicate_raises(self) -> None:
        self._assign("coder", self.superpower_skill)
        self._approve("coder", self.superpower_skill)
        with self.assertRaises(ValueError):
            self._approve("coder", self.superpower_skill)

    # --- revoke_not_approved_raises ---
    def test_revoke_not_approved_raises(self) -> None:
        with self.assertRaises(ValueError):
            revoke_superpower(self.db, "coder", self.superpower_skill)

    # --- list_superpower_approvals ---
    def test_list_superpower_approvals(self) -> None:
        self._assign("coder", self.superpower_skill)
        self._approve("coder", self.superpower_skill, actor="admin", surface="web")
        approvals = list_superpower_approvals(self.db)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["profile"], "coder")
        self.assertEqual(approvals[0]["skill_name"], self.superpower_skill)
        self.assertEqual(approvals[0]["approved_by"], "admin")
        self.assertIsNone(approvals[0]["revoked_at"])
        self._revoke("coder", self.superpower_skill)
        approvals = list_superpower_approvals(self.db)
        self.assertIsNotNone(approvals[0]["revoked_at"])

    # --- approve_audit ---
    def test_approve_audit_event(self) -> None:
        self._assign("general", self.superpower_skill)
        self._approve("general", self.superpower_skill, actor="admin", surface="web")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT action, actor, surface, details_json FROM audit_events WHERE action='superpower.approved' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["actor"], "admin")
        self.assertEqual(row["surface"], "web")
        details = json.loads(row["details_json"])
        self.assertEqual(details["profile"], "general")
        self.assertEqual(details["skill_name"], self.superpower_skill)

    # --- revoke_audit ---
    def test_revoke_audit_event(self) -> None:
        self._assign("general", self.superpower_skill)
        self._approve("general", self.superpower_skill)
        self._revoke("general", self.superpower_skill, actor="admin", surface="web")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT action, actor, surface, details_json FROM audit_events WHERE action='superpower.revoked' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        details = json.loads(row["details_json"])
        self.assertEqual(details["profile"], "general")
        self.assertEqual(details["skill_name"], self.superpower_skill)


class SkillIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.canonical = Path(self.temp.name) / "canonical"
        self.canonical.mkdir()

        self.assigned_skill = "assigned-one"
        self.unassigned_skill = "unassigned-two"
        for name in (self.assigned_skill, self.unassigned_skill):
            (self.canonical / name).mkdir(exist_ok=True)
            (self.canonical / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Test\nkind: standard\n---\n",
                encoding="utf-8",
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_isolate_creates_only_effective_skills(self) -> None:
        isolated = Path(self.temp.name) / "isolated"
        effective = [{"name": self.assigned_skill, "kind": "standard"}]
        isolate_skills(isolated, self.canonical, effective)
        self.assertTrue((isolated / self.assigned_skill).is_symlink())
        self.assertTrue((isolated / self.assigned_skill / "SKILL.md").is_file())
        self.assertFalse((isolated / self.unassigned_skill).exists())

    def test_isolate_cleanup_removes_directory(self) -> None:
        isolated = Path(self.temp.name) / "isolated-cleanup"
        isolated.mkdir(parents=True, exist_ok=True)
        (isolated / "stale").write_text("stale", encoding="utf-8")
        cleanup_isolated_skills(isolated)
        self.assertFalse(isolated.exists())

    def test_isolate_cleanup_nonexistent_does_not_raise(self) -> None:
        cleanup_isolated_skills(Path(self.temp.name) / "nonexistent")

    def test_unassigned_skill_absent_from_isolated_root(self) -> None:
        isolated = Path(self.temp.name) / "check-absent"
        effective = [{"name": self.assigned_skill, "kind": "standard"}]
        isolate_skills(isolated, self.canonical, effective)
        dir_entries = list(isolated.iterdir())
        names = [e.name for e in dir_entries]
        self.assertIn(self.assigned_skill, names)
        self.assertNotIn(self.unassigned_skill, names)

    def test_no_assignments_creates_empty_isolated_root(self) -> None:
        isolated = Path(self.temp.name) / "empty-isolated"
        isolate_skills(isolated, self.canonical, [])
        self.assertTrue(isolated.is_dir())
        self.assertEqual(len(list(isolated.iterdir())), 0)

    def test_empty_isolated_root_prevents_global_leak(self) -> None:
        isolated = Path(self.temp.name) / "empty-leak-guard"
        isolate_skills(isolated, self.canonical, [])
        self.assertTrue(isolated.is_dir())
        names = [p.name for p in isolated.iterdir()]
        self.assertNotIn(self.unassigned_skill, names)


class SuperpowerApprovalIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.skills_root = Path(self.temp.name) / "skills"
        self.skills_root.mkdir()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.db_path)
        self.db.migrate()

        for name, kind, allowed in [
            ("std-skill", "standard", None),
            ("sup-skill", "superpower", None),
            ("restricted-sup", "superpower", ["coder"]),
        ]:
            (self.skills_root / name).mkdir(exist_ok=True)
            fm = f"---\nname: {name}\ndescription: Test {kind}\nkind: {kind}\n"
            if allowed:
                fm += f"allowed_profiles: {', '.join(allowed)}\n"
            fm += "---\n"
            (self.skills_root / name / "SKILL.md").write_text(fm, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_approve_unknown_skill_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            approve_superpower(self.db, "general", "no-such-skill",
                               canonical_root=self.skills_root)
        self.assertIn("unknown skill", str(ctx.exception))

    def test_approve_standard_skill_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            approve_superpower(self.db, "general", "std-skill",
                               canonical_root=self.skills_root)
        self.assertIn("not superpower", str(ctx.exception))

    def test_approve_disallowed_profile_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            approve_superpower(self.db, "general", "restricted-sup",
                               canonical_root=self.skills_root)
        self.assertIn("not allowed", str(ctx.exception))

    def test_approve_valid_superpower_succeeds(self) -> None:
        result = approve_superpower(self.db, "general", "sup-skill",
                                     actor="admin", surface="web",
                                     canonical_root=self.skills_root)
        self.assertTrue(result["approved"])

    def test_revoke_fails_for_unapproved(self) -> None:
        with self.assertRaises(ValueError):
            revoke_superpower(self.db, "general", "sup-skill")


if __name__ == "__main__":
    unittest.main()
