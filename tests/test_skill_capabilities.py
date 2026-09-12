from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from agent_console.skill_capabilities import SKILL_TOOL_CAPABILITIES, SUPPORTED_TOOLS
from agent_console.skills import doctor_skills, skill_catalog, sync_skills


VERIFIED = lambda tool, binary: "opencode 1.18.30" if tool == "opencode" else "1.0.0"


def write_skill(root: Path, name: str = "fixture", tools: str = "opencode") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: fixture\ntools: {tools}\nkind: standard\n---\n",
        encoding="utf-8",
    )
    return skill


class SkillCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_patch = patch("agent_console.skills.SKILL_CATALOG", [])
        self.catalog_patch.start()

    def tearDown(self) -> None:
        self.catalog_patch.stop()

    def test_capability_table_is_supported_tools_source_of_truth(self) -> None:
        self.assertEqual(
            SUPPORTED_TOOLS,
            frozenset(name for name, item in SKILL_TOOL_CAPABILITIES.items() if item.supported),
        )
        required = {
            "codex": (".codex/skills", True),
            "codex-pro": (".codex/skills", True),
            "claude": (".claude/skills", False),
            "hermes": (".hermes/skills/homelab", False),
            "opencode": ("xdg-config/opencode/skills", False),
        }
        for tool, (native_root, can_isolate) in required.items():
            capability = SKILL_TOOL_CAPABILITIES[tool]
            self.assertEqual(capability.native_root, native_root)
            self.assertEqual(capability.can_isolate_skills, can_isolate)
            self.assertEqual(capability.materialization_method, "symlink")

    def test_opencode_root_honors_real_xdg_but_explicit_home_is_deterministic(self) -> None:
        capability = SKILL_TOOL_CAPABILITIES["opencode"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_home = base / "real-home"
            xdg = base / "custom-config"
            with patch("agent_console.skill_capabilities.Path.home", return_value=real_home):
                native, anchor = capability.resolve_native_root(
                    None, {"XDG_CONFIG_HOME": str(xdg)}
                )
            self.assertEqual(native, xdg / "opencode" / "skills")
            self.assertEqual(anchor, xdg)
            explicit = base / "fixture-home"
            native, anchor = capability.resolve_native_root(
                explicit, {"XDG_CONFIG_HOME": str(xdg)}
            )
            self.assertEqual(native, explicit / ".config" / "opencode" / "skills")
            self.assertEqual(anchor, explicit)

    def test_opencode_sync_uses_native_root_and_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            source = write_skill(canonical)
            result = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            native = home / ".config" / "opencode" / "skills" / "fixture"
            self.assertTrue(result["ok"])
            self.assertTrue(native.is_symlink())
            self.assertEqual(native.resolve(), source.resolve())
            self.assertEqual(result["roots"], {"opencode": str(native.parent)})

    def test_sync_preserves_unrelated_paths_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical)
            native = home / ".config" / "opencode" / "skills"
            native.mkdir(parents=True)
            unrelated_file = native / "notes.txt"
            unrelated_file.write_text("keep", encoding="utf-8")
            unrelated_dir = native / "local-skill"
            unrelated_dir.mkdir()
            external = base / "external"
            external.mkdir()
            unrelated_link = native / "user-link"
            unrelated_link.symlink_to(external, target_is_directory=True)
            sync_skills(canonical_root=canonical, home=home, version_probe=VERIFIED)
            self.assertEqual(unrelated_file.read_text(encoding="utf-8"), "keep")
            self.assertTrue(unrelated_dir.is_dir())
            self.assertTrue(unrelated_link.is_symlink())
            self.assertEqual(unrelated_link.resolve(), external.resolve())

    def test_wrong_target_is_reported_and_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical)
            wrong = base / "wrong"
            write_skill(wrong, "other")
            native = home / ".config" / "opencode" / "skills"
            native.mkdir(parents=True)
            path = native / "fixture"
            path.symlink_to(wrong / "other", target_is_directory=True)
            result = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["partial"])
            self.assertEqual(path.resolve(), (wrong / "other").resolve())
            diagnostic = result["diagnostics"][0]
            self.assertEqual(diagnostic["state"], "wrong-target")
            self.assertTrue(diagnostic["collision"])

    def test_source_directory_escape_is_rejected_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            canonical.mkdir()
            external = base / "external"
            write_skill(external, "escaped")
            (canonical / "escaped").symlink_to(external / "escaped", target_is_directory=True)
            home = base / "home"
            with self.assertRaisesRegex(ValueError, "source-escape"):
                sync_skills(canonical_root=canonical, home=home, version_probe=VERIFIED)
            self.assertFalse((home / ".config").exists())

    def test_skill_file_escape_is_rejected_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            skill = canonical / "escaped"
            skill.mkdir(parents=True)
            external_file = base / "SKILL.md"
            external_file.write_text("---\ntools: opencode\n---\n", encoding="utf-8")
            (skill / "SKILL.md").symlink_to(external_file)
            home = base / "home"
            with self.assertRaisesRegex(ValueError, "skill-file-escape"):
                sync_skills(canonical_root=canonical, home=home, version_probe=VERIFIED)
            self.assertFalse((home / ".config").exists())

    def test_target_parent_symlink_escape_is_rejected_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            home.mkdir()
            write_skill(canonical)
            external = base / "external-config"
            external.mkdir()
            (home / ".config").symlink_to(external, target_is_directory=True)
            result = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            self.assertFalse(result["ok"])
            self.assertTrue((home / ".config").is_symlink())
            self.assertFalse((external / "opencode").exists())

    def test_duplicate_and_project_shadowing_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            project = base / "project"
            source = write_skill(canonical)
            native = home / ".config" / "opencode" / "skills" / "fixture"
            native.parent.mkdir(parents=True)
            native.symlink_to(source, target_is_directory=True)
            write_skill(home / ".claude" / "skills", "fixture")
            project_skill = write_skill(project / ".opencode" / "skills", "fixture")
            result = doctor_skills(
                canonical_root=canonical,
                home=home,
                project_root=project,
                version_probe=VERIFIED,
            )
            diagnostic = result["diagnostics"][0]
            self.assertTrue(diagnostic["collision"])
            self.assertTrue(diagnostic["shadowed"])
            self.assertEqual(diagnostic["shadowed_by"], [str(project_skill)])
            duplicates = result["providers"][0]["discovery"]["duplicates"]
            self.assertEqual(duplicates[0]["name"], "fixture")
            self.assertEqual(duplicates[0]["winner"], str(project_skill))

    def test_opencode_scans_all_confirmed_roots_recursively_by_frontmatter_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            project = base / "project"
            write_skill(canonical, "catalogued")
            locations = {
                "legacy-global-claude": home / ".claude" / "skills" / "claude-dir",
                "global-agents": home / ".agents" / "skills" / "agents-dir",
                "native-global": home / ".config" / "opencode" / "skills" / "group" / "nested-dir",
                "project-claude": project / ".claude" / "skills" / "project-claude-dir",
                "project-agents": project / ".agents" / "skills" / "project-agents-dir",
                "native-project": project / ".opencode" / "skills" / "project-native-dir",
            }
            for index, path in enumerate(locations.values()):
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text(
                    f"---\nname: actual-id-{index}\ndescription: fixture\n---\n",
                    encoding="utf-8",
                )
            result = skill_catalog(
                canonical, home=home, project_root=project, version_probe=VERIFIED
            )
            discovery = result["providers"][0]["discovery"]
            self.assertEqual({root["kind"] for root in discovery["roots"]}, set(locations))
            self.assertEqual(
                {skill["name"] for skill in discovery["skills"]},
                {f"actual-id-{index}" for index in range(len(locations))},
            )
            native_nested = next(
                skill for skill in discovery["skills"] if skill["name"] == "actual-id-2"
            )
            self.assertEqual(native_nested["paths"], [str(locations["native-global"])])
            self.assertEqual(discovery["status"], "uncertain")
            self.assertIn(
                "configured discovery sources are not inspected",
                discovery["uncertainty_reasons"],
            )

    def test_unverified_duplicate_order_does_not_claim_a_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical)
            for root in (
                home / ".agents" / "skills" / "one",
                home / ".config" / "opencode" / "skills" / "two",
            ):
                root.mkdir(parents=True)
                (root / "SKILL.md").write_text(
                    "---\nname: duplicate-id\n---\n", encoding="utf-8"
                )
            result = doctor_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            duplicate = result["providers"][0]["discovery"]["duplicates"][0]
            self.assertEqual(duplicate["name"], "duplicate-id")
            self.assertIsNone(duplicate["winner"])
            self.assertEqual(duplicate["ordering_state"], "uncertain")

    def test_canonical_directory_identity_joins_discovery_by_native_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            project = base / "project"
            folder = canonical / "folder"
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                "---\nname: shared-id\ndescription: canonical\ntools: opencode\n---\n",
                encoding="utf-8",
            )
            synced = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            self.assertTrue(synced["ok"])
            override = project / ".opencode" / "skills" / "other"
            override.mkdir(parents=True)
            (override / "SKILL.md").write_text(
                "---\nname: shared-id\ndescription: project override\n---\n",
                encoding="utf-8",
            )
            result = doctor_skills(
                canonical_root=canonical,
                home=home,
                project_root=project,
                version_probe=VERIFIED,
            )
            diagnostic = result["diagnostics"][0]
            self.assertEqual(diagnostic["skill"], "folder")
            self.assertEqual(diagnostic["native_id"], "shared-id")
            self.assertTrue(diagnostic["collision"])
            self.assertTrue(diagnostic["shadowed"])
            self.assertEqual(diagnostic["shadowed_by"], [str(override)])
            self.assertEqual(diagnostic["discovered_paths"], [
                str(home / ".config" / "opencode" / "skills" / "folder"),
                str(override),
            ])
            self.assertTrue(result["problems"])

    def test_unverified_opencode_is_diagnostic_only_and_skips_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical)
            result = sync_skills(
                canonical_root=canonical,
                home=home,
                version_probe=lambda tool, binary: "opencode 2.0.0",
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["skipped"][0]["tool"], "opencode")
            self.assertEqual(result["diagnostics"][0]["version_state"], "unverified-version")
            self.assertFalse(result["diagnostics"][0]["mutation_allowed"])
            self.assertFalse((home / ".config").exists())

    def test_missing_binary_is_reported_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            write_skill(canonical)
            with patch("agent_console.skills._resolved_binary", return_value=base / "missing"):
                result = doctor_skills(
                    canonical_root=canonical,
                    home=base / "home",
                    version_probe=lambda tool, binary: None,
                )
            provider = result["providers"][0]
            self.assertEqual(provider["version_state"], "missing-binary")
            self.assertFalse(provider["binary_present"])
            self.assertFalse(provider["mutation_allowed"])

    def test_default_home_rejects_symlinked_xdg_anchor_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            home.mkdir()
            write_skill(canonical)
            outside = base / "outside"
            outside.mkdir()
            (home / ".config").symlink_to(outside, target_is_directory=True)
            with (
                patch("agent_console.skill_capabilities.Path.home", return_value=home),
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}),
            ):
                result = sync_skills(
                    canonical_root=canonical,
                    version_probe=VERIFIED,
                )
            self.assertFalse(result["ok"])
            self.assertTrue((home / ".config").is_symlink())
            self.assertFalse((outside / "opencode").exists())

    def test_removed_tool_allowlist_cleans_only_managed_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical, "keep", "codex")
            changed = write_skill(canonical, "changed", "codex")
            first = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            self.assertTrue(first["ok"])
            codex_root = home / ".codex" / "skills"
            sibling = codex_root / "user-sibling"
            sibling.mkdir()
            outside = base / "outside-skill"
            write_skill(outside, "unrelated")
            user_link = codex_root / "user-link"
            user_link.symlink_to(outside / "unrelated", target_is_directory=True)
            (changed / "SKILL.md").write_text(
                "---\nname: changed\ndescription: fixture\ntools: claude\nkind: standard\n---\n",
                encoding="utf-8",
            )
            before = doctor_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            revoked = next(
                item for item in before["diagnostics"]
                if item["skill"] == "changed" and item["state"] == "revoked-managed-link"
            )
            self.assertEqual(revoked["tool"], "codex")
            second = sync_skills(
                canonical_root=canonical, home=home, version_probe=VERIFIED
            )
            self.assertTrue(second["ok"])
            self.assertFalse((codex_root / "changed").exists())
            self.assertTrue((codex_root / "keep").is_symlink())
            self.assertTrue((home / ".claude" / "skills" / "changed").is_symlink())
            self.assertTrue(sibling.is_dir())
            self.assertTrue(user_link.is_symlink())
            self.assertEqual(user_link.resolve(), (outside / "unrelated").resolve())
            self.assertIn(
                "unlinked-revoked", {item["action"] for item in second["changed"]}
            )

    def test_catalogue_and_doctor_share_diagnostics_and_probe_once_per_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = base / "canonical"
            home = base / "home"
            write_skill(canonical, "one")
            write_skill(canonical, "two")
            calls: list[str] = []

            def probe(tool: str, binary: Path) -> str:
                calls.append(tool)
                return "opencode 1.18.30"

            catalog = skill_catalog(
                canonical, home=home, version_probe=probe
            )
            self.assertEqual(calls, ["opencode"])
            calls.clear()
            doctor = doctor_skills(
                canonical_root=canonical, home=home, version_probe=probe
            )
            self.assertEqual(calls, ["opencode"])
            self.assertEqual(catalog["diagnostics"], doctor["diagnostics"])
            self.assertEqual(catalog["providers"], doctor["providers"])


if __name__ == "__main__":
    unittest.main()
