"""Opt-in master initialization ordering; no live settings or worker startup."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console import presence_server
from agent_console.config import Settings
from agent_console.database import SCHEMA_VERSION


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(root, root/'state', root/'custom-db'/'db.sqlite3',
                                 root/'profiles', root/'handoffs', root/'worktrees', None,
                                 config_dir=root/'private-config')

    def bootstrap(self):
        with patch('agent_console.config.Settings.from_env', return_value=self.settings):
            presence_server.bootstrap_console_state()

    def test_fresh_custom_database_and_registry_ready_and_idempotent(self):
        self.bootstrap()
        with sqlite3.connect(self.settings.database_path) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        registry = self.settings.config_dir/'auth-contexts.json'
        before = registry.read_bytes()
        self.bootstrap()
        self.assertEqual(registry.read_bytes(), before)

    def test_bootstrap_completes_before_any_child_context(self):
        events = []
        def context(*args):
            self.assertEqual(events, ['bootstrap-start', 'bootstrap-finished'])
            raise RuntimeError('fixture stops before children')
        # Retain the real bootstrap in the callback without recursively mocking it.
        real = presence_server.bootstrap_console_state
        def bootstrap():
            events.append('bootstrap-start')
            with patch('agent_console.config.Settings.from_env', return_value=self.settings):
                real()
            events.append('bootstrap-finished')
        with patch.dict(os.environ, {'AGCONSOLE_DEVICE_PRESENCE':'1','AGCONSOLE_DEVICE_PRESENCE_DIR':self.temp.name}), patch.object(sys, 'argv', ['presence','--host','127.0.0.1','--port','1']), patch.object(presence_server, 'bootstrap_console_state', side_effect=bootstrap), patch.object(presence_server.multiprocessing, 'get_context', side_effect=context), patch('uvicorn.run') as web:
            with self.assertRaisesRegex(RuntimeError, 'fixture stops'):presence_server.main()
            web.assert_not_called()

    def test_forward_schema_failure_spawns_nothing_and_preserves_guard(self):
        self.bootstrap()
        with sqlite3.connect(self.settings.database_path) as conn:
            conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION+1),))
        with patch.dict(os.environ, {'AGCONSOLE_DEVICE_PRESENCE':'1','AGCONSOLE_DEVICE_PRESENCE_DIR':self.temp.name}), patch.object(sys, 'argv', ['presence','--host','127.0.0.1','--port','1']), patch('agent_console.config.Settings.from_env', return_value=self.settings), patch.object(presence_server.multiprocessing, 'get_context') as context, patch('uvicorn.run') as web:
            with self.assertRaisesRegex(ValueError, 'unsupported'):presence_server.main()
            context.assert_not_called()
            web.assert_not_called()
        with sqlite3.connect(self.settings.database_path) as conn:
            self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION+1))
        self.assertFalse((Path(self.temp.name)/'receiver.sock').exists())
