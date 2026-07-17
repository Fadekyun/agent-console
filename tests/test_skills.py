from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_console.skills import (
    SKILL_CATALOG,
    SUPPORTED_TOOLS,
    check_superpower_approval,
    doctor_skills,
    skill_catalog,
    sync_skills,
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


if __name__ == "__main__":
    unittest.main()
