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


def _age(path: Path, days: float) -> None:
    """Backdate a single path (the guard reads the newest file, so trees need _age_tree)."""
    stamp = time.time() - days * storage_guard.DAY
    os.utime(path, (stamp, stamp))


def _age_tree(path: Path, days: float) -> None:
    """Backdate a whole tree; the guard reads the newest file, not the directory."""
    for current, dirs, files in os.walk(path):
        for name in files:
            _age(Path(current) / name, days)
        for name in dirs:
            _age(Path(current) / name, days)
    _age(path, days)


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

    def test_reserve_matches_host_admission(self):
        # The deployed LXC admission vetoes launches below 4 GiB; the portable
        # preflight must not be laxer than the gate it shadows.
        self.assertEqual(storage.DEFAULT_MIN_FREE_MIB, 4096)

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
            self.assertEqual(list(Path(directory).iterdir()), [])


class GuardPlanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.now = time.time()

    # --- releases -------------------------------------------------------

    def test_obsolete_releases_are_selected_and_pointers_kept(self):
        releases = self.root / 'releases'
        releases.mkdir()
        names = [f'release-2026010{day}-000000-abcdef' for day in range(1, 7)]
        for name in names:
            (releases / name).mkdir()
            (releases / name / 'file').write_bytes(b'x')
            _age_tree(releases / name, 2)
        (releases / 'current').symlink_to(releases / names[-1])
        (releases / 'canary').symlink_to(releases / names[-2])
        actions = storage_guard.plan_releases(self.root, self.now, set())
        removed = {action.path.name for action in actions}
        self.assertNotIn(names[-1], removed)
        self.assertNotIn(names[-2], removed)
        self.assertEqual(len(actions), len(names) - storage_guard.RELEASES_KEEP)
        self.assertTrue(all(action.tier == 1 for action in actions))

    def test_recent_releases_are_never_reclaimed(self):
        releases = self.root / 'releases'
        releases.mkdir()
        for day in range(1, 6):
            (releases / f'release-2026010{day}-000000-abcdef').mkdir()
        self.assertEqual(storage_guard.plan_releases(self.root, self.now, set()), [])

    def test_release_that_became_current_is_skipped_at_apply_time(self):
        releases = self.root / 'releases'
        releases.mkdir()
        candidate = releases / 'release-20260101-000000-old'
        candidate.mkdir()
        (candidate / 'file').write_bytes(b'x')
        _age_tree(candidate, 30)
        new = releases / 'release-20260102-000000-new'
        new.mkdir()
        (releases / 'current').symlink_to(new)
        actions = [storage_guard.Action(candidate, 1, 'obsolete release', 1, 'release')]
        report = storage_guard.Report(level='ok', free_bytes=0)
        # A deploy flips the pointer between plan and apply.
        (releases / 'current').unlink()
        (releases / 'current').symlink_to(candidate)
        storage_guard.apply(actions, report, state_dir=self.root)
        self.assertTrue(candidate.exists())
        self.assertEqual(report.removed, 0)
        self.assertTrue(report.skipped)

    # --- activity window -------------------------------------------------

    def test_recent_write_inside_an_old_tree_protects_it(self):
        overlays = self.root / 'tool-overlays'
        overlays.mkdir()
        home = overlays / 'stale-session'
        (home / 'codex-home').mkdir(parents=True)
        (home / 'codex-home' / 'history.jsonl').write_bytes(b'x' * 64)
        _age_tree(home, 60)                # everything inside looks ancient
        actions = storage_guard.plan_provider_state(
            self.root, self.now, set(), set())
        self.assertEqual([a.path.name for a in actions], ['stale-session'])
        # Touching a descendant file must withdraw eligibility, even though the
        # directory mtime stays old.
        fresh = home / 'codex-home' / 'live.jsonl'
        fresh.write_bytes(b'y')
        _age(home, 60)                     # directory only; the new file stays fresh
        self.assertEqual(storage_guard.plan_provider_state(self.root, self.now, set(), set()), [])

    # --- containment -----------------------------------------------------

    def test_symlinked_candidate_is_skipped(self):
        overlays = self.root / 'tool-overlays'
        overlays.mkdir()
        outside = Path(tempfile.mkdtemp())
        (outside / 'precious').write_text('keep me')
        (overlays / 'linked').symlink_to(outside)
        actions = storage_guard.plan_provider_state(self.root, self.now, set(), set())
        self.assertEqual(actions, [])
        self.assertTrue((outside / 'precious').exists())

    def test_candidate_outside_the_root_is_not_deletable(self):
        report = storage_guard.Report(level='ok', free_bytes=0)
        outside = Path(tempfile.mkdtemp())
        (outside / 'keep').write_text('x')
        storage_guard.apply([storage_guard.Action(outside, 1, 'x', 1)], report,
                            state_dir=self.root)
        self.assertEqual(report.removed, 0)
        self.assertTrue(report.skipped)

    # --- contexts --------------------------------------------------------

    def test_contexts_prune_directories_and_keep_instruction_files(self):
        contexts = self.root / 'contexts'
        contexts.mkdir()
        (contexts / 'live-session.md').write_text('ctx')
        (contexts / 'dead-session.md').write_text('ctx')
        (contexts / 'other-session-pi').mkdir()
        (contexts / 'other-session-pi' / 'skills').mkdir()
        for target in contexts.iterdir():
            _age(target, 30)
            if target.is_dir():
                _age_tree(target, 30)
        actions = storage_guard.plan_contexts(self.root, self.now, {'live-session'})
        selected = {action.path.name for action in actions}
        # Instruction files are kept even when stale; only the heavy mirrors go.
        self.assertEqual(selected, {'other-session-pi'})

    # --- assessments -----------------------------------------------------

    def test_superseded_assessments_are_pruned_but_latest_two_kept(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        names = [f'2026010{day}T000000000000Z' for day in range(1, 6)]
        for name in names:
            (root / name).mkdir()
            (root / name / 'report.json').write_bytes(b'x' * 1024)
            _age_tree(root / name, 3)
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
            _age_tree(target, 3)
        fresh = root / '20260105T000000000000Z'
        fresh.mkdir()
        actions = storage_guard.plan_assessments(self.root, self.now)
        self.assertNotIn(fresh.name, {action.path.name for action in actions})

    def test_unreadable_latest_reference_keeps_every_snapshot(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        for day in range(1, 5):
            target = root / f'2026010{day}T000000000000Z'
            target.mkdir()
            _age_tree(target, 3)
        (root / 'latest.json').write_text('{not json', encoding='utf-8')
        self.assertEqual(storage_guard.plan_assessments(self.root, self.now), [])

    # --- /tmp ------------------------------------------------------------

    def test_temporary_cleanup_is_limited_to_managed_names(self):
        tmp = self.root / 'tmp'
        tmp.mkdir()
        for name in ('agc-build-cache', 'someone-elses-data', '.X11-unix',
                     'tmux-1000', 'live-session-scratch'):
            (tmp / name).mkdir()
            _age(tmp / name, 10)
        actions = storage_guard.plan_tmp(tmp, self.now, {'live-session'})
        self.assertEqual([action.path.name for action in actions], ['agc-build-cache'])

    # --- live state ------------------------------------------------------

    def test_live_sessions_are_read_from_the_database(self):
        db_path = self.root / 'agent-console.sqlite3'
        with sqlite3.connect(db_path) as conn:
            conn.execute('CREATE TABLE sessions (id TEXT, tmux_name TEXT, status TEXT)')
            conn.executemany('INSERT INTO sessions VALUES (?,?,?)',
                             [('sess-live', 'live-session', 'detached'),
                              ('sess-dead', 'dead-session', 'process-exited')])
        live = storage_guard.live_sessions(db_path)
        self.assertEqual(live.names, {'live-session'})
        self.assertEqual(live.ids, {'sess-live'})

    def test_unreadable_database_fails_closed(self):
        db_path = self.root / 'agent-console.sqlite3'
        db_path.write_bytes(b'not a database')
        with self.assertRaises(storage_guard.LiveStateUnavailable):
            storage_guard.live_sessions(db_path)

    def test_run_skips_session_dependent_cleanup_when_state_is_unreadable(self):
        (self.root / 'agent-console.sqlite3').write_bytes(b'not a database')
        contexts = self.root / 'contexts'
        contexts.mkdir()
        (contexts / 'any.md').write_text('ctx')
        _age(contexts / 'any.md', 60)
        logs = self.root / 'logs'
        logs.mkdir()
        (logs / 'old.log').write_text('x')
        _age(logs / 'old.log', 90)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                   now=self.now, apply_actions=True)
        self.assertTrue(any('unreadable' in error for error in report.errors))
        self.assertTrue((contexts / 'any.md').exists())      # fail closed
        self.assertFalse((logs / 'old.log').exists())        # independent rule still runs

    def test_tier_two_is_gated_on_pressure(self):
        overlays = self.root / 'tool-overlays'
        overlays.mkdir()
        home = overlays / 'old-session'
        (home / 'codex-home').mkdir(parents=True)
        (home / 'codex-home' / 'h.jsonl').write_bytes(b'x')
        _age_tree(home, 60)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=True,
                                   now=self.now, apply_actions=False)
        self.assertEqual(report.level, 'ok')       # the test filesystem is healthy
        self.assertEqual(report.actions, [])
        self.assertTrue(home.exists())

    def test_tier_two_reports_available_bytes_when_disabled(self):
        overlays = self.root / 'tool-overlays'
        overlays.mkdir()
        home = overlays / 'old-session'
        (home / 'codex-home').mkdir(parents=True)
        (home / 'codex-home' / 'h.jsonl').write_bytes(b'x' * 4096)
        _age_tree(home, 60)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                   now=self.now, apply_actions=False)
        self.assertGreater(report.tier2_available_bytes, 0)

    # --- run / apply -----------------------------------------------------

    def test_run_applies_only_when_not_a_check(self):
        releases = self.root / 'releases'
        releases.mkdir()
        for day in range(1, 6):
            target = releases / f'release-2026010{day}-000000-abcdef'
            target.mkdir()
            (target / 'payload.bin').write_bytes(b'x' * 2048)
            _age_tree(target, 2)
        dry = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                now=self.now, apply_actions=False)
        self.assertEqual(dry.removed, 0)
        self.assertTrue(dry.actions)
        self.assertEqual(len(list(releases.iterdir())), 5)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                   now=self.now, apply_actions=True)
        self.assertEqual(report.removed, len(dry.actions))
        self.assertLess(len(list(releases.iterdir())), 5)

    def test_retention_also_runs_while_healthy(self):
        root = self.root / 'harness-assessments'
        root.mkdir()
        for day in range(1, 5):
            target = root / f'2026010{day}T000000000000Z'
            target.mkdir()
            (target / 'report.json').write_bytes(b'x')
            _age_tree(target, 3)
        report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                   now=self.now, apply_actions=True)
        self.assertEqual(report.level, 'ok')
        self.assertEqual(report.removed, 2)
        self.assertEqual(len([p for p in root.iterdir() if p.is_dir()]), 2)

    def test_tmp_actions_are_contained_by_the_tmp_root(self):
        tmp = self.root / 'tmp'
        tmp.mkdir()
        (tmp / 'agc-old-build').mkdir()
        (tmp / 'agc-old-build' / 'f').write_bytes(b'x' * 32)
        _age_tree(tmp / 'agc-old-build', 10)
        report = storage_guard.run(self.root, tmp_dir=tmp, tier2=False,
                                   now=self.now, apply_actions=True)
        kinds = [action.kind for action in report.actions]
        self.assertIn('tmp', kinds)
        self.assertFalse((tmp / 'agc-old-build').exists())
        self.assertEqual(report.skipped, [])

    def test_concurrent_run_is_refused(self):
        import fcntl
        lock_path = self.root / 'storage-guard.lock'
        handle = open(lock_path, 'a+')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = storage_guard.run(self.root, tmp_dir=None, tier2=False,
                                       now=self.now, apply_actions=True)
            self.assertTrue(any('lock' in error for error in report.errors))
            self.assertEqual(report.actions, [])
        finally:
            handle.close()

    def test_status_file_is_private_and_leaves_no_temporary(self):
        report = storage_guard.Report(level='warn', free_bytes=5 * 1024 * MIB)
        path = storage_guard.write_status(self.root, report)
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(payload['level'], 'warn')
        self.assertEqual(payload['free_gib'], 5.0)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith('.')], [])


if __name__ == '__main__':
    unittest.main()
