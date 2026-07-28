from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent_console.auth import AuthRegistry
from agent_console.providers import provider_adapter


class AuthContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.registry = AuthRegistry(self.home / ".config" / "agent-console", home=self.home)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_contexts_have_isolated_native_stores(self) -> None:
        self.registry.add_context(
            "codex",
            "second",
            provider="openai",
            kind="oauth-native",
            source_ref="codex/second",
        )
        first = self.registry.codex_home("default")
        second = self.registry.codex_home("second")
        (first / "auth.json").write_text('{"account":"first"}\n', encoding="utf-8")
        (second / "auth.json").write_text('{"account":"second"}\n', encoding="utf-8")
        self.assertNotEqual(first, second)
        self.assertNotEqual((first / "auth.json").read_text(), (second / "auth.json").read_text())

    def test_two_api_credentials_coexist_without_values_in_registry(self) -> None:
        first = self.registry.secret_path("openrouter-main")
        second = self.registry.secret_path("openrouter-secondary")
        first.write_text("export OPENROUTER_API_KEY=fixture-one\n", encoding="utf-8")
        second.write_text("export OPENROUTER_API_KEY=fixture-two\n", encoding="utf-8")
        first.chmod(0o600)
        second.chmod(0o600)
        self.registry.add_context(
            "opencode",
            "openrouter-secondary",
            provider="openrouter",
            kind="api-key",
            secret_ref="openrouter-secondary",
        )
        serialized = self.registry.registry_path.read_text(encoding="utf-8")
        public = json.dumps(self.registry.list_contexts())
        self.assertNotIn("fixture-one", serialized + public)
        self.assertNotIn("fixture-two", serialized + public)
        self.assertEqual(
            self.registry.get_context("opencode", "openrouter-secondary")["status"],
            "ready",
        )

    def test_hermes_remains_setup_testing_until_verified(self) -> None:
        secret = self.registry.secret_path("openrouter-main")
        secret.write_text("export OPENROUTER_API_KEY=fixture\n", encoding="utf-8")
        secret.chmod(0o600)
        before = self.registry.get_context("hermes", "openrouter-main")
        self.assertEqual(before["status"], "setup-required")
        after = self.registry.add_context(
            "hermes",
            "openrouter-main",
            provider="openrouter",
            kind="api-key",
            secret_ref="openrouter-main",
            source_ref="hermes/default",
            enabled=True,
            verified=True,
            make_default=True,
        )
        self.assertEqual(after["status"], "ready")

    def test_fresh_registry_has_opencode_go_default_with_provider_opencode_go(self) -> None:
        ctx = self.registry.get_context("opencode", "opencode-go-default")
        self.assertEqual(ctx["provider"], "opencode-go")
        self.assertEqual(ctx["kind"], "oauth-native")
        self.assertEqual(ctx["source_ref"], "opencode/provider-native")
        self.assertTrue(ctx["enabled"])
        self.assertTrue(ctx["verified"])

    def test_migration_recognized_builtin_persisted_context(self) -> None:
        old = {
            "provider": "opencode-go",
            "kind": "oauth-native",
            "source_ref": "opencode/provider-native",
            "enabled": True,
            "verified": True,
        }
        data = {
            "version": 1,
            "defaults": {"opencode": "opencode-go-default"},
            "contexts": {"opencode": {"opencode-go-default": old}},
        }
        self.registry.registry_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.registry._ensure_builtin_contexts()
        migrated = json.loads(self.registry.registry_path.read_text(encoding="utf-8"))
        entry = migrated["contexts"]["opencode"]["opencode-go-default"]
        self.assertEqual(entry["provider"], "opencode-go")
        self.assertEqual(entry["source_ref"], "opencode/provider-native")

    def test_migration_recognizes_legacy_opencode_provider(self) -> None:
        old = {
            "provider": "opencode",
            "kind": "oauth-native",
            "source_ref": "opencode/provider-native",
            "enabled": True,
            "verified": True,
            "legacy_marker": "was-opencode-go-default",
        }
        data = {
            "version": 1,
            "defaults": {"opencode": "opencode-go-default"},
            "contexts": {"opencode": {"opencode-go-default": old}},
        }
        self.registry.registry_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.registry._ensure_builtin_contexts()
        migrated = json.loads(self.registry.registry_path.read_text(encoding="utf-8"))
        contexts = migrated["contexts"]["opencode"]
        # Old entry preserved as opencode-zen-default with opencode provider AND distinctive property
        self.assertIn("opencode-zen-default", contexts)
        zen = contexts["opencode-zen-default"]
        self.assertEqual(zen["provider"], "opencode")
        self.assertEqual(zen["source_ref"], "opencode/provider-native")
        self.assertEqual(zen.get("legacy_marker"), "was-opencode-go-default")
        # New GO context created with opencode-go provider (no legacy marker)
        self.assertIn("opencode-go-default", contexts)
        go = contexts["opencode-go-default"]
        self.assertEqual(go["provider"], "opencode-go")
        self.assertEqual(go["source_ref"], "opencode/provider-native")
        self.assertNotIn("legacy_marker", go)
        # GO remains default
        self.assertEqual(migrated["defaults"]["opencode"], "opencode-go-default")

    def test_builtin_key_with_custom_source_ref_not_migrated(self) -> None:
        entry = {
            "provider": "opencode-go",
            "kind": "oauth-native",
            "source_ref": "my-custom/source",
            "enabled": True,
            "verified": False,
        }
        data = {
            "version": 1,
            "defaults": {"opencode": "opencode-go-default"},
            "contexts": {"opencode": {"opencode-go-default": entry}},
        }
        self.registry.registry_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.registry._ensure_builtin_contexts()
        persisted = json.loads(self.registry.registry_path.read_text(encoding="utf-8"))
        entry = persisted["contexts"]["opencode"]["opencode-go-default"]
        self.assertEqual(entry["provider"], "opencode-go")
        self.assertEqual(entry["source_ref"], "my-custom/source")
        self.assertEqual(persisted["defaults"]["opencode"], "opencode-go-default")

    def test_completely_custom_key_not_migrated(self) -> None:
        custom = {
            "provider": "opencode-go",
            "kind": "api-key",
            "secret_ref": "my-custom-key",
            "enabled": True,
            "verified": False,
        }
        data = {
            "version": 1,
            "defaults": {"opencode": "my-custom"},
            "contexts": {"opencode": {"my-custom": custom}},
        }
        self.registry.registry_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.registry._ensure_builtin_contexts()
        persisted = json.loads(self.registry.registry_path.read_text(encoding="utf-8"))
        entry = persisted["contexts"]["opencode"]["my-custom"]
        self.assertEqual(entry["provider"], "opencode-go")
        self.assertEqual(entry["secret_ref"], "my-custom-key")

    def test_already_current_entry_does_not_write(self) -> None:
        with patch.object(self.registry, "_write") as mock_write:
            self.registry._ensure_builtin_contexts()
            mock_write.assert_not_called()

    def test_all_provider_adapters_expose_the_lifecycle_contract(self) -> None:
        contract = {
            "availability",
            "auth_status",
            "login",
            "build_argv",
            "build_environment",
            "interrupt",
            "restart",
            "resume",
            "doctor",
        }
        for tool in ("codex", "claude", "opencode", "hermes", "shell"):
            adapter = provider_adapter(tool, self.registry)
            self.assertTrue(all(callable(getattr(adapter, name, None)) for name in contract))


if __name__ == "__main__":
    unittest.main()
