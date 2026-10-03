"""Failed terminal observation must not turn live sessions into stopped records."""
import subprocess
import unittest
from unittest.mock import patch
from agent_console.tmux import Tmux

class TmuxObservationTests(unittest.TestCase):
    def test_only_positive_absence_is_an_empty_inventory(self):
        tmux=Tmux(socket_name='observation-test')
        for detail in ['no server running on /tmp/test', 'error connecting to /tmp/test (No such file or directory)']:
            with self.subTest(detail=detail), patch.object(tmux,'run',return_value=subprocess.CompletedProcess([],1,'',detail)):
                self.assertEqual(tmux.list_sessions(),{})
        for detail in ['error connecting to /tmp/test (Permission denied)', 'server exited unexpectedly', 'error connecting to /tmp/test (Connection refused)', '']:
            with self.subTest(detail=detail), patch.object(tmux,'run',return_value=subprocess.CompletedProcess([],1,'',detail)):
                with self.assertRaisesRegex(RuntimeError,'list-sessions failed'):
                    tmux.list_sessions()

    def test_observation_and_rename_have_bounded_timeouts(self):
        tmux=Tmux(socket_name='observation-test')
        for call in [tmux.list_sessions, lambda:tmux.rename('old','new')]:
            with self.subTest(call=call), patch('agent_console.tmux.subprocess.run',side_effect=subprocess.TimeoutExpired('tmux',2)) as run:
                with self.assertRaisesRegex(RuntimeError,'timed out'):
                    call()
                self.assertEqual(run.call_args.kwargs['timeout'],2)
