import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class PreparedSkillHelpersTests(unittest.TestCase):
    def run_script(self, name, *args, payload=None):
        env=dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE']='1'
        env.pop('AGCONSOLE_RETAINED_SKILLS',None)
        return subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/name),*args],
                              input=payload,text=True,capture_output=True,env=env,timeout=5)

    def test_digest_excludes_frozen_payloads_and_does_not_claim_receipt(self):
        value={'id':'session-id','tmux_name':'fixture','status':'detached','running':False,
               'execution_kind':'integration','initial_task':'private-marker',
               'attention_note':'private-marker','context':'private-marker',
               'request':{'prompt':'private-marker'}}
        result=self.run_script('session-digest.py',payload=json.dumps(value))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('private-marker',result.stdout)
        output=json.loads(result.stdout)
        self.assertEqual(output['session']['status'],'detached')
        self.assertFalse(output['session']['running'])
        self.assertEqual(output['receipt_status'],'not-established-by-session-metadata')

    def test_digest_rejects_error_objects_and_oversized_or_nested_metadata(self):
        for payload in (json.dumps({'error':'private-marker'}), 'x'*262145,
                        json.dumps({'id':'id','tmux_name':'name','status':{'secret':'private-marker'}})):
            with self.subTest(size=len(payload)):
                result=self.run_script('session-digest.py',payload=payload)
                self.assertEqual(result.returncode,2)
                self.assertNotIn('private-marker',result.stderr+result.stdout)

    def test_content_validator_refuses_escape_and_unknown_tool_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'canonical'; skill=root/'fixture'; skill.mkdir(parents=True)
            content=skill/'SKILL.md'
            content.write_text('---\nname: fixture\ntools: not-a-harness\n---\n')
            before=content.read_bytes(); metadata=content.stat()
            result=self.run_script('validate-skill-content.py','--root',str(root))
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertEqual(content.read_bytes(),before)
            self.assertEqual(content.stat().st_mtime_ns,metadata.st_mtime_ns)
            content.unlink(); external=Path(tmp)/'outside';external.write_text('---\nname: outside\n---\n')
            content.symlink_to(external)
            result=self.run_script('validate-skill-content.py','--root',str(root))
            self.assertEqual(result.returncode,1)
            self.assertTrue(content.is_symlink())

    def test_content_validator_accepts_existing_optional_metadata_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill=Path(tmp)/'fixture';skill.mkdir()
            (skill/'SKILL.md').write_text('---\nname: fixture\ndescription: fixture\n---\n# Fixture\n')
            result=self.run_script('validate-skill-content.py','--root',tmp)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok'])
