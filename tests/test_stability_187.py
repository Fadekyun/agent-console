import concurrent.futures
import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_console.manager import _launcher_exports
from agent_console.native_recovery import resolve_binding, RecoveryUnavailable, codex_resume_argv
from agent_console.resources import resource_status, ResourceUnavailable, GIB
from test_shared_skill_discovery import SharedSkillSessionTests
from native_fixture import seed_native, THREAD


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.path = Path(self.temp.name) / 'resources.json'
        self.env = patch.dict(os.environ, {'AGENT_CONSOLE_RESOURCE_SNAPSHOT': str(self.path)});self.env.start()
        self.data = dict(sampled_at=time.time(), host_available_bytes=3*GIB,
            host_full_psi_avg10=0, ct115_memory_headroom_bytes=GIB,
            ct115_disk_free_bytes=8*GIB, tmp_free_bytes=20*GIB)
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.data));self.path.chmod(0o644)

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_thresholds_staleness_invalid_and_missing(self):
        # CI runs as root in the isolated Linux fixture to exercise trust.
        if os.getuid() != 0:self.skipTest('root-owned snapshot fixture')
        self.assertTrue(resource_status()['launch_allowed'])
        for key, value, reason in (
            ('host_available_bytes', 2*GIB-1, 'host_memory_low'),
            ('host_full_psi_avg10', 5.01, 'host_memory_pressure'),
            ('ct115_memory_headroom_bytes', GIB//2-1, 'container_memory_low'),
            ('ct115_disk_free_bytes', 4*GIB-1, 'container_disk_low'),
            ('tmp_free_bytes', 10*GIB-1, 'temporary_disk_low'),
            ('sampled_at', time.time()-61, 'snapshot_stale')):
            old=self.data[key];self.data[key]=value;self.write()
            self.assertIn(reason,resource_status()['reasons']);self.data[key]=old
        self.path.write_text('{}');self.assertEqual(resource_status()['reasons'],['snapshot_unavailable'])
        self.path.unlink();self.assertFalse(resource_status()['launch_allowed'])

    def test_untrusted_permissions_and_symlink(self):
        self.path.chmod(0o666);self.assertFalse(resource_status()['launch_allowed'])
        self.path.unlink();self.path.symlink_to('/dev/null');self.assertFalse(resource_status()['launch_allowed'])


class RecoveryTests(unittest.TestCase):
    setUp = SharedSkillSessionTests.setUp
    tearDown = SharedSkillSessionTests.tearDown

    def create(self):
        session=self.manager.create(tool='codex',profile='general',name='native-recovery',repository=str(self.workspace))
        self.home,self.rollout=seed_native(session)
        return session

    def test_rename_restart_and_stopped_resume_keep_native_identity(self):
        session=self.create();before=self.rollout.read_bytes()
        renamed=self.manager.rename(session['tmux_name'],'renamed-native')
        restarted=self.manager.restart('renamed-native')
        text=Path(restarted['launcher_path']).read_text()
        self.assertIn('resume '+THREAD,text)
        self.assertEqual(_launcher_exports(text)['CODEX_HOME'],str(self.home))
        self.assertEqual(before,self.rollout.read_bytes())
        # Simulate reboot losing only tmux, without calling Console Kill.
        self.manager.tmux.kill('renamed-native')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.manager.resume('renamed-native'),range(2)))
        self.assertTrue(all(r['running'] for r in results))
        self.assertEqual({r['id'] for r in results},{session['id']})
        self.assertEqual(len(self.manager.tmux.list_sessions()),1)
        self.assertEqual(before,self.rollout.read_bytes())

    def test_ambiguous_missing_and_subagent_never_start_fresh(self):
        session=self.create()
        seed_native(session,'01a106f0-0efb-7892-b871-e7e8b167bd79')
        with self.assertRaisesRegex(RecoveryUnavailable,'ambiguous'):
            self.manager.restart(session['tmux_name'])
        with sqlite3.connect(self.home/'state_5.sqlite') as conn:
            conn.execute("UPDATE threads SET source='subagent' WHERE id!=?",(THREAD,))
        self.manager.restart(session['tmux_name'])
        self.rollout.unlink()
        with self.assertRaises(RecoveryUnavailable):self.manager.restart(session['tmux_name'])

    def test_resource_rejection_preserves_launcher_and_running_agent(self):
        session=self.create(); launcher=Path(session['launcher_path']);before=launcher.read_bytes()
        with patch('agent_console.manager.require_launch_resources',side_effect=ResourceUnavailable(['host_memory_low'])):
            with self.assertRaises(ResourceUnavailable):self.manager.restart(session['tmux_name'])
            with self.assertRaises(ResourceUnavailable):self.manager.create(tool='shell',profile='general',name='blocked')
        self.assertEqual(before,launcher.read_bytes());self.assertTrue(self.manager.inspect(session['tmux_name'])['running'])
        self.assertFalse(self.manager.tmux.exists('blocked'))

    def test_global_home_is_not_automatically_selected(self):
        session=self.create();text=Path(session['launcher_path']).read_text()
        with self.assertRaises(RecoveryUnavailable):
            resolve_binding(self.settings.state_dir,session,{'CODEX_HOME':str(self.workspace)},text)

    def test_permission_model_and_effort_flags_survive_resume(self):
        argv=['/bin/codex','--ask-for-approval','never','--sandbox','read-only','-m','gpt-test',
            '-c','model_reasoning_effort="high"','original task']
        self.assertEqual(codex_resume_argv(argv,THREAD),argv[:-1]+['resume',THREAD])

    def test_restart_refreshes_missing_release_runtime_without_changing_native_history(self):
        import sys,shlex
        from agent_console.native_recovery import launcher_command
        session=self.create();launcher=Path(session['launcher_path']);text=launcher.read_text()
        prefix,argv=launcher_command(text);snapshot=prefix[-1]
        marker=text.index('\nexec ')
        launcher.write_text(text[:marker]+'\nexec '+shlex.join([
            '/removed-release/.runtime/bin/python','-I','/removed-release/agent_console/environment_bootstrap.py',snapshot,*argv])+'\n')
        restarted=self.manager.restart(session['tmux_name'])
        new_prefix,new_argv=launcher_command(launcher.read_text())
        self.assertEqual(new_prefix[0],sys.executable)
        self.assertTrue(Path(new_prefix[2]).is_file())
        self.assertEqual(new_prefix[-1],snapshot)
        self.assertEqual(new_argv[-2:],['resume',THREAD])
        self.assertEqual(_launcher_exports(launcher.read_text())['CODEX_HOME'],str(self.home))
        self.assertTrue(self.rollout.is_file());self.assertEqual(restarted['id'],session['id'])
