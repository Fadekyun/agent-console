import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_console.cli import main, parser


class SkillCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / 'skills'
        env = patch.dict(os.environ, {
            'AGCONSOLE_SKILLS_ROOT': str(self.library),
            'AGENT_CONSOLE_STATE_DIR': str(self.root / 'state'),
            'AGENT_CONSOLE_DB': str(self.root / 'state/main.sqlite3'),
            'AGENT_CONSOLE_CONFIG_DIR': str(self.root / 'config'),
            'AGENT_CONSOLE_LOG_DIR': str(self.root / 'logs'),
            'AGCONSOLE_RETAINED_SKILLS': '', 'AGCONSOLE_SHARED_SKILLS': '',
        })
        env.start()
        self.addCleanup(env.stop)

    def package(self, name):
        package = self.library / name
        package.mkdir(parents=True)
        (package / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: A fixture guide.\n---\nFixture.\n')
        return package

    def validate(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch(
            'agent_console.manager.SessionManager', side_effect=AssertionError('Package validation must not initialize sessions')
        ):
            status = main(['skills', 'validate', *args])
        return status, json.loads(output.getvalue())

    def test_package_validation_uses_current_bytes_and_nonzero_on_invalid(self):
        package = self.package('bounded-guide')
        status, result = self.validate('bounded-guide')
        self.assertEqual(status, 0)
        self.assertTrue(result['valid'])
        original = result['hash']
        token = 'ghp_' + 'Q' * 32
        (package / 'secret.txt').write_text(token)
        status, result = self.validate('bounded-guide')
        self.assertEqual(status, 1)
        self.assertFalse(result['valid'])
        self.assertNotEqual(result['hash'], original)
        self.assertNotIn(token, json.dumps(result))
        self.assertFalse((self.root / 'state/main.sqlite3').exists())

    def test_missing_package_and_explicit_profile_named_package(self):
        status, result = self.validate('missing-guide')
        self.assertEqual(status, 1)
        self.assertTrue(result['issues'])
        self.package('coder')
        status, result = self.validate('coder', '--package')
        self.assertEqual(status, 0)
        self.assertEqual(result['target_kind'], 'package')

    def test_legacy_profile_validation_and_unambiguous_profile_command(self):
        fake_manager = type('Manager', (), {'database': object()})()
        for command in ('validate', 'validate-profile'):
            with patch('agent_console.cli.SessionManager', return_value=fake_manager), \
                 patch('agent_console.manager.SessionManager', return_value=fake_manager), \
                 patch('agent_console.skills.validate_profile_skills', return_value={'valid': False, 'issues': ['fixture']}) as validate, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['skills', command, 'coder']), 1)
                validate.assert_called_once_with(fake_manager.database, 'coder')

    def test_unassign_alias_retains_remove_syntax(self):
        from agent_console.skill_cli import run
        fake_manager = type('Manager', (), {'database': object()})()
        for command in ('remove', 'unassign'):
            args = parser().parse_args(['skills', command, 'bounded-guide', '--profile', 'coder'])
            with patch('agent_console.manager.SessionManager', return_value=fake_manager), \
                 patch('agent_console.skills.unassign_skill', return_value={'removed': True}) as unassign:
                self.assertEqual(run(args), {'removed': True})
                unassign.assert_called_once_with(fake_manager.database, 'coder', 'bounded-guide', actor='CLI-user')
