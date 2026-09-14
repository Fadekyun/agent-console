import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_console.auth import AuthRegistry
from agent_console.commandcode import BASE_URL, DEFAULT_MODEL, catalogue, provision, selected_model
from agent_console.providers import provider_adapter
from agent_console.profiles import validate_profile_capability
from agent_console.validation import validate_tool

class CommandCodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry = AuthRegistry(self.root / 'config', home=self.root)
        self.context = dict(provider='commandcode', base_url=BASE_URL, model=DEFAULT_MODEL,
                            models=[DEFAULT_MODEL, 'test/alternate'], verified=True, secret_ref='commandcode-main')
        self.context_path = self.root / 'context.md'
        self.context_path.write_text('Profile: general\nSession: test\nPROJECT_CONTEXT_MARKER')
    def tearDown(self):
        self.tmp.cleanup()
    def test_registration_and_no_implicit_verification(self):
        validate_tool('pi')
        self.assertEqual(self.registry.get_context('pi')['status'], 'setup-required')
        for tool in ('pi', 'hermes'):
            self.assertEqual(validate_profile_capability('planner', tool, None)['enforcement'], 'unsupported')
    def test_model_selection_fails_closed(self):
        self.assertEqual(selected_model(self.context), DEFAULT_MODEL)
        self.assertEqual(selected_model(self.context, 'test/alternate'), 'test/alternate')
        for context, model in [(self.context, 'deepseek/deepseek-v4-flash'),
                               ({**self.context, 'verified': False}, None),
                               ({**self.context, 'base_url': 'https://other.test'}, None)]:
            with self.assertRaises(ValueError): selected_model(context, model)
    def test_native_launches_deliver_context_and_runtime_secret_reference(self):
        for tool in ('pi', 'hermes'):
            spec = provider_adapter(tool, self.registry).build_launch_spec(
                context=self.context, context_path=self.context_path, model=None,
                role='Profile: general', profile='general', cwd=self.root, read_only=False, agent_mode=None)
            self.assertIn(DEFAULT_MODEL, spec.argv)
            self.assertNotIn('gpt-4o-mini', str(spec))
            self.assertEqual(spec.secret_files, [self.registry.secret_path('commandcode-main')])
            self.assertEqual(spec.environment['AGENT_CONSOLE_CONTEXT_FILE'], str(self.context_path))
            if tool == 'pi':
                self.assertIn(str(self.context_path), spec.argv)
                config = Path(spec.environment['PI_CODING_AGENT_DIR']) / 'models.json'
            else:
                self.assertEqual(spec.argv[1:5], ['chat', '--cli', '--provider', 'custom'])
                config = Path(spec.environment['HERMES_HOME']) / 'config.yaml'
            self.assertIn('CMD_API_KEY', config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
    def test_unavailable_model_does_not_write_anything(self):
        untouched = self.root / 'untouched'
        with patch('agent_console.commandcode.catalogue', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError): provision(untouched, 'fixture')
        self.assertFalse(untouched.exists())
    def test_provisioning_backup_permissions_and_redaction(self):
        key = 'fixture-key-do-not-publish-12345'
        original = self.registry.registry_path.read_text()
        with patch('agent_console.commandcode.catalogue', return_value=[DEFAULT_MODEL]):
            result = provision(self.registry.config_dir, key)
        self.assertEqual((Path(result['backup'])/'auth-contexts.json').read_text(), original)
        self.assertEqual(self.registry.secret_path('commandcode-main').stat().st_mode & 0o777, 0o600)
        public = json.dumps(self.registry.list_contexts()) + json.dumps(result) + self.registry.registry_path.read_text()
        self.assertNotIn(key, public)
        for tool in ('pi', 'hermes'):
            self.assertEqual(self.registry.get_context(tool, require_ready=True)['model'], DEFAULT_MODEL)
    def test_catalogue_requires_exact_v41_model(self):
        from io import BytesIO
        with patch('urllib.request.urlopen', return_value=BytesIO(json.dumps({'data':[{'id':'deepseek/deepseek-v4-flash'}]}).encode())):
            with self.assertRaisesRegex(ValueError, 'unavailable'): catalogue('fixture-key-long-enough-123')

    def test_logging_redacts_commandcode_assignments_and_bearer(self):
        from agent_console.logging_config import redact_secrets
        key = 'fixture-sensitive-commandcode-token-123'
        for message in ('CMD_API_KEY='+key, 'Authorization: Bearer '+key):
            self.assertNotIn(key, redact_secrets(message))
