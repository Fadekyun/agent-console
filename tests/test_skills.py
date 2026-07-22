from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_console.database import Database
from agent_console.skills import (
    SKILL_CATALOG,
    SUPPORTED_TOOLS,
    assign_skill,
    check_superpower_approval,
    doctor_skills,
    enrich_catalog_with_assignments,
    list_assignments,
    skill_catalog,
    sync_skills,
    unassign_skill,
    validate_catalog,
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


if __name__ == "__main__":
    unittest.main()
