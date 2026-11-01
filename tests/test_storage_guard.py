from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from agent_console import storage, storage_guard
from agent_console.storage import MIB, StoragePressure, level_for, minimum_free_mib, require_headroom, status


class StoragePolicyTests(unittest.TestCase):
    def test_levels_are_ordered_from_healthy_to_exhausted(self):
        self.assertEqual(level_for(20 * 1024 * MIB), 'ok')
        self.assertEqual(level_for(10 * 1024 * MIB), 'ok')
        self.assertEqual(level_for(10 * 1024 * MIB - 1), 'warn')
        self.assertEqual(level_for(4 * 1024 * MIB - 1), 'critical')
        self.assertEqual(level_for(1024 * MIB), 'critical')
        self.assertEqual(level_for(1024 * MIB - 1), 'blocked')
        self.assertEqual(level_for(0), 'blocked')

    def test_minimum_reserve_ignores_unusable_configuration(self):
        self.assertEqual(minimum_free_mib(''), storage.DEFAULT_MIN_FREE_MIB)
        self.assertEqual(minimum_free_mib('nonsense'), storage.DEFAULT_MIN_FREE_MIB)
        self.assertEqual(minimum_free_mib('0'), storage.DEFAULT_MIN_FREE_MIB)
        self.assertEqual(minimum_free_mib('-5'), storage.DEFAULT_MIN_FREE_MIB)
        self.assertEqual(minimum_free_mib(' 512 '), 512)

    def test_status_reports_headroom_without_touching_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            report = status(directory, minimum=1)
            self.assertTrue(report['headroom_ok'])
            self.assertEqual(report['minimum_free_bytes'], MIB)
            self.assertIn(report['level'], {'ok', 'warn', 'critical'})
            require_headroom(directory, minimum=1)

    def test_launch_is_refused_below_the_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(StoragePressure) as caught:
                require_headroom(directory, minimum=1024 * 1024 * 1024)
            self.assertEqual(caught.exception.status, 503)
            self.assertIsInstance(caught.exception, RuntimeError)
            self.assertIn('No session state was created', str(caught.exception))
            # Refusal happens before any launch state exists.
            self.assertEqual(list(Path(directory).iterdir()), [])


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * storage_guard.DAY
    os.utime(path, (stamp, stamp))


class GuardPlanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.now = time.time()

    def test_obsolete_releases_are_selected_and_current_kept(self):
        releases = self.root / 'releases'
        releases.mkdir()
        names = [f'release-2026010{day}-000000-abcdef' for day in range(1, 7)]
        for name in names:
            (releases / name).mkdir()
            _age(releases / name, 2)
        (releases / 'current').symlink_to(releases / names[-1])
        (releases / 'canary').symlink_to(releases / names[-2])
        actions = storage_guard.plan_releases(self.root, self.now, set())
        removed = {action.path.name for action in actions}
        self.assertNotIn(names[-1], removed)      # current
        self.assertNotIn(names[-2], removed)      # canary
        self.assertEqual(len(actions), len(names) - storage_guard.RELEASES_KEEP)
        self.assertTrue(all(action.tier == 1 for action in actions))

    def test_recent_releases_are_never_reclaimed(self):
        releases = self.root / 'releases'
        releases.mkdir()
        for day in range(1, 6):
            target = releases / f'release-2026010{day}-000000-abcdef'
            target.mkdir()
        self.assertEqual(storage_guard.plan_releases(self.root, self.now, set()), [])

    def test_contexts_for_live_sessions_are_protected(self):
        contexts = self.root / 'contexts'
        contexts.mkdir()
        live = contexts / 'agc-closeout-pi'
        stale = contexts / 'old-session-pi'
        fresh = contexts / 'recent-pi'
        for path in (live, stale, fresh):
            path.mkdir()
            _age(path, 30)
        _age(fresh, 1)
        actions = storage_guard.plan_contexts(self.root, self.now, {'agc-closeout'})
        selected = {action.path.name for action in actions}
        self.assertEqual(selected, {'old-session-pi'})

    def test_superseded_assessments_are_pruned_but_latest_two_kept(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        names = [f'2026010{day}T000000000000Z' for day in range(1, 6)]
        for name in names:
            (root / name).mkdir()
            (root / name / 'report.json').write_bytes(b'x' * 1024)
            _age(root / name, 3)
        (root / 'latest.json').write_text(json.dumps({'snapshot': names[0]}), encoding='utf-8')
        actions = storage_guard.plan_assessments(self.root, self.now)
        selected = {action.path.name for action in actions}
        # Newest two stay, and so does the snapshot latest.json still names.
        self.assertEqual(selected, {names[1], names[2]})
        self.assertEqual(actions[0].tier, 1)

    def test_assessment_being_written_is_left_alone(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        for day in range(1, 5):
            target = root / f'2026010{day}T000000000000Z'
            target.mkdir()
            _age(target, 3)
        fresh = root / '20260105T000000000000Z'
        fresh.mkdir()          # just created, may still be written
        actions = storage_guard.plan_assessments(self.root, self.now)
        self.assertNotIn(fresh.name, {action.path.name for action in actions})

    def test_provider_history_is_only_reclaimed_with_tier_two(self):
        overlays = self.root / 'tool-overlays'
        provider = self.root / 'provider-state'
        overlays.mkdir()
        provider.mkdir()
        (overlays / 'dead-session').mkdir()
        (provider / 'sess-dead').mkdir()
        (overlays / 'live-session').mkdir()
        for path in list(overlays.iterdir()) + list(provider.iterdir()):
            _age(path, 30)
        live_names, live_ids = {'live-session'}, {'sess-live'}
        self.assertEqual(storage_guard.plan(self.root, now=self.now,
                                            live_names=live_names, live_ids=live_ids,
                                            tmp_dir=None, tier2=False), [])
        actions = storage_guard.plan(self.root, now=self.now, live_names=live_names,
                                     live_ids=live_ids, tmp_dir=None, tier2=True)
        selected = {action.path.name for action in actions}
        self.assertEqual(selected, {'dead-session', 'sess-dead'})
        self.assertTrue(all(action.tier == 2 for action in actions))

    def test_temporary_entries_skip_mounts_and_live_sessions(self):
        tmp = self.root / 'tmp'
        tmp.mkdir()
        for name in ('agc-build-cache', '.X11-unix', 'tmux-1000', 'live-session-tmp'):
            (tmp / name).mkdir()
            _age(tmp / name, 10)
        actions = storage_guard.plan_tmp(tmp, self.now, {'live-session'})
        self.assertEqual([action.path.name for action in actions], ['agc-build-cache'])

    def test_live_sessions_are_read_from_the_database(self):
        db_path = self.root / 'agent-console.sqlite3'
        with sqlite3.connect(db_path) as conn:
            conn.execute('CREATE TABLE sessions (id TEXT, tmux_name TEXT, status TEXT)')
            conn.executemany('INSERT INTO sessions VALUES (?,?,?)',
                             [('sess-live', 'live-session', 'detached'),
                              ('sess-dead', 'dead-session', 'process-exited')])
        names, ids = storage_guard.live_sessions(db_path)
        self.assertEqual(names, {'live-session'})
        self.assertEqual(ids, {'sess-live'})

    def test_run_applies_only_when_not_a_check(self):
        releases = self.root / 'releases'
        releases.mkdir()
        for day in range(1, 6):
            target = releases / f'release-2026010{day}-000000-abcdef'
            target.mkdir()
            (target / 'payload.bin').write_bytes(b'x' * 2048)
            _age(target, 2)
        original_free = storage.free_bytes
        storage.free_bytes = lambda path: 5 * MIB     # below 'warn' -> 'critical'
        try:
            dry = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                    now=self.now, apply_actions=False)
            self.assertEqual(dry.removed, 0)
            self.assertTrue(dry.actions)
            self.assertEqual(len(list(releases.iterdir())), 5)
            report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                       now=self.now, apply_actions=True)
            self.assertEqual(report.removed, len(dry.actions))
            self.assertLess(len(list(releases.iterdir())), 5)
        finally:
            storage.free_bytes = original_free

    def test_retention_also_runs_while_healthy(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        for day in range(1, 5):
            target = root / f'2026010{day}T000000000000Z'
            target.mkdir()
            _age(target, 3)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                   now=self.now, apply_actions=True)
        self.assertEqual(report.level, 'ok')
        self.assertEqual(report.removed, 2)      # keeps the newest two
        self.assertEqual(len([p for p in root.iterdir() if p.is_dir()]), 2)

    def test_status_file_is_private_and_parseable(self):
        report = storage_guard.Report(level='warn', free_bytes=5 * 1024 * MIB)
        path = storage_guard.write_status(self.root, report)
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(payload['level'], 'warn')
        self.assertEqual(payload['free_gib'], 5.0)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
