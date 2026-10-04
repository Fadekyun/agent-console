from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_console.database import Database
from agent_console.providers import PiAdapter
from agent_console.skill_capabilities import SKILL_TOOL_CAPABILITIES
from agent_console.skills import assign_skill, resolve_session_skills, sync_skills


class PiSkillDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        self.skill = self.library / 'portable-guide'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: portable-guide\ndescription: A portable fixture.\nmetadata:\n  agent-console/version: "1"\n---\nInspect the selected input.\n')
        self.agent = self.root / 'agent'
        self.agent.mkdir()
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()

    def test_native_agent_directory_points_to_selected_snapshot_and_preserves_collisions(self):
        adapter = PiAdapter(None)
        env = {'PI_CODING_AGENT_DIR': str(self.agent)}
        adapter.configure_shared_skills(environment=env, isolated_skills_root=self.snapshot)
        self.assertEqual((self.agent / 'skills').resolve(), self.snapshot)
        adapter.configure_shared_skills(environment=env, isolated_skills_root=self.snapshot)
        (self.agent / 'skills').unlink()
        (self.agent / 'skills').mkdir()
        sentinel = self.agent / 'skills' / 'operator-file'
        sentinel.write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            adapter.configure_shared_skills(environment=env, isolated_skills_root=self.snapshot)
        self.assertEqual(sentinel.read_text(), 'preserve')
        with self.assertRaisesRegex(ValueError, 'per-session'):
            adapter.configure_shared_skills(environment={}, isolated_skills_root=self.snapshot)

    def test_pi_sync_is_version_gated_and_uses_native_root(self):
        (self.skill / 'agent-console.json').write_text('{"compatible_harnesses":["pi"]}')
        with patch('agent_console.skills.SKILL_CATALOG', []):
            denied = sync_skills(canonical_root=self.library, home=self.root / 'home', version_probe=lambda *_: '0.99.1')
            self.assertFalse((self.root / 'home/.pi/agent/skills/portable-guide').exists())
            allowed = sync_skills(canonical_root=self.library, home=self.root / 'home', version_probe=lambda *_: '0.99.2')
        self.assertFalse(denied['ok'])
        self.assertTrue(allowed['ok'])
        self.assertEqual((self.root / 'home/.pi/agent/skills/portable-guide').resolve(), self.skill)
        self.assertFalse(SKILL_TOOL_CAPABILITIES['pi'].configured_sources_inspected)

    def test_profile_and_shared_delivery_fail_closed_for_unverified_versions(self):
        database = Database(self.root / 'state/main.sqlite3')
        database.migrate()
        with patch('agent_console.skills.SKILL_CATALOG', []):
            assign_skill(database, 'general', 'portable-guide', canonical_root=self.library)
            for tool in ('pi', 'opencode'):
                with self.subTest(tool=tool), patch('agent_console.skills._default_version_probe', return_value='unknown'):
                    result = resolve_session_skills(database, 'general', tool, shared_allowlist=['portable-guide'], canonical_root=self.library)
                    self.assertFalse(result['validation']['valid'])
                    self.assertIn('unverified-version', ';'.join(result['validation']['issues']))
                    self.assertFalse(result['delivery_version']['mutation_allowed'])
            with patch('agent_console.skills._default_version_probe', return_value='0.99.2'):
                result = resolve_session_skills(database, 'general', 'pi', canonical_root=self.library)
                self.assertTrue(result['validation']['valid'], result['validation'])
                self.assertEqual(result['materialized'][0]['name'], 'portable-guide')
                self.assertEqual(result['delivery_version']['installed_version'], '0.99.2')


if __name__ == '__main__':
    unittest.main()
