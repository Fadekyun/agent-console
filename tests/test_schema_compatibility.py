from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_console.database import Database
from agent_console.deployer import Deployer, FakeServiceRunner
from agent_console.schema_compatibility import (
    SchemaCompatibilityError, prepare_database_for_release, release_schema_version,
)


class SchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state' / 'test.sqlite3'
        self.config = self.root / 'config'
        self.config.mkdir()
        Database(self.path).migrate()
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO sessions(id,tmux_name,created_at,status) VALUES('ordinary','ordinary','now','detached')")

    def schema(self):
        with sqlite3.connect(self.path) as conn:
            return conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]

    def prepare(self, target=10):
        return prepare_database_for_release(self.path, target, self.config)

    def retain_request(self):
        with sqlite3.connect(self.path) as conn:
            # A retained receipt/tombstone, even terminal or unacknowledged,
            # makes rollback unsafe. No dependency on a real provider fixture.
            required = [row[1] for row in conn.execute('PRAGMA table_info(integration_requests)')
                        if row[3] and row[4] is None]
            conn.execute('INSERT INTO integration_requests ('+','.join(required)+') VALUES ('+','.join('?' for _ in required)+')', ['retained']*len(required))

    def test_empty_feature_rollback_preserves_sessions_and_extension_layout(self):
        result = self.prepare()
        self.assertTrue(result['compatibility_rollback'])
        self.assertEqual(self.schema(), '10')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT id,execution_kind FROM sessions').fetchall(), [('ordinary','interactive')])
            self.assertEqual(conn.execute('SELECT count(*) FROM integration_requests').fetchone()[0],0)
            conn.execute("INSERT INTO sessions(id,tmux_name,created_at,status) VALUES('after','after','later','detached')")
        Database(self.path).migrate()
        self.assertEqual(self.schema(),'11')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions WHERE execution_kind='interactive'").fetchone()[0],2)

    def test_any_retained_request_blocks_downgrade_without_relabel(self):
        self.retain_request()
        with self.assertRaisesRegex(SchemaCompatibilityError,'retained request'):
            self.prepare()
        self.assertEqual(self.schema(),'11')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM integration_requests').fetchone()[0],1)

    def test_noninteractive_session_blocks_even_without_request_row(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE sessions SET execution_kind='integration'")
        with self.assertRaisesRegex(SchemaCompatibilityError,'noninteractive'):
            self.prepare()
        self.assertEqual(self.schema(),'11')

    def test_enabled_malformed_symlink_or_unknown_config_refuses_transition(self):
        path=self.config/'plan-integration.json'
        for content in ('{"enabled":true}', '{}', 'broken'):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(SchemaCompatibilityError): self.prepare()
                self.assertEqual(self.schema(),'11')
        path.unlink(); outside=self.root/'outside'; outside.write_text('{"enabled":false}')
        path.symlink_to(outside)
        with self.assertRaises(SchemaCompatibilityError): self.prepare()

    def test_explicit_disabled_config_allows_empty_rollback(self):
        (self.config/'plan-integration.json').write_text('{"enabled":false}')
        self.assertTrue(self.prepare()['compatibility_rollback'])

    def test_new_writer_refuses_future_or_invalid_schema_without_relabel(self):
        for version in ('12', 'not-a-version', '0'):
            with self.subTest(version=version):
                with sqlite3.connect(self.path) as conn:
                    conn.execute("UPDATE schema_meta SET value=?", (version,))
                with self.assertRaises(ValueError): Database(self.path).migrate()
                self.assertEqual(self.schema(),version)

    def test_target_release_schema_is_literal_contained_and_not_executed(self):
        release=self.root/'source'; (release/'agent_console').mkdir(parents=True)
        source=release/'agent_console'/'database.py'
        source.write_text("SCHEMA_VERSION=10\nraise RuntimeError('must not execute')\n")
        self.assertEqual(release_schema_version(release),10)
        for text in ('SCHEMA_VERSION=12','SCHEMA_VERSION=10+1','SCHEMA_VERSION=True'):
            source.write_text(text)
            with self.assertRaises(SchemaCompatibilityError): release_schema_version(release)
        source.unlink(); external=self.root/'external.py';external.write_text('SCHEMA_VERSION=10')
        source.symlink_to(external)
        with self.assertRaises(SchemaCompatibilityError): release_schema_version(release)

    def test_missing_database_is_not_created(self):
        path=self.root/'absent'/'db'
        with self.assertRaises(SchemaCompatibilityError):
            prepare_database_for_release(path,10,self.config)
        self.assertFalse(path.parent.exists())

    def test_target11_preserves_existing_requests_and_schema(self):
        self.retain_request()
        self.assertFalse(self.prepare(11)['compatibility_rollback'])
        self.assertEqual(self.schema(),'11')

    def test_real_deployer_refuses_unsafe_old_selection_before_link_or_restart(self):
        runner=FakeServiceRunner()
        deployer=Deployer(self.root/'releases',runner,database_path=self.path,config_dir=self.config)
        sources=[]
        for version in (10,11):
            source=self.root/f'source{version}'; (source/'agent_console').mkdir(parents=True)
            (source/'agent_console'/'database.py').write_text(f'SCHEMA_VERSION = {version}\n')
            sources.append(deployer.create_release(source,candidate_sha=str(version)*6)['release_name'])
        deployer.select_release(sources[1])
        self.retain_request()
        with self.assertRaisesRegex(SchemaCompatibilityError,'retained request'):
            deployer.select_release(sources[0])
        self.assertEqual((deployer.releases_root/'current').resolve().name,sources[1])
        self.assertEqual(self.schema(),'11')
        # Upgrade failure's automatic rollback uses this same guarded link path.
        with self.assertRaises(SchemaCompatibilityError): deployer._restore_current(sources[0])
        self.assertEqual((deployer.releases_root/'current').resolve().name,sources[1])
