"""Restart admission matches creation readiness without touching live auth."""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_shared_skill_discovery


class RestartAuthReadinessTests(unittest.TestCase):
    setUp = test_shared_skill_discovery.SharedSkillSessionTests.setUp
    tearDown = test_shared_skill_discovery.SharedSkillSessionTests.tearDown
    configure_commandcode = test_shared_skill_discovery.SharedSkillSessionTests.configure_commandcode

    def create(self, tool='codex'):
        return self.manager.create(tool=tool, profile='general', name='auth-restart-fixture', repository=str(self.workspace))

    def assert_rejected_without_mutation(self, session, status):
        paths = [Path(session['launcher_path']),
                 self.settings.state_dir/'environment-launches'/f"{session['id']}.json",
                 self.settings.state_dir/'skills-isolated'/session['tmux_name']/'typesafe-ai/SKILL.md']
        before = {path:path.read_bytes() for path in paths}
        with patch.object(self.manager.tmux, 'restart') as restart:
            with self.assertRaisesRegex(RuntimeError, status):
                self.manager.restart(session['tmux_name'])
            restart.assert_not_called()
        self.assertEqual(before, {path:path.read_bytes() for path in paths})
        self.assertTrue(self.manager.inspect(session['tmux_name'])['running'])

    def test_disabled_context_rejects_restart_before_mutation(self):
        session = self.create()
        self.manager.auth.disable('codex', 'default', reason='fixture disabled')
        self.assert_rejected_without_mutation(session, 'disabled')

    def test_missing_auth_rejects_restart_before_mutation(self):
        session = self.create()
        (self.manager.auth.codex_home('default')/'auth.json').unlink()
        self.assert_rejected_without_mutation(session, 'setup-required')

    def test_unsafe_secret_permissions_reject_restart_before_mutation(self):
        self.configure_commandcode('pi')
        session = self.create('pi')
        self.manager.auth.secret_path('commandcode-main').chmod(0o644)
        self.assert_rejected_without_mutation(session, 'error')

    def test_ready_context_still_restarts(self):
        session = self.create()
        with patch.object(self.manager.tmux, 'restart', wraps=self.manager.tmux.restart) as restart:
            result = self.manager.restart(session['tmux_name'])
            restart.assert_called_once()
        self.assertTrue(result['running'])
        self.assertEqual(result['id'], session['id'])
