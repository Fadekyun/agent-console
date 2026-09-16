from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock

from agent_console.manager import SessionManager
from agent_console.tmux import Tmux, TmuxObservationError


class TmuxObservationTests(unittest.TestCase):
    def test_no_server_is_authoritative_empty(self) -> None:
        tmux = Tmux("unit-test")
        tmux.run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no server running on /tmp/tmux-1000/unit-test\n"
        ))
        self.assertEqual(tmux.list_sessions(), {})

    def test_command_failure_is_not_empty(self) -> None:
        tmux = Tmux("unit-test")
        tmux.run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied\n"
        ))
        with self.assertRaisesRegex(TmuxObservationError, "observation unavailable"):
            tmux.list_sessions()

    def test_malformed_output_is_not_silently_skipped(self) -> None:
        tmux = Tmux("unit-test")
        tmux.run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="broken-row\n", stderr=""
        ))
        with self.assertRaisesRegex(TmuxObservationError, "malformed list-sessions"):
            tmux.list_sessions()

    def test_malformed_numeric_metadata_is_not_silently_accepted(self) -> None:
        tmux = Tmux("unit-test")
        tmux.run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="session\tnot-a-time\t2\t0\t1\tbash\n", stderr=""
        ))
        with self.assertRaisesRegex(TmuxObservationError, "malformed numeric"):
            tmux.list_sessions()

    def test_reconcile_does_not_touch_database_when_observation_fails(self) -> None:
        manager = object.__new__(SessionManager)
        manager.database = MagicMock()
        manager._live_sessions = MagicMock(side_effect=TmuxObservationError("temporary failure"))

        with self.assertRaises(TmuxObservationError):
            manager.reconcile()

        manager.database.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
