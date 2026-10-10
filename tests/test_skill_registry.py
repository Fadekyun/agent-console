from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from agent_console.skill_registry import SkillRegistry, inspect_package, snapshot_skill


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'library'; self.root.mkdir()
        self.registry = SkillRegistry(self.root, self.base / 'state')
        self.skill = self.package(self.root / 'bounded-coding')

    def tearDown(self): self.temp.cleanup()

    def package(self, path, policy=None):
        path.mkdir(parents=True)
        (path / 'SKILL.md').write_text('---\nname: bounded-coding\ndescription: |\n  Implement a bounded change.\n  Preserve the agreed scope.\nmetadata:\n  agent-console/version: "1"\n---\nRead the task.\n')
        if policy: (path / 'agent-console.json').write_text(json.dumps(policy))
        return path

    def test_portable_yaml_and_content_identity_include_supporting_files(self):
        first = self.registry.inspect('bounded-coding')
        self.assertEqual(first['issues'], [])
        self.assertIn('\n', first['description'])
        self.assertEqual(first['trust'], 'local-trusted')
        (self.skill / 'references').mkdir()
        ref = self.skill / 'references' / 'contract.md'; ref.write_text('Ready is not final.')
        second = self.registry.inspect('bounded-coding')
        self.assertNotEqual(first['hash'], second['hash'])
        ref.write_text('Final is versioned.')
        self.assertNotEqual(second['hash'], self.registry.inspect('bounded-coding')['hash'])

    def test_duplicate_yaml_and_unknown_policy_fail_closed(self):
        (self.skill / 'SKILL.md').write_text('---\nname: a\nname: b\n---\n')
        self.assertTrue(self.registry.inspect('bounded-coding')['issues'])
        self.assertEqual(self.registry.explain('bounded-coding','coder')['effective_policy'],'deny')

    def test_hash_bound_approval_invalidates_on_drift(self):
        (self.skill / 'agent-console.json').write_text(json.dumps({'approval':'ask'}))
        package = self.registry.inspect('bounded-coding')
        self.assertEqual(self.registry.explain('bounded-coding','coder')['effective_policy'],'ask')
        self.registry.approve('bounded-coding','coder',expected_hash=package['hash'],actor='operator')
        self.assertEqual(self.registry.explain('bounded-coding','coder')['effective_policy'],'allow')
        with (self.skill / 'SKILL.md').open('a') as f: f.write('Changed instruction.\n')
        self.assertEqual(self.registry.explain('bounded-coding','coder')['effective_policy'],'ask')
        with self.assertRaises(ValueError):
            self.registry.approve('bounded-coding','coder',expected_hash=package['hash'],actor='operator')

    def test_snapshot_does_not_change_with_live_library(self):
        target = self.base / 'snapshot'
        receipt = snapshot_skill(self.skill,target)
        before = (target / 'SKILL.md').read_bytes()
        (self.skill / 'SKILL.md').write_text('Changed live content')
        self.assertEqual((target / 'SKILL.md').read_bytes(),before)
        self.assertFalse(target.is_symlink())
        self.assertEqual(inspect_package(target,name='bounded-coding')['hash'],receipt['hash'])

    def test_symlink_and_secret_packages_are_never_copied(self):
        outside = self.base / 'outside'; outside.write_text('private content')
        (self.skill / 'escape').symlink_to(outside)
        with self.assertRaises(ValueError): snapshot_skill(self.skill,self.base/'no-copy')
        self.assertFalse((self.base/'no-copy').exists())
        (self.skill/'escape').unlink()
        token = 'ghp_' + 'Q'*32
        (self.skill/'credential.txt').write_text(token)
        report = self.registry.inspect('bounded-coding')
        self.assertTrue(report['issues']); self.assertNotIn(token,json.dumps(report))
        with self.assertRaises(ValueError): self.registry.stage(self.skill)
        self.assertFalse((self.base/'state/skill-imports').exists())

    def test_import_is_inert_until_hash_review_and_activation(self):
        source = self.package(self.base/'external'/'imported-guide')
        (source/'scripts').mkdir(); script=source/'scripts'/'danger.sh'
        marker=self.base/'executed'; script.write_text(f'#!/bin/sh\ntouch {marker}\n');script.chmod(0o755)
        staged=self.registry.stage(source)
        self.assertFalse((self.root/'imported-guide').exists());self.assertFalse(marker.exists())
        self.assertEqual(staged['status'],'imported-unreviewed')
        with self.assertRaises(ValueError): self.registry.activate(staged['id'],expected_hash='stale',actor='operator')
        active=self.registry.activate(staged['id'],expected_hash=staged['hash'],actor='operator')
        self.assertEqual(active['trust'],'reviewed');self.assertFalse(marker.exists())
        self.assertEqual(self.registry.explain('imported-guide','coder')['effective_policy'],'allow')
        (self.root/'imported-guide/SKILL.md').write_text('Changed imported content')
        self.assertEqual(self.registry.explain('imported-guide','coder')['effective_policy'],'deny')

    def test_interrupted_activation_never_becomes_local_trusted(self):
        source=self.package(self.base/'external'/'imported-guide')
        staged=self.registry.stage(source)
        with patch('agent_console.skill_registry._copy_validated',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.registry.activate(staged['id'],expected_hash=staged['hash'],actor='operator')
        self.assertNotEqual(self.registry.inspect('imported-guide')['trust'],'local-trusted')
        self.assertEqual(self.registry.explain('imported-guide','coder')['effective_policy'],'deny')

    def test_policy_compatibility_and_project_scope(self):
        (self.skill/'agent-console.json').write_text(json.dumps({'scope':'project','project':str(self.base/'project'),'compatible_profiles':['reviewer'],'compatible_harnesses':['codex']}))
        self.assertEqual(self.registry.explain('bounded-coding','coder','codex',str(self.base/'project'))['effective_policy'],'deny')
        self.assertEqual(self.registry.explain('bounded-coding','reviewer','codex',str(self.base/'project/sub'))['effective_policy'],'allow')
        self.assertEqual(self.registry.explain('bounded-coding','reviewer','codex',str(self.base/'other'))['effective_policy'],'deny')
        self.assertEqual(self.registry.explain('bounded-coding','reviewer','shell',str(self.base/'project'))['effective_policy'],'deny')

    def test_dependency_and_executable_diagnostics_do_not_run_commands(self):
        (self.skill/'agent-console.json').write_text(json.dumps({'required_binaries':['missing-console-test-binary']}))
        self.assertTrue(self.registry.inspect('bounded-coding')['issues'])
        (self.skill/'agent-console.json').write_text(json.dumps({'required_binaries':['echo;touch marker']}))
        self.assertTrue(self.registry.inspect('bounded-coding')['issues'])
        (self.skill/'agent-console.json').unlink()
        unexpected=self.skill/'install.sh';unexpected.write_text('#!/bin/sh\nexit 0\n');unexpected.chmod(0o755)
        self.assertTrue(self.registry.inspect('bounded-coding')['issues'])

    def test_reads_do_not_create_policy_state(self):
        self.registry.inspect('bounded-coding');self.registry.explain('bounded-coding','coder')
        self.assertFalse((self.base/'state').exists())

    def test_revoked_approval_and_blocked_revision_cannot_launch(self):
        (self.skill/'agent-console.json').write_text(json.dumps({'approval':'ask'}))
        package = self.registry.inspect('bounded-coding')
        self.registry.approve('bounded-coding', 'coder', expected_hash=package['hash'], actor='operator')
        self.registry.revoke('bounded-coding', 'coder')
        self.assertEqual(self.registry.explain('bounded-coding', 'coder')['effective_policy'], 'ask')
        self.registry.decide('bounded-coding', decision='blocked', expected_hash=package['hash'], actor='operator')
        self.assertEqual(self.registry.explain('bounded-coding', 'coder')['effective_policy'], 'deny')

    def test_delivery_receipt_survives_snapshot_cleanup(self):
        from agent_console.skills import isolate_skills, cleanup_isolated_skills
        from agent_console.skill_registry import record_delivery, read_deliveries
        target = self.base/'isolated'
        isolate_skills(target, self.root, [{'name':'bounded-coding', 'selection':'shared'}])
        record_delivery(self.base/'state', 'sess-fixture', target, tool='codex', profile='coder', isolated=True)
        original = read_deliveries(self.base/'state', 'sess-fixture')['latest']
        cleanup_isolated_skills(target)
        self.assertEqual(read_deliveries(self.base/'state', 'sess-fixture')['latest'], original)
        self.assertEqual(original['skills'][0]['selection'], 'shared')
        self.assertIsNone(read_deliveries(self.base/'state', 'sess-old')['latest'])

    def test_global_sync_revokes_owned_restricted_links(self):
        from agent_console.skills import sync_skills
        home = self.base/'home'
        with patch('agent_console.skills.SKILL_CATALOG', []):
            sync_skills(canonical_root=self.root, home=home, version_probe=lambda *args: '1.18.30')
            path = home/'.codex/skills/bounded-coding'
            self.assertTrue(path.is_symlink())
            (self.skill/'agent-console.json').write_text(json.dumps({'approval':'ask'}))
            result = sync_skills(canonical_root=self.root, home=home, version_probe=lambda *args: '1.18.30')
            self.assertFalse(path.exists())
            self.assertIn('bounded-coding', result['session_only'])
            self.assertTrue(any(item['action']=='unlinked-revoked' for item in result['changed']))

    def test_duplicate_native_ids_block_session_resolution(self):
        from agent_console.database import Database
        from agent_console.skills import resolve_session_skills
        self.package(self.root/'duplicate-id')
        db = Database(self.base/'state'/'test.sqlite3');db.migrate()
        with patch('agent_console.skills.SKILL_CATALOG', []):
            result = resolve_session_skills(db, 'coder', 'codex', canonical_root=self.root, shared_allowlist=['bounded-coding', 'duplicate-id'])
        self.assertFalse(result['validation']['valid'])
        self.assertIn('duplicate native skill id', '; '.join(result['validation']['issues']))
