from __future__ import annotations

import tempfile
import unittest
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from agent_console.auth import AuthRegistry
from agent_console.providers import (CodexAdapter, CodexProAdapter, TOOL_BINARIES,
    normalize_codex_automatic_review_argv, normalize_codex_automatic_review_launcher)


class CodexProApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.binary = root / "codex"
        self.binary.write_text(
            "#!/bin/sh\n"
            "case \"$1 $2\" in\n"
            "  *--help*) echo '--approve-for-me --output-schema --output-last-message --sandbox' ;;\n"
            "esac\n"
        )
        self.binary.chmod(0o700)
        self.registry = AuthRegistry(root / "config", home=root / "home")
        self.adapter = CodexProAdapter(self.registry)
        self.patch = patch.dict(TOOL_BINARIES, {"codex-pro": self.binary})
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def _argv(self, mode: str) -> list[str]:
        with patch.dict(os.environ, {"AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS": ""}):
            return self.adapter.build_argv(
                context={"name": "default"},
                profile="general",
                cwd=Path(self.temp.name),
                role="Do the bounded task.",
                context_path=Path(self.temp.name) / "context.md",
                agent_mode=mode,
                model=None,
                read_only=mode == "plan",
            )

    def test_writable_default_uses_native_automatic_review(self) -> None:
        argv = self._argv("auto")
        self.assertNotIn("--sandbox", argv)
        self.assertIn("--approve-for-me", argv)
        self.assertNotIn("--ask-for-approval", argv)
        self.assertNotIn("danger-full-access", argv)

    def test_legacy_resume_migration_preserves_quoted_role_and_environment(self):
        import shlex
        argv = ["/bin/codex", "--sandbox", "workspace-write", "--approve-for-me",
                "-c", 'developer_instructions=two lines\n--sandbox workspace-write',
                "resume", "native-thread-id"]
        text = "#!/bin/sh\nexport CODEX_HOME=/private/context\nexec " + shlex.join(argv) + "\n"
        migrated = normalize_codex_automatic_review_launcher(text)
        self.assertTrue(migrated.startswith("#!/bin/sh\nexport CODEX_HOME=/private/context\n"))
        self.assertEqual(shlex.split(migrated.split("\nexec ", 1)[1]),
                         [argv[0]] + argv[3:])
        self.assertEqual(normalize_codex_automatic_review_launcher(migrated), migrated)

    def test_automatic_review_never_replaces_other_sandbox_policies(self):
        for value in ["read-only", "danger-full-access"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_codex_automatic_review_argv(["codex", "--sandbox", value, "--approve-for-me"])
        argv = ["codex", "--sandbox", "read-only", "--ask-for-approval", "never"]
        self.assertEqual(normalize_codex_automatic_review_argv(argv), argv)

    def test_plan_remains_read_only_and_never_approves(self) -> None:
        argv = self._argv("plan")
        self.assertIn("read-only", argv)
        self.assertIn("--ask-for-approval", argv)
        self.assertIn("never", argv)
        self.assertNotIn("--approve-for-me", argv)

    def test_missing_native_flag_fails_closed(self) -> None:
        self.binary.write_text("#!/bin/sh\necho '--sandbox'\n")
        self.binary.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError, "--approve-for-me"):
            self._argv("auto")

    def test_read_action_narrows_auto_argv_without_native_approval(self) -> None:
        argv = self.adapter.workflow_argv(
            self._argv("auto"), schema=Path("schema.json"), output=Path("final.json"), read_only=True
        )
        self.assertNotIn("--approve-for-me", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")

    def test_failed_help_with_flag_is_not_capability_support(self) -> None:
        self.binary.write_text("#!/bin/sh\necho '--approve-for-me'\nexit 1\n")
        with self.assertRaisesRegex(RuntimeError, "native --approve-for-me"):
            self._argv("auto")

    def test_timeout_does_not_substitute_approval_policy(self) -> None:
        with patch("agent_console.providers.subprocess.run", side_effect=subprocess.TimeoutExpired("help", 5)):
            with self.assertRaisesRegex(RuntimeError, "native --approve-for-me"):
                self._argv("auto")

    def test_network_enabled_plan_is_narrowed_for_workflow(self) -> None:
        with patch.dict(os.environ, {"AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS": "1"}):
            argv = self.adapter.build_argv(
                context={"name": "default"}, profile="general", cwd=Path(self.temp.name),
                role="Inspect only.", context_path=Path("context.md"), agent_mode="plan", model=None, read_only=True,
            )
        self.assertIn("sandbox_workspace_write.network_access=true", argv)
        native = self.adapter.workflow_argv(
            argv, schema=Path("schema.json"), output=Path("final.json"), read_only=True
        )
        self.assertNotIn("sandbox_workspace_write.network_access=true", native)
        self.assertNotIn("--approve-for-me", native)
        self.assertEqual(native[native.index("--sandbox") + 1], "read-only")
        self.assertEqual(native[native.index("--ask-for-approval") + 1], "never")

    def test_exec_without_native_support_rejects_writable_workflow(self) -> None:
        interactive = self._argv("auto")
        self.binary.write_text("#!/bin/sh\necho '--sandbox --output-schema --output-last-message'\n")
        with self.assertRaisesRegex(RuntimeError, "exec --help"):
            self.adapter.workflow_argv(
                interactive, schema=Path("schema.json"), output=Path("final.json"), read_only=False
            )
        self.assertFalse(self.adapter.workflow_info()["supported"])

    def test_plan_does_not_require_automatic_review_support(self) -> None:
        self.binary.write_text("#!/bin/sh\necho '--sandbox'\n")
        argv = self._argv("plan")
        self.assertNotIn("--approve-for-me", argv)
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")

    def test_ordinary_codex_keeps_on_request_interactive_approval(self) -> None:
        self.adapter = CodexAdapter(self.registry)
        argv = self._argv("auto")
        self.assertNotIn("--approve-for-me", argv)
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "on-request")
        native = self.adapter.workflow_argv(
            argv, schema=Path("schema.json"), output=Path("final.json"), read_only=False
        )
        self.assertEqual(native[native.index("--ask-for-approval") + 1], "never")

    def test_fixed_planning_task_remains_read_only(self) -> None:
        argv = self.adapter.build_planning_task_argv(
            cwd=Path(self.temp.name), output_schema=Path("schema.json"), final_output=Path("final.json")
        )
        self.assertNotIn("--approve-for-me", argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")

    def test_writable_workflow_keeps_native_approval_and_plan_does_not(self) -> None:
        interactive = self._argv("auto")
        writable = self.adapter.workflow_argv(
            interactive, schema=Path("schema.json"), output=Path("final.json"), read_only=False
        )
        self.assertIn("--approve-for-me", writable)
        self.assertNotIn("--ask-for-approval", writable)
        self.assertNotIn("--sandbox", writable)

        plan = self.adapter.workflow_argv(
            self._argv("plan"), schema=Path("schema.json"), output=Path("final.json"), read_only=True
        )
        self.assertIn("--ask-for-approval", plan)
        self.assertIn("never", plan)
        self.assertNotIn("--approve-for-me", plan)
        self.assertIn("read-only", plan)


if __name__ == "__main__":
    unittest.main()
