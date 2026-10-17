"""Policy boundaries only; no CT102 service, mounts, SSH or jobs are touched."""
import importlib.util
from pathlib import Path
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bushi_emergency_policy', Path(__file__).parents[2] / 'ops/agc-worker/emergency_worker.py')
emergency = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(emergency)


def sample():
    return {'observed_at': 1000, 'physical_host': 'n100', 'memory_total_bytes': 16 * 1024**3,
            'memory_available_bytes': 6 * 1024**3, 'cpu_count': 4, 'ct102_memory_limit_bytes': 2 * 1024**3,
            'ct102_memory_current_bytes':128*1024**2, 'ct102_cgroup_path':'/lxc/102',
            'ct102_namespace_root':'/lxc/102/ns',
            'memory_full_avg60': 0, 'io_some_avg60': 0, 'thinpool_data_percent': 30, 'thinpool_metadata_percent': 10}


class EmergencyPolicyTests(unittest.TestCase):
    def test_sample_rejects_wrong_host_missing_fields_stale_and_nonfinite(self):
        self.assertEqual(emergency.validate_sample(sample(), now=995), sample())
        for update in [{'physical_host': 'agc-laptop'}, {'observed_at': 969}, {'observed_at': 1006},
                       {'memory_available_bytes': float('nan')}, {'cpu_count': True},
                       {'ct102_memory_limit_bytes': 3 * 1024**3},
                       {'ct102_memory_current_bytes': True}, {'ct102_memory_current_bytes': -1},
                       {'ct102_namespace_root':'/lxc/115/ns'}]:
            with self.assertRaises(ValueError): emergency.validate_sample({**sample(), **update}, now=1000)
        with self.assertRaises(ValueError): emergency.validate_sample({**sample(), 'extra': 1}, now=1000)

    @patch.object(emergency, 'local_guest', return_value={'guest_memory_limit_bytes': 2 * 1024**3, 'guest_available_bytes': 2 * 1024**3, 'guest_disk_free_bytes': 10 * 1024**3, 'scratch_available_bytes': 512 * 1024**2})
    def test_start_and_runtime_headroom_are_independently_enforced(self, _):
        host = {'n100_sample': sample(), 'policy_verified': True}
        self.assertEqual(emergency.emergency_admission(host, now=1000, required_memory=1024**3), [])
        host['n100_sample']['memory_available_bytes'] = 3 * 1024**3
        self.assertIn('n100_memory_reserve', emergency.emergency_admission(host, now=1000, required_memory=1024**3))
        self.assertEqual(emergency.emergency_admission(host, now=1000), [])
        host['n100_sample']['memory_available_bytes'] = 1024**3
        self.assertIn('n100_memory_reserve', emergency.emergency_admission(host, now=1000))
        for key, value in [('memory_full_avg60', 1), ('io_some_avg60', 10), ('thinpool_data_percent', 80), ('thinpool_metadata_percent', 70)]:
            host['n100_sample'] = {**sample(), key: value}
            self.assertIn('n100_' + key, emergency.emergency_admission(host, now=1000))

    @patch.object(emergency, 'local_guest', return_value={'guest_memory_limit_bytes': 1024**3, 'guest_available_bytes': 1024**3, 'guest_disk_free_bytes': 10 * 1024**3, 'scratch_available_bytes': 512 * 1024**2})
    def test_guest_limit_mismatch_and_post_job_reserve_hold(self, _):
        holds = emergency.emergency_admission({'n100_sample': sample(), 'policy_verified': True}, now=1000, required_memory=1024**3)
        self.assertIn('guest_ceiling_mismatch', holds)
        self.assertIn('guest_memory_reserve', holds)

    def test_only_preinstalled_blob_can_be_staged(self):
        instance = object.__new__(emergency.EmergencyWorker)
        instance.policy = {'source_artifact': {'sha256': 'a' * 64, 'size': 123}}
        self.assertTrue(instance.stage({'artifact_id': 'a' * 64, 'size': 123})['complete'])
        with self.assertRaisesRegex(ValueError, 'not_installed'):
            instance.stage({'artifact_id': 'b' * 64, 'size': 123})

    def test_cold_held_status_has_no_invented_limit_or_positive_headroom(self):
        instance=object.__new__(emergency.EmergencyWorker)
        instance.host={};instance.boot_id='boot'
        instance.policy={'enabled':False,'installed_source_sha':'a'*40,
                         'source_artifact':{'sha256':'b'*64,'size':123}}
        with patch.object(emergency.BaseWorker,'status',return_value={'admission':{'ready':False}}):
            response=instance.status()
        self.assertFalse(response['admission']['ready'])
        self.assertIsNone(response['telemetry']['guest_memory_limit_bytes'])
        self.assertEqual(response['telemetry']['guest_available_bytes'],0)
        self.assertFalse(response['telemetry']['policy_verified'])

    def test_expired_or_unmarked_urgency_is_rejected_before_execution(self):
        instance = object.__new__(emergency.EmergencyWorker)
        request = {'job_id': 'job', 'attempt_id': 'attempt', 'lease_generation': 1, 'kind': 'bushi.emergency',
                   'source_sha': 'a' * 40, 'input_sha256': 'b' * 64, 'manifest_sha256': 'c' * 64,
                   'manifest': {'emergency': {'urgent': True, 'deadline': int(time.time())-1}}}
        with patch.object(instance, 'bound_receipt', return_value={'state': 'not_found'}):
            with self.assertRaisesRegex(ValueError, 'deadline_required'): instance.start(request)
            request['manifest']['emergency'] = []
            with self.assertRaisesRegex(ValueError, 'invalid_emergency_deadline'): instance.start(request)


if __name__ == '__main__':
    unittest.main()
