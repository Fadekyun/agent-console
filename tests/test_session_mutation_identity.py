"""Mutations are pinned to the UI's durable identity, using private tmux fixtures."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

from agent_console.manager import SessionIdentityConflict, SessionManager
from agent_console.session_control import SessionControl
from test_web import WebTests as _Fixture


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class SessionMutationIdentityTests(unittest.TestCase):
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown

    def create(self, name):
        return self.manager.create(tool='shell', profile='general', name=name,
                                   repository=str(self.workspace))

    def call(self, operation, name, identity=None, **extra):
        payload = dict(extra)
        if identity is not None:
            payload['session_id'] = identity
        if operation == 'kill':
            payload.setdefault('confirmed', True)
        if operation == 'attention':
            payload.setdefault('state', 'ready_for_review')
        method = self.client.patch if operation == 'attention' else self.client.post
        return method(f'/api/sessions/{name}/{operation}', json=payload, headers=self.headers)

    def test_stale_requests_after_rename_and_reuse_leave_both_sessions_untouched(self):
        original = self.create('original-name')
        moved = self.manager.rename(original['tmux_name'], 'renamed-original')
        replacement = self.create(original['tmux_name'])
        launcher = Path(replacement['launcher_path'])
        before = launcher.read_bytes()
        with patch.object(self.manager.tmux, 'kill') as kill, \
             patch.object(self.manager.tmux, 'interrupt') as interrupt, \
             patch.object(self.manager.tmux, 'restart') as restart:
            for operation in ('kill', 'interrupt', 'restart', 'attention'):
                with self.subTest(operation=operation):
                    response = self.call(operation, original['tmux_name'], original['id'])
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn('identity changed', response.json()['detail'])
            kill.assert_not_called()
            interrupt.assert_not_called()
            restart.assert_not_called()
        self.assertEqual(launcher.read_bytes(), before)
        for session in (moved, replacement):
            observed = self.manager.inspect(session['tmux_name'])
            self.assertTrue(observed['running'])
            self.assertEqual(observed['attention_state'], 'normal')
        self.assertEqual(self.call('attention', moved['tmux_name'], original['id']).status_code, 200)
        self.assertEqual(self.manager.inspect(replacement['tmux_name'])['attention_state'], 'normal')

    def test_matched_and_legacy_requests_still_work(self):
        for pinned in (True, False):
            session = self.create('matched' if pinned else 'legacy')
            identity = session['id'] if pinned else None
            for operation in ('interrupt', 'restart', 'attention', 'kill'):
                with self.subTest(pinned=pinned, operation=operation):
                    response = self.call(operation, session['tmux_name'], identity)
                    self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(self.manager.inspect(session['tmux_name'])['running'])

    def test_empty_body_legacy_interrupt_and_restart_remain_supported(self):
        session = self.create('no-body')
        for operation in ('interrupt', 'restart'):
            response = self.client.post(f"/api/sessions/{session['tmux_name']}/{operation}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)

    def test_confirmation_and_unmanaged_guards_remain_in_force(self):
        self.manager.tmux.run('new-session', '-d', '-s', 'unmanaged', 'sleep', '60')
        session = self.manager.inspect('unmanaged')
        for extra in ({'confirmed': False}, {}, {'allow_unmanaged': True}, {'understand_unmanaged': True}):
            with self.subTest(extra=extra):
                self.assertEqual(self.call('kill', 'unmanaged', session['id'], **extra).status_code, 400)
                self.assertTrue(self.manager.inspect('unmanaged')['running'])
        self.assertEqual(self.call('kill', 'unmanaged', session['id'], allow_unmanaged=True,
                                   understand_unmanaged=True).status_code, 200)

    def test_missing_pinned_identity_fails_before_tmux_or_metadata_mutation(self):
        with patch.object(self.manager.tmux, 'interrupt') as interrupt:
            with self.assertRaises(SessionIdentityConflict):
                self.manager.interrupt('absent', session_id='absent-id')
            interrupt.assert_not_called()

    def test_same_tree_control_snapshot_cannot_mutate_reused_name(self):
        caller = self.create('caller')
        target = self.create('target')
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET parent_session_id=?,profile='reviewer',agent_mode='plan' WHERE id=?",
                       (target['id'],caller['id']))
        control = SessionControl(self.manager, {'id':caller['id']})
        self.manager.rename(target['tmux_name'], 'target-renamed')
        replacement = self.create(target['tmux_name'])
        with patch.object(self.manager.tmux, 'kill') as kill, \
             patch.object(self.manager.tmux, 'interrupt') as interrupt, \
             patch.object(self.manager.tmux, 'restart') as restart:
            for command in ('attention','interrupt','restart-agent','kill'):
                with self.subTest(command=command), self.assertRaises(SessionIdentityConflict):
                    control.run(command, {'name':target['id'],'state':'ready_for_review'})
            kill.assert_not_called()
            interrupt.assert_not_called()
            restart.assert_not_called()
        self.assertEqual(self.manager.inspect(replacement['tmux_name'])['attention_state'],'normal')
        self.assertTrue(self.manager.inspect(replacement['tmux_name'])['running'])

    def test_rename_waits_for_validated_mutation_and_reuse_stays_protected(self):
        original = self.create('race-original')
        peer = SessionManager(self.manager.settings)
        entered, release, rename_entered = threading.Event(), threading.Event(), threading.Event()
        def paused_interrupt(name):
            self.assertEqual(name, original['tmux_name'])
            entered.set()
            if not release.wait(5):
                raise AssertionError('test did not release interrupt')
        def rename():
            rename_entered.set()
            return peer.rename(original['tmux_name'], 'race-moved')
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.manager.tmux, 'interrupt', side_effect=paused_interrupt):
            mutation = pool.submit(self.manager.interrupt, original['tmux_name'], session_id=original['id'])
            try:
                self.assertTrue(entered.wait(5))
                # Prove that validation and the eventual tmux operation share
                # the cross-process lock, rather than relying on scheduling.
                with (self.manager.settings.state_dir/'admission.lock').open('a+b') as handle:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                renaming = pool.submit(rename)
                self.assertTrue(rename_entered.wait(5))
                self.assertFalse(renaming.done())
                self.assertEqual(peer.inspect(original['tmux_name'])['id'], original['id'])
            finally:
                release.set()
            self.assertEqual(mutation.result(timeout=5)['id'], original['id'])
            self.assertEqual(renaming.result(timeout=5)['id'], original['id'])
        replacement = self.create(original['tmux_name'])
        with self.assertRaises(SessionIdentityConflict):
            self.manager.kill(original['tmux_name'], session_id=original['id'])
        self.assertTrue(self.manager.inspect(replacement['tmux_name'])['running'])
