"""Receipt gating and real filesystem rejection; no hold/daemon/host changes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('qualification_gate', Path(__file__).parents[2]/'ops/agc-worker/qualification_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
DIGEST = 'a'*64
UID, GID = 994, 994


def namespace():
    return dict(cwd='/run/agc-job/workspace', uid=UID, gid=GID, job_root_mode='0710',
                metadata_mode='0640', private_mount=True, host_state_inaccessible=True)


def native():
    cases = []
    for mode, state in [('success','succeeded'), ('oom','oom'), ('timeout','timeout'), ('lease','unknown'), ('timer','unknown')]:
        receipt = dict(state=state, termination_confirmed=True)
        if mode == 'lease':
            receipt.update(reason='lease_lost', checkpoint_requested=True, checkpoint_sha256='b'*64)
        cases.append(dict(mode=mode, production_umask='0077', process_group_empty=True,
            duplicate_did_not_execute=True, namespace=namespace(), receipt=receipt, elapsed_seconds=84,
            atomic_lease_replacement=True, timer_stopped_without_watchdog=True,
            timer_unit_result={'Result':'success','ExecMainStatus':'0'}))
    return dict(version=1, state='passed', worker_source_sha256=DIGEST, cases=cases)


def toolchains():
    return dict(version=1, passed=True, worker_source_sha256=DIGEST, production_umask='0077',
                results=[dict(toolchain=name, passed=True, namespace=namespace()) for name in ('debian12','debian13')])


class GateTests(unittest.TestCase):
    def test_fresh_policy_seal_must_bind_the_same_worker(self):
        sample = dict(policy_verified=True, qualified_worker_sha256=DIGEST, observed_at=1000)
        gate.validate_seal(sample, DIGEST, now=1020)
        for replacement in ({'observed_at':900}, {'observed_at':1100}, {'qualified_worker_sha256':'c'*64},
                            {'qualified_worker_sha256':None}, {'policy_verified':False}):
            with self.assertRaises(ValueError): gate.validate_seal({**sample, **replacement}, DIGEST, now=1020)

    def test_current_complete_receipts_pass(self):
        gate.validate_native(native(), DIGEST, UID, GID)
        gate.validate_toolchains(toolchains(), DIGEST, UID, GID)

    def test_old_or_different_worker_receipts_fail(self):
        for value, validate in [(native(), gate.validate_native), (toolchains(), gate.validate_toolchains)]:
            for field in ('worker_source_sha256', 'version'):
                invalid = copy.deepcopy(value); invalid.pop(field)
                with self.assertRaises(ValueError): validate(invalid, DIGEST, UID, GID)
            with self.assertRaises(ValueError): validate(value, 'c'*64, UID, GID)

    def test_five_distinct_native_cases_required(self):
        for cases in (native()['cases'][:4], [native()['cases'][0]]*5):
            invalid = native(); invalid['cases'] = cases
            with self.assertRaisesRegex(ValueError, 'five_distinct'): gate.validate_native(invalid, DIGEST, UID, GID)

    def test_native_unknown_unconfirmed_and_missing_private_proof_fail(self):
        for field, value in [('production_umask','0022'), ('process_group_empty',False), ('duplicate_did_not_execute',False)]:
            invalid = native(); invalid['cases'][0][field] = value
            with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)
        invalid = native(); invalid['cases'][0]['receipt']['termination_confirmed'] = False
        with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)
        for field, value in [('private_mount',False), ('host_state_inaccessible',False), ('uid',0), ('gid',0),
                             ('cwd','/var/lib/agc-worker/jobs/x'), ('job_root_mode','0711')]:
            invalid = native(); invalid['cases'][0]['namespace'][field] = value
            with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)

    def test_real_lease_and_independent_timer_proofs_required(self):
        invalid = native(); invalid['cases'][0].pop('atomic_lease_replacement')
        with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)
        invalid = native(); invalid['cases'][3]['elapsed_seconds'] = 99
        with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)
        invalid = native(); invalid['cases'][4]['timer_unit_result']['ExecMainStatus'] = '1'
        with self.assertRaises(ValueError): gate.validate_native(invalid, DIGEST, UID, GID)

    def test_both_toolchains_need_same_private_view_and_umask(self):
        invalid = toolchains(); invalid['production_umask'] = '0022'
        with self.assertRaises(ValueError): gate.validate_toolchains(invalid, DIGEST, UID, GID)
        invalid = toolchains(); invalid['results'][1]['toolchain'] = 'debian12'
        with self.assertRaises(ValueError): gate.validate_toolchains(invalid, DIGEST, UID, GID)
        invalid = toolchains(); invalid['results'][1]['namespace'].pop('private_mount')
        with self.assertRaises(ValueError): gate.validate_toolchains(invalid, DIGEST, UID, GID)

    def test_real_filesystem_rejects_writable_ancestry_links_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); parent = root/'private'; parent.mkdir(mode=0o700)
            file = parent/'receipt.json'; file.write_text('{}'); file.chmod(0o600)
            read = lambda path=file, **kw: gate.protected_bytes(path, owner=os.getuid(), boundary=root, **kw)
            self.assertEqual(read(), b'{}')
            with self.assertRaises(ValueError): read(root)
            parent.chmod(0o770)
            with self.assertRaisesRegex(ValueError,'ancestor'): read()
            parent.chmod(0o700); file.chmod(0o660)
            with self.assertRaises(ValueError): read()
            file.chmod(0o600)
            link = parent/'link'; link.symlink_to(file)
            with self.assertRaises(OSError): read(link)
            os.link(file, parent/'hardlink')
            with self.assertRaises(ValueError): read()
            (parent/'hardlink').unlink()
            with self.assertRaises(ValueError): read(limit=1)
            parent.rename(root/'actual'); parent.symlink_to(root/'actual')
            with self.assertRaisesRegex(ValueError,'ancestor'): read()

    def test_exact_reviewed_receipt_bytes_and_fixed_directory(self):
        with patch.object(gate, 'protected_bytes', return_value=b'{"state":"passed"}'):
            digest = hashlib.sha256(b'{"state":"passed"}').hexdigest()
            self.assertEqual(gate.reviewed_json(gate.RESULTS/'native.json', digest)['state'], 'passed')
            with self.assertRaisesRegex(ValueError,'changed'):
                gate.reviewed_json(gate.RESULTS/'native.json', DIGEST)
            with self.assertRaisesRegex(ValueError,'outside'):
                gate.reviewed_json('/tmp/native.json', digest)


if __name__ == '__main__': unittest.main()
