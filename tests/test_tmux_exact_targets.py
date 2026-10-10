"""Real tmux regression: a missing parent must never resolve to its child."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.tmux import Tmux


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class ExactTmuxTargetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tmux = Tmux(socket_name='exact-target-'+str(os.getpid())+'-'+str(id(self)))
        self.addCleanup(lambda: self.tmux.run('kill-server',check=False))
        self.launcher = self.base/'launcher.sh'
        self.launcher.write_text('#!/bin/sh\nprintf "EXACT_LAUNCH_OK\\n"\n')
        self.launcher.chmod(0o700)
        self.tmux.create('parent-child',self.base,self.launcher)

    def wait_output(self, name, text):
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            if text in self.tmux.capture(name)[0]:
                return
            time.sleep(.02)
        self.fail('expected output did not arrive in exact session')

    def test_absent_parent_operations_cannot_read_change_or_kill_prefix_child(self):
        self.wait_output('parent-child','EXACT_LAUNCH_OK')
        self.assertFalse(self.tmux.exists('parent'))
        self.assertEqual(self.tmux.pane_pids('parent'),[])
        self.assertFalse(self.tmux.alternate_screen('parent'))
        pid = self.tmux.pane_pids('parent-child')
        before = self.tmux.capture('parent-child')
        operations = {
            'capture': lambda:self.tmux.capture('parent'),
            'restart': lambda:self.tmux.restart('parent',self.launcher),
            'interrupt': lambda:self.tmux.interrupt('parent'),
            'rename': lambda:self.tmux.rename('parent','wrong-name'),
            'kill': lambda:self.tmux.kill('parent'),
            'scroll': lambda:self.tmux.scroll_history('parent',-5),
            'launcher': lambda:self.tmux._run_launcher('parent',self.launcher),
        }
        for operation, call in operations.items():
            with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                call()
            self.assertTrue(self.tmux.exists('parent-child'))
            self.assertEqual(self.tmux.pane_pids('parent-child'),pid)
        # tmux display-message prints no value for an absent exact target;
        # returning to live mode is therefore a harmless no-op, not a child edit.
        self.tmux.scroll_history('parent',0)
        self.assertEqual(self.tmux.capture('parent-child'),before)
        # Control mode provides a noninteractive real attach, exercising the
        # same command builder used by both CLI exec and the websocket PTY.
        args=self.tmux.attach_command('parent')
        args.insert(args.index('attach-session'),'-C')
        attached=subprocess.run(args,input='detach-client\n',capture_output=True,text=True,timeout=3)
        self.assertIn("can't find session",attached.stdout+attached.stderr)
        self.assertTrue(self.tmux.exists('parent-child'))

    def test_existing_exact_parent_creation_and_kill_preserve_child(self):
        # The prefix child exists first; it must not prevent creating its parent.
        self.assertFalse(self.tmux.exists('parent'))
        self.tmux.create('parent',self.base,self.launcher)
        self.wait_output('parent','EXACT_LAUNCH_OK')
        child_pid=self.tmux.pane_pids('parent-child')
        self.tmux.kill('parent')
        self.assertFalse(self.tmux.exists('parent'))
        self.assertEqual(self.tmux.pane_pids('parent-child'),child_pid)
        self.assertIn('EXACT_LAUNCH_OK',self.tmux.capture('parent-child')[0])
        with self.assertRaises(RuntimeError):
            self.tmux.kill('parent')
        self.assertTrue(self.tmux.exists('parent-child'))

    def test_manager_kill_succeeds_and_repeated_kill_does_not_touch_child(self):
        profiles=self.base/'profiles';profiles.mkdir()
        (profiles/'general.md').write_text('# General')
        manager=SessionManager(Settings(workspace_root=self.base,state_dir=self.base/'state',
            database_path=self.base/'state/db.sqlite',profile_dir=profiles,handoff_dir=self.base/'handoff',
            worktree_root=self.base/'trees',tmux_socket=self.tmux.socket_name,legacy_tmux_socket_path=None))
        root=manager.create(tool='shell',profile='general',name='managed-parent')
        child=manager.create(tool='shell',profile='general',name='managed-parent-child',parent_session_id=root['id'])
        child_pid=self.tmux.pane_pids(child['tmux_name'])
        stopped=manager.kill(root['tmux_name'])
        self.assertFalse(stopped['running'])
        self.assertEqual(stopped['status'],'process-exited')
        manager.kill(root['tmux_name'])
        self.assertTrue(manager.inspect(child['tmux_name'])['running'])
        self.assertEqual(self.tmux.pane_pids(child['tmux_name']),child_pid)
        with self.assertRaises(ValueError):
            manager.restart(root['tmux_name'])
        self.assertEqual(self.tmux.pane_pids(child['tmux_name']),child_pid)

    def test_positive_restart_scroll_rename_and_cli_attach_use_exact_session(self):
        self.wait_output('parent-child','EXACT_LAUNCH_OK')
        self.tmux.scroll_history('parent-child',-2)
        self.tmux.scroll_history('parent-child',0)
        self.tmux.restart('parent-child',self.launcher)
        self.wait_output('parent-child','EXACT_LAUNCH_OK')
        self.tmux.rename('parent-child','renamed-child')
        self.assertFalse(self.tmux.exists('parent-child'))
        self.assertTrue(self.tmux.exists('renamed-child'))
        with patch('agent_console.tmux.os.execvp') as execute:
            self.tmux.attach('renamed-child')
        self.assertEqual(execute.call_args.args[1],self.tmux.attach_command('renamed-child'))


if __name__ == '__main__':
    unittest.main()
