from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.secrets_store import secret_status, set_openrouter_secret
from agent_console.skills import doctor_skills, sync_skills


class SkillAndSecretTests(unittest.TestCase):
    def test_skill_sync_links_all_tool_roots(self) -> None:
        fixture_skills = ("tailscale-router", "agent-console-ops")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical"
            home = root / "home"
            for name in fixture_skills:
                skill = canonical / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: fixture\n---\n# Fixture\n",
                    encoding="utf-8",
                )
            with patch("agent_console.skills.SKILL_CATALOG", []):
                result = sync_skills(canonical_root=canonical, home=home)
                self.assertTrue(result["ok"])
                self.assertTrue(doctor_skills(canonical_root=canonical, home=home)["ok"])
            for relative in (
                Path(".codex/skills"),
                Path(".claude/skills"),
                Path(".hermes/skills/homelab"),
            ):
                for name in fixture_skills:
                    self.assertTrue((home / relative / name).is_symlink())

    def test_openrouter_secret_is_hidden_and_mode_600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            key = "sk-or-v1-" + "a" * 40
            with patch("getpass.getpass", side_effect=[key, key]):
                result = set_openrouter_secret(home)
            self.assertEqual(result["mode"], "0600")
            status = secret_status(home)["openrouter"]
            self.assertTrue(status["configured"])
            self.assertTrue(status["permissions_ok"])
            self.assertTrue(status["single_source_ok"])
            self.assertEqual(status["duplicate_sources"], [])
            self.assertNotIn(key, str(status))

    def test_openrouter_status_reports_duplicate_sources_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            key = "sk-or-v1-" + "a" * 40
            with patch("getpass.getpass", side_effect=[key, key]):
                set_openrouter_secret(home)
            hermes = home / ".hermes"
            hermes.mkdir()
            (hermes / "config.yaml").write_text(
                "model:\n  provider: openrouter\n  api_key: duplicate-fixture-value\nagent:\n  max_turns: 1\n",
                encoding="utf-8",
            )
            status = secret_status(home)["openrouter"]
            self.assertFalse(status["single_source_ok"])
            self.assertEqual(status["duplicate_sources"], [str(hermes / "config.yaml")])
            self.assertNotIn("duplicate-fixture-value", str(status))

    def test_openrouter_secret_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("getpass.getpass", side_effect=["a" * 30, "b" * 30]):
                with self.assertRaises(ValueError):
                    set_openrouter_secret(Path(tmp))


if __name__ == "__main__":
    unittest.main()
