from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from agent_console import storage_guard, storage_proposal as sp


def _age_tree(path: Path, days: float) -> None:
    stamp = time.time() - days * storage_guard.DAY
    for current, dirs, files in os.walk(path):
        for name in files:
            os.utime(Path(current) / name, (stamp, stamp))
        for name in dirs:
            os.utime(Path(current) / name, (stamp, stamp))
    os.utime(path, (stamp, stamp))


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.now = time.time()
        releases = self.root / 'releases'
        releases.mkdir()
        self.names = [f'release-2026010{day}-000000-abcdef' for day in range(1, 6)]
        for name in self.names:
            (releases / name).mkdir()
            (releases / name / 'payload').write_bytes(b'x' * 1024)
            _age_tree(releases / name, 2)
        (releases / 'current').symlink_to(releases / self.names[-1])

    def _proposal(self, **kwargs):
        return sp.build(self.root, tmp_dir=None, now=self.now, **kwargs)

    def test_proposal_is_deterministic_and_round_trips(self):
        first = self._proposal()
        second = self._proposal()
        self.assertEqual(first['digest'], second['digest'])
        self.assertEqual(len(first['actions']), 2)     # 5 releases, keep newest 3
        path = sp.write(self.root, first)
        loaded = sp.load(path)
        self.assertEqual(loaded['digest'], first['digest'])
        self.assertEqual(set(loaded['actions'][0]), {'path', 'tier', 'kind', 'reason', 'bytes'})
        sp.verify(self.root, loaded, now=self.now)

    def test_edited_proposal_is_rejected(self):
        proposal = self._proposal()
        path = sp.write(self.root, proposal)
        payload = json.loads(path.read_text())
        payload['actions'][0]['bytes'] += 1
        path.write_text(json.dumps(payload))
        with self.assertRaises(sp.ProposalInvalid):
            sp.load(path)

    def test_expired_proposal_is_rejected(self):
        proposal = self._proposal()
        sp.write(self.root, proposal)
        later = self.now + sp.PROPOSAL_TTL_SECONDS + 1
        with self.assertRaises(sp.ProposalExpired):
            sp.verify(self.root, proposal, now=later)

    def test_drift_withdraws_approval(self):
        proposal = self._proposal()
        sp.write(self.root, proposal)
        extra = self.root / 'releases' / 'release-20250101-000000-ancient'
        extra.mkdir()
        (extra / 'payload').write_bytes(b'x')
        _age_tree(extra, 30)
        with self.assertRaises(sp.ProposalDrift):
            sp.verify(self.root, proposal, now=self.now)

    def test_policy_change_invalidates_old_proposals(self):
        proposal = self._proposal()
        sp.write(self.root, proposal)
        original = storage_guard.RELEASES_KEEP
        try:
            storage_guard.RELEASES_KEEP = original + 1
            with self.assertRaises(sp.ProposalInvalid):
                sp.verify(self.root, proposal, now=self.now)
        finally:
            storage_guard.RELEASES_KEEP = original

    def test_execute_applies_exactly_the_approved_paths_once(self):
        proposal = self._proposal()
        sp.write(self.root, proposal)
        approved = {action['path'] for action in proposal['actions']}
        # Something that becomes eligible after approval must not be taken.
        late = self.root / 'releases' / 'release-20250102-000000-later'
        late.mkdir()
        (late / 'payload').write_bytes(b'x')
        _age_tree(late, 30)
        with self.assertRaises(sp.ProposalDrift):
            sp.execute(self.root, proposal['digest'])
        self.assertTrue(late.exists())
        # Re-approve the wider plan, then run it once.
        wider = self._proposal()
        sp.write(self.root, wider)
        report = sp.execute(self.root, wider['digest'])
        self.assertEqual(report.removed, len(wider['actions']))
        for path in approved:
            self.assertFalse(Path(path).exists())
        with self.assertRaises(sp.ProposalReplayed):
            sp.execute(self.root, wider['digest'])

    def test_foreign_state_root_is_refused(self):
        proposal = self._proposal()
        sp.write(self.root, proposal)
        other = Path(tempfile.mkdtemp())
        with self.assertRaises(sp.ProposalInvalid):
            sp.verify(other, proposal, now=self.now)

    def test_unknown_digest_is_refused(self):
        with self.assertRaises(sp.ProposalInvalid):
            sp.resolve(self.root, 'f' * 64)


if __name__ == '__main__':
    unittest.main()
