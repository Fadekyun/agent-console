from __future__ import annotations

import unittest

from agent_console.profiles import (
    PROFILE_SCHEMA,
    READ_ONLY_PROFILES,
    WRITE_PROFILES,
    profile_summaries,
    validate_profile_schema,
)
from agent_console.validation import PROFILES


class ProfileSchemaTests(unittest.TestCase):
    def test_schema_has_all_required_profiles(self) -> None:
        self.assertEqual(
            frozenset(PROFILE_SCHEMA.keys()),
            PROFILES,
            "PROFILE_SCHEMA keys must match validation.PROFILES exactly",
        )

    def test_validate_passes(self) -> None:
        validate_profile_schema()

    def test_read_only_derived_correctly(self) -> None:
        expected = frozenset(
            name for name, meta in PROFILE_SCHEMA.items()
            if meta["read_write_capability"] == "read_only"
        )
        self.assertEqual(READ_ONLY_PROFILES, expected)

    def test_write_derived_correctly(self) -> None:
        expected = frozenset(
            name for name, meta in PROFILE_SCHEMA.items()
            if meta["read_write_capability"] == "write"
        )
        self.assertEqual(WRITE_PROFILES, expected)

    def test_verifier_is_read_only(self) -> None:
        self.assertIn("verifier", READ_ONLY_PROFILES)
        self.assertEqual(
            PROFILE_SCHEMA["verifier"]["read_write_capability"], "read_only"
        )

    def test_general_is_write_default(self) -> None:
        self.assertIn("general", WRITE_PROFILES)
        self.assertEqual(
            PROFILE_SCHEMA["general"]["read_write_capability"], "write"
        )

    def test_coder_and_bugfix_prefer_worktree(self) -> None:
        for name in ("coder", "bugfix"):
            self.assertEqual(
                PROFILE_SCHEMA[name]["worktree_requirement"], "preferred"
            )

    def test_read_only_profiles_are_plan_constrained(self) -> None:
        for name in READ_ONLY_PROFILES:
            self.assertEqual(
                PROFILE_SCHEMA[name]["provider_mode_constraints"],
                frozenset({"plan"}),
                f"{name} should be constrained to plan mode",
            )

    def test_write_profiles_have_no_mode_constraint(self) -> None:
        for name in WRITE_PROFILES:
            self.assertEqual(
                PROFILE_SCHEMA[name]["provider_mode_constraints"],
                frozenset(),
                f"{name} should have no mode constraints",
            )

    def test_release_and_operator_require_human_approval(self) -> None:
        for name in ("release", "operator"):
            self.assertTrue(
                PROFILE_SCHEMA[name]["requires_human_approval"],
                f"{name} should require human approval",
            )

    def test_other_profiles_do_not_require_human_approval(self) -> None:
        for name in ("general", "coder", "planner", "scout", "reviewer",
                       "researcher", "verifier", "bugfix"):
            self.assertFalse(
                PROFILE_SCHEMA[name]["requires_human_approval"],
                f"{name} should not require human approval",
            )

    def test_coder_planner_release_operator_manage_session_links(self) -> None:
        for name in ("coder", "planner", "release", "operator"):
            self.assertTrue(
                PROFILE_SCHEMA[name]["manages_session_links"],
                f"{name} should manage session links",
            )

    def test_delegation_profiles_are_consistent(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            allowed = meta["allowed_delegation_profiles"]
            for dep in allowed:
                self.assertIn(
                    dep, PROFILES,
                    f"{name} allows delegation to unknown profile {dep}",
                )

    def test_collaboration_profiles_are_full_set(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            self.assertEqual(
                meta["allowed_collaboration_profiles"],
                PROFILES,
                f"{name} should collaborate with all profiles",
            )

    def test_all_profiles_have_no_legacy_aliases(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            self.assertEqual(
                meta["legacy_aliases"], frozenset(),
                f"{name} should have no legacy aliases",
            )

    def test_all_profiles_have_no_replacement(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            self.assertIsNone(
                meta["replacement_profile"],
                f"{name} should have no replacement profile",
            )

    def test_all_profiles_are_active(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            self.assertEqual(
                meta["status"], "active",
                f"{name} should be active",
            )

    def test_read_only_profiles_have_read_only_delegation_permission(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            if meta["read_write_capability"] == "read_only":
                self.assertEqual(
                    meta["delegation_permissions"],
                    frozenset({"read_only"}),
                    f"{name} should have read_only delegation permission",
                )

    def test_write_profiles_have_read_only_delegation_permission(self) -> None:
        for name, meta in PROFILE_SCHEMA.items():
            if meta["read_write_capability"] == "write":
                self.assertEqual(
                    meta["delegation_permissions"],
                    frozenset({"read_only"}),
                    f"{name} should have read_only delegation permission set",
                )

    def test_general_worktree_requirement_is_none(self) -> None:
        self.assertEqual(
            PROFILE_SCHEMA["general"]["worktree_requirement"], "none"
        )

    def test_every_profile_has_all_expected_fields(self) -> None:
        expected_fields = {
            "name", "display_name", "description", "read_write_capability",
            "worktree_requirement", "delegation_permissions",
            "allowed_delegation_profiles", "allowed_collaboration_profiles",
            "legacy_aliases", "replacement_profile", "provider_mode_constraints",
            "requires_human_approval", "manages_session_links", "status",
        }
        for name, meta in PROFILE_SCHEMA.items():
            self.assertEqual(
                set(meta.keys()),
                expected_fields,
                f"{name} should have exactly the expected fields",
            )


class ProfileSummaryTests(unittest.TestCase):
    def test_summaries_contain_all_profiles(self) -> None:
        summaries = profile_summaries()
        names = {s["name"] for s in summaries}
        self.assertEqual(names, set(PROFILES))

    def test_summary_includes_key_fields(self) -> None:
        summaries = profile_summaries()
        for summary in summaries:
            self.assertIn("name", summary)
            self.assertIn("display_name", summary)
            self.assertIn("read_write_capability", summary)
            self.assertIn("worktree_requirement", summary)
            self.assertIn("requires_human_approval", summary)
            self.assertIn("status", summary)

    def test_general_is_first_summary(self) -> None:
        summaries = profile_summaries()
        self.assertEqual(summaries[0]["name"], "general")


class ProfiledValidationFailureTests(unittest.TestCase):
    def test_missing_profile_validation_fails(self) -> None:
        from agent_console.profiles import PROFILE_SCHEMA as real_schema
        saved = dict(real_schema)
        try:
            real_schema["ghost"] = {
                "name": "ghost",
                "display_name": "Ghost",
                "description": "unknown",
                "read_write_capability": "read_only",
                "worktree_requirement": "none",
                "allowed_delegation_profiles": frozenset(),
                "allowed_collaboration_profiles": frozenset(),
                "legacy_aliases": frozenset(),
                "replacement_profile": None,
                "provider_mode_constraints": frozenset(),
                "requires_human_approval": False,
                "manages_session_links": False,
                "status": "active",
            }
            with self.assertRaises(RuntimeError):
                validate_profile_schema()
        finally:
            real_schema.clear()
            real_schema.update(saved)

    def test_unknown_profile_validation_fails(self) -> None:
        saved = dict(PROFILE_SCHEMA)
        try:
            PROFILE_SCHEMA.pop("verifier", None)
            with self.assertRaises(RuntimeError):
                validate_profile_schema()
        finally:
            PROFILE_SCHEMA.clear()
            PROFILE_SCHEMA.update(saved)


if __name__ == "__main__":
    unittest.main()
