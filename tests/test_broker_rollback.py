"""Rollback must preserve broker-backed sessions instead of using raw keys."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.broker_client import BROKER_URL, CAPABILITY
from agent_console.config import Settings
from agent_console.environment import write_private
from agent_console.manager import SessionManager


class BrokerRollbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "PATH": os.defpath}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.manager = SessionManager(Settings(
            workspace_root=self.root, state_dir=self.root / "state",
            database_path=self.root / "state/db.sqlite", config_dir=self.root / "config",
            profile_dir=self.root / "profiles", handoff_dir=self.root / "handoffs",
            worktree_root=self.root / "worktrees", tmux_socket=None,
            tmux_socket_path=self.root / "unused.sock", legacy_tmux_socket_path=None,
        ))

    def assert_rollback_preserves_session(self, tool, *, running):
        session_id, name = "rollback-" + tool, "rollback-" + tool
        launcher = self.root / (name + ".sh")
        launcher.write_text("#!/bin/sh\nexec false\n")
        native = self.root / (name + "-native.json")
        native.write_text('{"apiKey":"$AGENT_CONSOLE_BROKER_CAPABILITY"}\n')
        snapshot = self.manager.settings.state_dir / "environment-launches" / (session_id + ".json")
        write_private(snapshot, {"environment": {
            CAPABILITY: "synthetic-data-capability", BROKER_URL: "http://127.0.0.1:8792",
        }, "launcher_keys": [], "secret_files": [], "revision": 0})
        original = {path: path.read_bytes() for path in (snapshot, launcher, native)}
        with self.manager.database.connect() as connection:
            connection.execute("INSERT INTO sessions(id,tmux_name,tool,managed,created_at,status) "
                               "VALUES(?,?,?,1,'2026-10-10','detached')", (session_id, name, tool))
        session = {"id": session_id, "tmux_name": name, "tool": tool, "managed": True,
                   "launcher_path": str(launcher), "running": running}
        # Restoring a direct account is not permission to silently convert an
        # existing protected launch. Neither credential lookup nor tmux may run.
        legacy_key = self.manager.auth.secret_path("commandcode-main")
        legacy_key.parent.mkdir(parents=True, exist_ok=True)
        legacy_key.write_text("export CMD_API_KEY=synthetic-legacy-key\n")
        legacy_key.chmod(0o600)
        with patch.object(self.manager, "inspect", return_value=session), \
                patch.object(self.manager.auth, "get_context") as credentials, \
                patch.object(self.manager.environment, "resolve") as resolve, \
                patch.object(self.manager, "tmux_for_name") as terminal:
            action = self.manager.restart if running else self.manager.resume
            with self.assertRaisesRegex(ValueError, "Keep it enabled, or create a new session"):
                action(name, session_id=session_id)
            credentials.assert_not_called()
            resolve.assert_not_called()
            terminal.assert_not_called()
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_restart_refuses_broker_rollback_before_credentials_or_process_changes(self):
        for tool in ("pi", "hermes", "codex", "claude", "opencode", "shell"):
            with self.subTest(tool=tool):
                self.assert_rollback_preserves_session(tool, running=True)

    def test_resume_refuses_broker_rollback_and_preserves_pinned_launch(self):
        for tool in ("codex", "pi"):
            with self.subTest(tool=tool):
                self.assert_rollback_preserves_session(tool, running=False)
