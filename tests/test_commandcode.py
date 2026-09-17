import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_console.auth import AuthRegistry
from agent_console.commandcode import (BASE_URL, DEFAULT_MODEL, catalogue, pi_model_entries,
                                       provision, selected_model)
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
    def _launch(self, tool, model=None):
        return provider_adapter(tool, self.registry).build_launch_spec(
            context=self.context, context_path=self.context_path, model=model,
            role='Profile: general', profile='general', cwd=self.root, read_only=False, agent_mode=None)
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
                root = Path(spec.environment['PI_CODING_AGENT_DIR'])
                provider = json.loads((root / 'models.json').read_text())['providers']['commandcode']
                models = provider['models']
                self.assertEqual([m['id'] for m in models], [DEFAULT_MODEL, 'test/alternate'])
                self.assertTrue(all(m['contextWindow'] == 128000 for m in models))
                # Credential is an explicit environment-variable reference, never a literal.
                self.assertEqual(provider['apiKey'], '$CMD_API_KEY')
                auth = json.loads((root / 'auth.json').read_text())
                self.assertEqual(auth['commandcode'], {'type': 'api_key', 'key': '$CMD_API_KEY'})
                config = root / 'models.json'
            else:
                self.assertEqual(spec.argv[1:5], ['chat', '--cli', '--provider', 'custom'])
                config = Path(spec.environment['HERMES_HOME']) / 'config.yaml'
                self.assertIn('${CMD_API_KEY}', config.read_text())
            self.assertIn('CMD_API_KEY', config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
    def test_pi_auth_json_is_rewritten_to_environment_reference(self):
        from agent_console.commandcode import ensure_pi_auth_env_reference
        path = self.root / 'auth.json'
        path.write_text(json.dumps({'commandcode': {'type': 'api_key', 'key': 'literal-secret-value'},
                                    'other-provider': {'type': 'oauth', 'access': 'keep-me'}}))
        ensure_pi_auth_env_reference(path)
        data = json.loads(path.read_text())
        self.assertEqual(data['commandcode'], {'type': 'api_key', 'key': '$CMD_API_KEY'})
        self.assertEqual(data['other-provider'], {'type': 'oauth', 'access': 'keep-me'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('literal-secret-value', path.read_text())

    def test_native_sessions_receive_generated_mcp_config_without_values(self):
        n8n_secret = 'fixture-n8n-token-do-not-publish-123'
        directus_secret = 'fixture-directus-token-do-not-publish-456'
        with patch.dict(os.environ, {'N8N_MCP_TOKEN': n8n_secret,
                                     'DIRECTUS_MCP_TOKEN': directus_secret,
                                     'N8N_MCP_URL': 'http://n8n.example.test/mcp'},
                        clear=False):
            os.environ.pop('DIRECTUS_MCP_URL', None)
            pi_root = Path(self._launch('pi').environment['PI_CODING_AGENT_DIR'])
            pi_config = json.loads((pi_root / 'mcp.json').read_text())
            self.assertEqual(sorted(pi_config['mcpServers']), ['directus', 'n8n'])
            self.assertEqual(pi_config['mcpServers']['n8n'], {
                'url': 'http://n8n.example.test/mcp', 'auth': 'bearer',
                'bearerTokenEnv': 'N8N_MCP_TOKEN', 'lifecycle': 'lazy',
                'requestTimeoutMs': 180000,
            })
            self.assertEqual(pi_config['mcpServers']['directus']['url'], 'http://192.168.1.71:8055/mcp')
            self.assertEqual((pi_root / 'mcp.json').stat().st_mode & 0o777, 0o600)

            hermes_root = Path(self._launch('hermes').environment['HERMES_HOME'])
            hermes_config = (hermes_root / 'config.yaml').read_text()
            self.assertIn('Bearer ${N8N_MCP_TOKEN}', hermes_config)
            self.assertIn('Bearer ${DIRECTUS_MCP_TOKEN}', hermes_config)
            # The reasoning gate must survive the added server block.
            self.assertIn('reasoning_effort', hermes_config)
            for text in ((pi_root / 'mcp.json').read_text(), hermes_config):
                self.assertNotIn(n8n_secret, text)
                self.assertNotIn(directus_secret, text)

    def test_native_sessions_omit_mcp_config_without_credentials(self):
        with patch.dict(os.environ, {}, clear=False):
            for name in ('N8N_MCP_TOKEN', 'DIRECTUS_MCP_TOKEN'):
                os.environ.pop(name, None)
            pi_root = Path(self._launch('pi').environment['PI_CODING_AGENT_DIR'])
            self.assertFalse((pi_root / 'mcp.json').exists())
            hermes_root = Path(self._launch('hermes').environment['HERMES_HOME'])
            self.assertNotIn('mcp_servers', (hermes_root / 'config.yaml').read_text())

    def test_unavailable_model_does_not_write_anything(self):
        untouched = self.root / 'untouched'
        with patch('agent_console.commandcode.catalogue', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError): provision(untouched, 'fixture')
        self.assertFalse(untouched.exists())
    def test_provisioning_backup_permissions_and_redaction(self):
        key = 'fixture-key-do-not-publish-12345'
        catalogue_entry = {'id': DEFAULT_MODEL, 'name': 'DeepSeek V4.1 Flash', 'context_length': 1000000}
        original = self.registry.registry_path.read_text()
        with patch('agent_console.commandcode.catalogue', return_value=[catalogue_entry]):
            result = provision(self.registry.config_dir, key)
        self.assertEqual((Path(result['backup'])/'auth-contexts.json').read_text(), original)
        self.assertEqual(self.registry.secret_path('commandcode-main').stat().st_mode & 0o777, 0o600)
        public = json.dumps(self.registry.list_contexts()) + json.dumps(result) + self.registry.registry_path.read_text()
        self.assertNotIn(key, public)
        for tool in ('pi', 'hermes'):
            context = self.registry.get_context(tool, require_ready=True)
            self.assertEqual(context['model'], DEFAULT_MODEL)
            self.assertEqual(context['models'], [DEFAULT_MODEL])
            self.assertEqual(context['model_catalogue'], [catalogue_entry])

    def test_pi_models_expose_full_catalogue_with_real_context_windows(self):
        context = {**self.context, 'models': ['a/b', 'c/d'], 'model_catalogue': [
            {'id': 'a/b', 'name': 'A B', 'context_length': 1000000},
            {'id': 'c/d', 'name': 'C D', 'context_length': 256000},
            {'id': 'a/b', 'name': 'duplicate ignored', 'context_length': 1},
        ]}
        entries = pi_model_entries(context, selected='c/d')
        self.assertEqual([e['id'] for e in entries], ['a/b', 'c/d'])
        self.assertEqual(entries[0]['contextWindow'], 1000000)
        self.assertEqual(entries[1]['contextWindow'], 256000)
        self.assertEqual(entries[0]['name'], 'A B')
        self.assertEqual(entries[0]['maxTokens'], 8192)
        self.assertEqual(entries[0]['compat'], {'supportsDeveloperRole': False})

    def test_pi_advertises_thinking_only_for_supported_models(self):
        from agent_console.commandcode import REASONING_MODELS
        supported = next(iter(sorted(REASONING_MODELS)))
        context = {**self.context, 'models': [supported, 'claude-sonnet-5'], 'model_catalogue': [
            {'id': supported, 'name': 'Supported', 'context_length': 1000},
            {'id': 'claude-sonnet-5', 'name': 'Unsupported', 'context_length': 1000},
        ]}
        entries = {e['id']: e for e in pi_model_entries(context)}
        self.assertTrue(entries[supported]['reasoning'])
        self.assertEqual(entries[supported]['thinkingLevelMap'], {'minimal': None, 'xhigh': 'xhigh'})
        self.assertNotIn('reasoning', entries['claude-sonnet-5'])
        self.assertNotIn('thinkingLevelMap', entries['claude-sonnet-5'])

    def test_hermes_sets_reasoning_effort_only_for_supported_models(self):
        from agent_console.commandcode import REASONING_MODELS
        supported = next(iter(sorted(REASONING_MODELS)))
        for model, expected in ((DEFAULT_MODEL, True), ('claude-sonnet-5', False)):
            context = {**self.context, 'models': [DEFAULT_MODEL, 'claude-sonnet-5']}
            spec = provider_adapter('hermes', self.registry).build_launch_spec(
                context=context, context_path=self.context_path, model=model,
                role='Profile: general', profile='general', cwd=self.root,
                read_only=False, agent_mode=None)
            config = (Path(spec.environment['HERMES_HOME']) / 'config.yaml').read_text()
            self.assertEqual('reasoning_effort' in config, expected, config)
            if expected:
                self.assertIn('"high"', config)

    def test_hermes_reasoning_gate_is_model_aware(self):
        # Regression for the /model-switch hazard: the session-level effort must
        # not be sent for a model that rejects reasoning_effort.
        import importlib.util
        import sys
        import types
        from agent_console.commandcode import REASONING_MODELS
        supported = next(iter(sorted(REASONING_MODELS)))
        spec = provider_adapter('hermes', self.registry).build_launch_spec(
            context={**self.context, 'models': [DEFAULT_MODEL, 'claude-sonnet-5']},
            context_path=self.context_path, model=DEFAULT_MODEL, role='Profile: general',
            profile='general', cwd=self.root, read_only=False, agent_mode=None)
        root = Path(spec.environment['HERMES_HOME'])
        plugin = root / 'plugins/model-providers/commandcode/__init__.py'
        self.assertTrue(plugin.is_file())
        self.assertTrue((plugin.parent / 'plugin.yaml').is_file())
        self.assertEqual(plugin.stat().st_mode & 0o777, 0o600)
        payload = json.loads((root / 'reasoning-models.json').read_text())
        self.assertEqual(payload['models'], sorted(REASONING_MODELS))
        self.assertEqual(payload['default_effort'], 'high')

        registered = []

        class _Base:
            name = 'custom'

            def build_api_kwargs_extras(self, *, reasoning_config=None, model=None, **kwargs):
                if reasoning_config and reasoning_config.get('effort'):
                    return {}, {'reasoning_effort': reasoning_config['effort']}
                return {}, {}

        base = _Base()
        providers_stub = types.ModuleType('providers')
        providers_stub.get_provider_profile = lambda name: base if name == 'custom' else None
        providers_stub.register_provider = registered.append
        base_stub = types.ModuleType('providers.base')
        base_stub.ProviderProfile = _Base
        constants_stub = types.ModuleType('hermes_constants')
        constants_stub.get_hermes_home = lambda: root
        saved = {key: sys.modules.get(key) for key in ('providers', 'providers.base', 'hermes_constants')}
        sys.modules.update({'providers': providers_stub, 'providers.base': base_stub,
                            'hermes_constants': constants_stub})
        try:
            module_spec = importlib.util.spec_from_file_location('cc_gate_under_test', plugin)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            self.assertEqual(len(registered), 1)
            profile = registered[0]

            def effort(model, level):
                return profile.build_api_kwargs_extras(
                    reasoning_config={'enabled': True, 'effort': level}, model=model)[1]

            self.assertEqual(effort(supported, 'high')['reasoning_effort'], 'high')
            self.assertEqual(effort(supported, 'minimal')['reasoning_effort'], 'low')
            self.assertEqual(effort(supported, 'bogus'), {})
            self.assertEqual(effort('claude-sonnet-5', 'high'), {})
            self.assertEqual(effort(None, 'high'), {})
        finally:
            for key, value in saved.items():
                sys.modules.pop(key, None) if value is None else sys.modules.update({key: value})

    def test_catalogue_requires_exact_v41_model(self):
        from io import BytesIO
        with patch('urllib.request.urlopen', return_value=BytesIO(json.dumps({'data':[{'id':'deepseek/deepseek-v4-flash'}]}).encode())):
            with self.assertRaisesRegex(ValueError, 'unavailable'): catalogue('fixture-key-long-enough-123')

    def test_logging_redacts_commandcode_assignments_and_bearer(self):
        from agent_console.logging_config import redact_secrets
        key = 'fixture-sensitive-commandcode-token-123'
        for message in ('CMD_API_KEY='+key, 'Authorization: Bearer '+key):
            self.assertNotIn(key, redact_secrets(message))
