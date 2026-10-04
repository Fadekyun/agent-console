from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_console.database import Database
from agent_console.skill_git import GitReader, validate_source
from agent_console.skill_registry import SkillRegistry
from agent_console.skills import assign_skill, isolate_skills, resolve_session_skills


class GitSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.skill = self.repo / 'guides/portable-guide'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: portable-guide\ndescription: Selected fixture.\nmetadata:\n  agent-console/version: "1"\n  agent-console/source: https://declared.invalid/example\n  agent-console/revision: claimed-version\n---\nInspect the selected input.\n')
        (self.skill / 'scripts').mkdir()
        script = self.skill / 'scripts/check.sh'
        script.write_text('#!/bin/sh\ntouch ' + str(self.base / 'executed') + '\n')
        script.chmod(0o755)
        self.commit()
        self.registry = SkillRegistry(self.base / 'library', self.base / 'state')
        repo, sha = self.repo, self.sha
        def fixture_fetch(reader, source, revision):
            # Only transport is replaced. Tree enumeration/blob extraction and
            # registry staging/activation use real Git objects and code.
            shutil.copytree(repo / '.git', reader.repo)
            return sha
        self.fetch = patch.object(GitReader, 'fetch', fixture_fetch)
        self.fetch.start()
        self.addCleanup(self.fetch.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@localhost', *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.sha = self.git('rev-parse', 'HEAD')

    def stage(self):
        return self.registry.stage_git('https://example.invalid/guides.git', revision='main', subdirectory='guides/portable-guide')

    def test_exact_commit_stays_attributed_through_activation_review_and_delivery(self):
        record = self.stage()
        self.assertEqual(record['provenance']['revision'], self.sha)
        self.assertEqual(record['status'], 'imported-unreviewed')
        self.assertFalse(self.registry.root.exists())
        staged = self.registry.inspect_import(record['id'])['package']
        self.assertEqual(staged['revision'], self.sha)
        self.assertEqual(staged['declared_revision'], 'claimed-version')
        active = self.registry.activate(record['id'], expected_hash=record['hash'], actor='operator')
        self.assertEqual(active['source'], 'https://example.invalid/guides.git')
        self.assertEqual(active['trust'], 'reviewed')
        self.registry.decide('portable-guide', decision='reviewed', expected_hash=active['hash'], actor='operator')
        self.assertEqual(self.registry.inspect('portable-guide')['provenance'], record['provenance'])
        db = Database(self.base / 'state/main.sqlite3'); db.migrate()
        with patch('agent_console.skills.SKILL_CATALOG', []):
            assign_skill(db, 'coder', 'portable-guide', canonical_root=self.registry.root)
            selected = resolve_session_skills(db, 'coder', 'codex', canonical_root=self.registry.root)
            isolate_skills(self.base / 'snapshot', self.registry.root, selected['materialized'])
        receipt = json.loads((self.base / 'snapshot/delivery.json').read_text())['skills'][0]
        self.assertEqual(receipt['revision'], self.sha)
        self.assertEqual(receipt['provenance'], record['provenance'])
        self.assertTrue(receipt['provenance_content_matches'])
        self.assertFalse((self.base / 'executed').exists())
        (self.registry.root / 'portable-guide/SKILL.md').write_text('Changed content')
        drift = self.registry.inspect('portable-guide')
        self.assertFalse(drift['provenance_content_matches'])
        self.assertEqual(drift['trust'], 'imported-unreviewed')

    def test_reviewed_local_edit_keeps_original_import_hash_without_claiming_a_git_match(self):
        record = self.stage()
        self.registry.activate(record['id'], expected_hash=record['hash'], actor='operator')
        target = self.registry.root / 'portable-guide/SKILL.md'
        target.write_text(target.read_text() + '\nReviewed local addition.\n')
        changed = self.registry.inspect('portable-guide')
        self.registry.decide('portable-guide', decision='reviewed', expected_hash=changed['hash'], actor='operator')
        db = Database(self.base / 'state/main.sqlite3'); db.migrate()
        with patch('agent_console.skills.SKILL_CATALOG', []):
            selected = resolve_session_skills(db, 'general', 'codex', canonical_root=self.registry.root, shared_allowlist=['portable-guide'])
            isolate_skills(self.base / 'edited-snapshot', self.registry.root, selected['materialized'])
        delivered = json.loads((self.base / 'edited-snapshot/delivery.json').read_text())['skills'][0]
        self.assertFalse(delivered['provenance_content_matches'])
        self.assertEqual(delivered['provenance']['content_hash'], record['hash'])
        self.assertNotEqual(delivered['hash'], record['hash'])

    def test_generated_defaults_are_not_presented_as_package_declared_provenance(self):
        (self.skill / 'SKILL.md').write_text('---\nname: portable-guide\ndescription: No provenance claims.\n---\nInspect the input.\n')
        self.commit()
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            record = self.stage()
        package = self.registry.inspect_import(record['id'])['package']
        self.assertIsNone(package['declared_source'])
        self.assertIsNone(package['declared_revision'])
        self.assertEqual(package['source'], 'https://example.invalid/guides.git')
        self.assertEqual(package['revision'], self.sha)

    def test_missing_directory_and_unsafe_links_never_activate(self):
        with self.assertRaises(ValueError):
            self.registry.stage_git('https://example.invalid/repo', subdirectory='missing')
        (self.skill / 'escape').symlink_to('/etc/passwd')
        self.commit()
        # Transport fixture reads the latest tree explicitly for this test.
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            with self.assertRaisesRegex(ValueError, 'symlink or submodule'):
                self.stage()
        self.assertFalse(self.registry.root.exists())
        self.assertEqual(self.registry.imports(), [])

    def copy_current(self, reader):
        shutil.copytree(self.repo / '.git', reader.repo)
        return self.sha

    def test_submodules_and_oversize_files_are_rejected(self):
        self.git('update-index', '--add', '--cacheinfo', '160000', self.sha, 'guides/portable-guide/submodule')
        self.git('commit', '-qm', 'gitlink')
        self.sha = self.git('rev-parse', 'HEAD')
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            with self.assertRaisesRegex(ValueError, 'submodule'):
                self.stage()
        self.git('rm', '--cached', 'guides/portable-guide/submodule')
        (self.skill / 'large').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        self.commit()
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            with self.assertRaisesRegex(ValueError, 'size limits'):
                self.stage()

    def test_credentials_protocols_and_traversal_are_rejected_before_fetch(self):
        sources = ['http://example.invalid/repo', 'ssh://example.invalid/repo',
                   'https://user:password@example.invalid/repo', 'https://example.invalid/repo?token=value',
                   'https://example.invalid/repo#fragment', 'file:///tmp/repo']
        for source in sources:
            with self.subTest(source=source), patch.object(GitReader, 'fetch') as fetch:
                with self.assertRaises(ValueError):
                    self.registry.stage_git(source)
                fetch.assert_not_called()
        for directory in ('../escape', '/absolute', 'a/../../b', 'a\\b', 'a:b'):
            with self.assertRaises(ValueError):
                validate_source('https://example.invalid/repo', 'HEAD', directory)
        for revision in ('--upload-pack=evil', 'main..other', 'HEAD^{tree}', 'main\nsecret'):
            with self.assertRaises(ValueError):
                validate_source('https://example.invalid/repo', revision, '')

    def test_native_errors_are_redacted_and_environment_is_not_inherited(self):
        root = self.base / 'reader'; root.mkdir()
        reader = GitReader(root)
        self.assertNotIn('AGENT_CONSOLE_EVIDENCE_CAPABILITY', reader.env)
        self.assertEqual(reader.env['GIT_ALLOW_PROTOCOL'], 'https')
        command = self.base / 'fail-git'
        command.write_text('#!/bin/sh\necho private-remote-text >&2\nexit 1\n'); command.chmod(0o755)
        reader.binary = str(command)
        with self.assertRaises(ValueError) as error:
            reader.run('fetch')
        self.assertNotIn('private-remote-text', str(error.exception))
        command.write_text('#!/bin/sh\nsleep 20\n')
        reader.deadline = 0
        with self.assertRaisesRegex(ValueError, 'budget'):
            reader.run('fetch')

    def test_secret_files_and_changed_staged_content_cannot_activate(self):
        (self.skill / 'credential').write_text('ghp_' + 'Q' * 32)
        self.commit()
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            with self.assertRaisesRegex(ValueError, 'possible secret'):
                self.stage()
        (self.skill / 'credential').unlink(); self.commit()
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            record = self.stage()
        staged = self.registry.inspect_import(record['id'])
        (Path(staged['staged_path']) / 'SKILL.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.registry.activate(record['id'], expected_hash=record['hash'], actor='operator')
        self.assertFalse(self.registry.root.exists())

    def test_secret_like_filenames_never_reach_registry_or_error_output(self):
        name = 'ghp_' + 'q' * 32
        (self.skill / name).write_text('ordinary content')
        self.commit()
        with patch.object(GitReader, 'fetch', lambda reader, *_: self.copy_current(reader)):
            with self.assertRaises(ValueError) as error:
                self.stage()
        self.assertNotIn(name, str(error.exception))
        self.assertEqual(self.registry.imports(), [])
