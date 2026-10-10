"""Execute the shipped wrapper using synthetic secrets and an inert native CLI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WRAPPER = Path(__file__).resolve().parents[1] / 'scripts/pi-wrapper'


@unittest.skipUnless(shutil.which('node'), 'Node required for native Pi wrapper fixtures')
class PiWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.agent = self.root/'agent'; self.agent.mkdir()
        self.secrets = self.root/'secrets.env'
        self.secrets.write_text('OPENROUTER_API_KEY=synthetic-host\nBUSHI_MCP_TOKEN=synthetic-host\n')
        self.cli = self.root/'inert-cli.cjs'
        self.cli.write_text("console.log(JSON.stringify({openrouter:process.env.OPENROUTER_API_KEY??null,bushi:process.env.BUSHI_MCP_TOKEN??null,args:process.argv.slice(2)}))")
        scripts = self.root/'.local/bin'; scripts.mkdir(parents=True)
        (scripts/'pi-normalize-mcp-config.cjs').write_text("require('fs').writeFileSync(process.env.HOME+'/normalizer-ran','yes')")
        self.config = {'mcpServers':{'openrouter':{'url':'https://example.invalid','enabled':False},
             'bushi':{'url':'https://example.invalid','disabled':True},
             'custom':{'command':'inert','args':['preserve'],'headers':{'Authorization':'Bearer ${CUSTOM_TOKEN}'}}}}
        self.mcp = self.agent/'mcp.json'; self.mcp.write_text(json.dumps(self.config))
        self.before = self.mcp.read_bytes()
        self.env = {'HOME':str(self.root),'PATH':os.environ['PATH'], 'AGCONSOLE_PI_NODE':shutil.which('node'),
            'AGCONSOLE_PI_CLI':str(self.cli),'PI_CODING_AGENT_DIR':str(self.agent),
            'AGENT_CONSOLE_OPENROUTER_ENV':str(self.secrets),'AGENT_CONSOLE_BUSHI_ENV':str(self.secrets),
            'AGENT_CONSOLE_CONTEXT_FILE':str(self.root/'context.md'),'CMD_API_KEY':'synthetic-commandcode'}

    def run_wrapper(self, env=None, args=()):
        return subprocess.run(['bash',str(WRAPPER),*args],env=env or self.env,
            capture_output=True,text=True,timeout=10)

    def test_managed_override_empty_and_suppression_survive_host_defaults(self):
        for value in ('synthetic-project','',None):
            with self.subTest(value=value):
                env=dict(self.env)
                if value is not None:env.update(OPENROUTER_API_KEY=value,BUSHI_MCP_TOKEN=value)
                result=self.run_wrapper(env, ['--model','fixture model'])
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout), {'openrouter':value,'bushi':value,'args':['--model','fixture model']})
                self.assertFalse((self.root/'normalizer-ran').exists())
                self.assertEqual(self.mcp.read_bytes(),self.before)
        self.assertEqual(json.loads((self.agent/'settings.json').read_text())['theme'],'dark')
        self.assertNotIn('synthetic-',self.mcp.read_text())

    def test_explicit_settings_skills_and_extensions_preserved(self):
        settings=self.agent/'settings.json'; settings.write_text('{"theme":"light","custom":true}\n')
        prior=settings.read_bytes()
        selected=self.root/'selected';selected.mkdir();(self.agent/'skills').symlink_to(selected)
        extensions=self.root/'extensions';extensions.mkdir();(self.agent/'extensions').symlink_to(extensions)
        env={**self.env,'AGCONSOLE_PI_THEME':'dark','AGCONSOLE_SKILLS_ROOT':str(self.root)}
        for _ in range(2):self.assertEqual(self.run_wrapper(env).returncode,0)
        self.assertEqual(settings.read_bytes(),prior)
        self.assertEqual((self.agent/'skills').resolve(),selected)
        self.assertEqual((self.agent/'extensions').resolve(),extensions)
        self.assertEqual(self.mcp.read_bytes(),self.before)

    def test_partial_managed_markers_do_not_load_host_secrets(self):
        for marker in ('AGENT_CONSOLE_SESSION_ID','AGENT_CONSOLE_EVIDENCE_CAPABILITY',
                       'AGENT_CONSOLE_REPORTING_URL','AGENT_CONSOLE_SESSION_NAME'):
            env={**self.env,marker:'synthetic-marker'};env.pop('AGENT_CONSOLE_CONTEXT_FILE')
            result=self.run_wrapper(env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIsNone(json.loads(result.stdout)['openrouter'])
            self.assertFalse((self.root/'normalizer-ran').exists())

    def test_standalone_compatibility_still_loads_host_and_normalizer(self):
        env=dict(self.env);env.pop('AGENT_CONSOLE_CONTEXT_FILE')
        result=self.run_wrapper(env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['openrouter'],'synthetic-host')
        self.assertTrue((self.root/'normalizer-ran').exists())

    def test_invalid_settings_fail_without_overwriting_or_launching(self):
        settings=self.agent/'settings.json';settings.write_text('{broken-synthetic-value')
        result=self.run_wrapper()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')
        self.assertNotIn('broken-synthetic-value',result.stderr)
        self.assertEqual(settings.read_text(),'{broken-synthetic-value')
        self.assertEqual(self.mcp.read_bytes(),self.before)

    def test_explicit_missing_cli_does_not_fall_back(self):
        env={**self.env,'AGCONSOLE_PI_CLI':str(self.root/'missing')}
        result=self.run_wrapper(env)
        self.assertEqual(result.returncode,127)
        self.assertEqual(result.stdout,'')
