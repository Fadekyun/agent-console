"""Regression guard for native history retained by the private compute release."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agent_console.manager import SessionManager


class PrivateHistoryIntegrationTests(unittest.TestCase):
    def test_refresh_preserves_pinned_legacy_home_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = object.__new__(SessionManager)
            manager.settings = SimpleNamespace(state_dir=root)
            manager.auth = SimpleNamespace(codex_home=lambda *args, **kwargs: root / 'auth')
            native = root / 'tool-overlays' / 'original-name' / 'codex-home'
            native.mkdir(parents=True)
            history = native / 'sessions' / 'retained.jsonl'
            history.parent.mkdir()
            history.write_text('retained native conversation\n')
            skills = root / 'skills'
            skills.mkdir()
            for name in ('original-name', 'renamed-session'):
                result = manager._create_session_tool_overlay(name, 'codex-pro',
                    {'name': 'default'}, skills, session_id='immutable-fixture', native_home=native)
                self.assertEqual(result['CODEX_HOME'], str(native))
                self.assertEqual(history.read_text(), 'retained native conversation\n')
                manager._cleanup_session_tool_overlay(name)
                self.assertEqual(history.read_text(), 'retained native conversation\n')

    def test_new_history_home_uses_immutable_session_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = object.__new__(SessionManager)
            manager.settings = SimpleNamespace(state_dir=root)
            manager.auth = SimpleNamespace(codex_home=lambda *args, **kwargs: root / 'auth')
            skills = root / 'skills'
            skills.mkdir()
            first = manager._create_session_tool_overlay('first-name', 'codex', {}, skills,
                                                        session_id='stable-id')
            native = Path(first['CODEX_HOME'])
            receipt = native / 'history.jsonl'
            receipt.write_text('history survives rename\n')
            second = manager._create_session_tool_overlay('second-name', 'codex', {}, skills,
                                                         session_id='stable-id')
            self.assertEqual(second['CODEX_HOME'], first['CODEX_HOME'])
            self.assertEqual(receipt.read_text(), 'history survives rename\n')
