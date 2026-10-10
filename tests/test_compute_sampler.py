import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_console.compute_sampler import (
    GIB, HOST_PROBE_COMMAND, MAX_PROBE_BYTES, ComputeSampler, maintenance_report, host_snapshot_gate,
)


class ComputeSamplerTests(unittest.TestCase):
    def setUp(self):
        self.host = {
            'host_cpu_count': 4, 'host_memory_total_bytes': 16 * GIB,
            'host_available_bytes': 6 * GIB, 'guest_limit_bytes': 6 * GIB,
            'memory_full_avg60': .1, 'io_some_avg60': 1,
            'thinpool_data_percent': 50, 'thinpool_metadata_percent': 5,
        }
        self.files = {
            '/proc/self/cgroup': '0::/user.slice/session.scope\n',
            '/proc/meminfo': 'MemTotal: 6291456 kB\nMemAvailable: 4194304 kB\n',
            '/sys/fs/cgroup/memory.max': 'max\n',
            '/sys/fs/cgroup/memory.current': str(2 * GIB),
            '/sys/fs/cgroup/user.slice/memory.max': str(3 * GIB),
            '/sys/fs/cgroup/user.slice/memory.current': str(GIB),
            '/sys/fs/cgroup/user.slice/session.scope/memory.max': 'max',
            '/sys/fs/cgroup/user.slice/session.scope/memory.current': str(GIB // 2),
        }
        self.calls = []
        self.free = 10 * GIB
        self.sampler = ComputeSampler(
            '/var/lib/agent-console', transport=self.transport,
            read_text=lambda path: self.files[str(path)],
            disk_free=lambda path: self.free,
            device_id=lambda path: 1,
            resource_snapshot='',
            hostname=lambda: 'agent-console-staging',
        )

    def transport(self, command, timeout):
        self.calls.append((command, timeout))
        return json.dumps(self.host)

    def sample(self, at=0, **kwargs):
        return self.sampler.sample(enabled=True, operator_hold=False, now=at, **kwargs)

    def test_disabled_default_does_not_contact_host(self):
        report = self.sampler.sample(now=0)
        self.assertFalse(report['enabled'])
        self.assertTrue(report['operator_hold'])
        self.assertFalse(report['online'])
        self.assertEqual(report['error'], 'disabled')
        self.assertEqual(self.calls, [])
        self.assertNotIn('host_available_bytes', report)

    def test_host_veto_prevents_probe_and_resets_observation_history(self):
        self.sample(0)
        with patch('agent_console.compute_sampler.host_snapshot_gate', return_value='host_maintenance'):
            report = self.sample(10)
        self.assertEqual(report['host_gate'], 'host_maintenance')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sample(20)['healthy_since'], 20)

    def test_relocated_state_volume_fails_closed_before_host_probe(self):
        self.sampler._device_id = lambda path: 1 if str(path) == '/' else 2
        self.assertFalse(self.sample()['online'])
        self.assertEqual(self.calls, [])

    def test_fixed_probe_and_minimum_visible_ancestor_headroom(self):
        report = self.sample()
        self.assertTrue(report['online'])
        self.assertTrue(report['supported'])
        self.assertEqual(report['guest_available_bytes'], 2 * GIB)
        self.assertEqual(report['guest_disk_free_bytes'], 10 * GIB)
        self.assertEqual(self.calls[0], (HOST_PROBE_COMMAND, 12.0))
        self.assertIn('StrictHostKeyChecking=yes', HOST_PROBE_COMMAND)
        self.assertIn('BatchMode=yes', HOST_PROBE_COMMAND)
        self.assertEqual(HOST_PROBE_COMMAND[-2], 'n100')
        self.assertIn('vm-115-disk-0', HOST_PROBE_COMMAND[-1])

    def test_proxmox_cap_covers_hidden_lxc_parent(self):
        self.files['/sys/fs/cgroup/memory.current'] = str(6 * GIB - GIB // 4)
        report = self.sample()
        self.assertEqual(report['guest_available_bytes'], GIB // 4)

    def test_over_limit_usage_is_zero_available_not_negative(self):
        self.files['/sys/fs/cgroup/user.slice/memory.current'] = str(4 * GIB)
        self.assertEqual(self.sample()['guest_available_bytes'], 0)

    def test_five_minutes_of_fresh_healthy_samples_required(self):
        for at in range(0, 301, 10):
            report = self.sample(at)
        self.assertEqual(report['healthy_since'], 0)
        self.assertEqual(report['sampled_at'], 300)
        self.assertEqual(self.sample(331)['healthy_since'], 331)
        self.assertEqual(self.sample(320)['healthy_since'], 320)

    def test_pressure_and_operator_hold_reset_history(self):
        self.sample(0)
        self.sample(10)
        self.host['memory_full_avg60'] = 1
        self.assertEqual(self.sample(20)['healthy_since'], 20)
        self.host['memory_full_avg60'] = .1
        self.assertEqual(self.sample(30)['healthy_since'], 30)
        self.sampler.sample(enabled=True, operator_hold=True, now=40)
        self.assertEqual(self.sample(50)['healthy_since'], 50)

    def test_disk_pool_headroom_and_disable_reset_history(self):
        for key, bad in [('host_available_bytes', 3 * GIB), ('thinpool_data_percent', 80),
                         ('thinpool_metadata_percent', 70), ('io_some_avg60', 10)]:
            original = self.host[key]
            self.sample(0)
            self.host[key] = bad
            self.sample(10)
            self.host[key] = original
            self.assertEqual(self.sample(20)['healthy_since'], 20)
        self.free = 5 * GIB
        self.sample(30)
        self.free = 10 * GIB
        self.assertEqual(self.sample(40)['healthy_since'], 40)
        self.sampler.sample(now=50)
        self.assertEqual(self.sample(60)['healthy_since'], 60)

    def test_missing_guest_facts_and_unsupported_identity_fail_closed(self):
        del self.files['/sys/fs/cgroup/user.slice/memory.max']
        report = self.sample()
        self.assertFalse(report['online'])
        self.assertEqual(report['error'], 'telemetry_unavailable')
        self.sampler._hostname = lambda: 'another-machine'
        self.assertFalse(self.sample()['online'])

    def test_invalid_cgroup_paths_fail_closed(self):
        for path in ('0::/../../etc', '0::relative', '1:memory:/container', '0::/\n0::/'):
            with self.subTest(path=path):
                self.files['/proc/self/cgroup'] = path
                self.assertFalse(self.sample()['online'])

    def test_invalid_or_missing_host_numbers_fail_closed(self):
        for value in (None, True, -1, float('nan'), float('inf'), '999', 10 ** 1000):
            with self.subTest(value=value):
                self.host['host_available_bytes'] = value
                self.assertFalse(self.sample()['online'])
        del self.host['host_available_bytes']
        self.assertFalse(self.sample()['online'])

    def test_probe_timeout_and_errors_are_sanitized_and_reset_history(self):
        self.sample(0)
        def broken(*args):
            raise subprocess.TimeoutExpired('SECRET MUST NOT BE INCLUDED', 12)
        self.sampler._transport = broken
        report = self.sample(10)
        self.assertFalse(report['online'])
        self.assertEqual(report['error'], 'telemetry_unavailable')
        self.assertNotIn('SECRET', json.dumps(report))
        self.sampler._transport = self.transport
        self.assertEqual(self.sample(20)['healthy_since'], 20)

    def test_oversized_probe_is_rejected(self):
        self.sampler._transport = lambda *_: 'x' * (MAX_PROBE_BYTES + 1)
        self.assertFalse(self.sample()['online'])

    def test_report_is_a_sanitized_bounded_copy_and_does_not_probe(self):
        telemetry = self.sample()
        self.calls.clear()
        telemetry.update(secret='do not copy', error='secret exception detail', arbitrary=['x'] * 10000)
        report = maintenance_report(telemetry)
        self.assertEqual(report['execution'], 'in_process_read_only')
        self.assertFalse(report['production_writes'])
        self.assertNotIn('secret', report)
        self.assertNotIn('arbitrary', report)
        self.assertNotIn('error', report)
        self.assertLess(len(json.dumps(report)), 1500)
        self.assertEqual(self.calls, [])
        telemetry['host_available_bytes'] = 0
        self.assertEqual(report['host_available_bytes'], 6 * GIB)

    def test_report_excludes_non_numeric_or_non_finite_telemetry(self):
        report = maintenance_report({'host_available_bytes': 'secret', 'guest_available_bytes': float('nan'),
                                     'sampled_at': True, 'physical_host': 'untrusted', 'online': 'yes', 'error': []})
        self.assertNotIn('host_available_bytes', report)
        self.assertNotIn('guest_available_bytes', report)
        self.assertNotIn('sampled_at', report)
        self.assertNotIn('physical_host', report)
        self.assertNotIn('online', report)
        self.assertNotIn('error', report)


class HostSnapshotGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'resources.json'
        self.data = dict(sampled_at=1000, host_available_bytes=6 * GIB,
                         host_full_psi_avg10=0, ct115_memory_headroom_bytes=2 * GIB,
                         ct115_disk_free_bytes=10 * GIB, tmp_free_bytes=16 * GIB,
                         maintenance=False)
        self.owner, self.mode = 0, 0o100644
        self.stat = os.fstat

    def gate(self, **changes):
        self.path.write_text(json.dumps({**self.data, **changes}))

        def metadata(fd):
            actual = self.stat(fd)
            return SimpleNamespace(st_uid=self.owner, st_mode=self.mode, st_size=actual.st_size)

        with patch('agent_console.compute_sampler.os.fstat', side_effect=metadata):
            return host_snapshot_gate(str(self.path), 1000)

    def test_healthy_existing_snapshot_and_explicit_unconfigured_host(self):
        self.assertIsNone(self.gate())
        self.assertIsNone(host_snapshot_gate(None, 1000))

    def test_shared_maintenance_freshness_and_resource_vetoes(self):
        for changes, reason in (
            ({'maintenance': True}, 'host_maintenance'),
            ({'sampled_at': 939}, 'host_snapshot_stale'),
            ({'sampled_at': 1006}, 'host_snapshot_stale'),
            ({'host_available_bytes': GIB}, 'host_snapshot_memory_low'),
            ({'ct115_memory_headroom_bytes': 1}, 'host_snapshot_container_memory_low'),
            ({'ct115_disk_free_bytes': GIB}, 'host_snapshot_disk_low'),
            ({'tmp_free_bytes': GIB}, 'host_snapshot_temporary_disk_low'),
            ({'host_full_psi_avg10': 6}, 'host_snapshot_memory_pressure'),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(self.gate(**changes), reason)

    def test_untrusted_owner_writable_or_nonregular_snapshot_fails_closed(self):
        for owner, mode in ((1000, 0o100644), (0, 0o100666), (0, 0o010644)):
            self.owner, self.mode = owner, mode
            self.assertEqual(self.gate(), 'host_snapshot_unavailable')

    def test_missing_symlink_and_malformed_data_never_bypass_gate(self):
        self.assertEqual(host_snapshot_gate(str(self.path), 1000), 'host_snapshot_unavailable')
        self.gate()
        link = self.path.with_name('linked.json')
        link.symlink_to(self.path)
        self.assertEqual(host_snapshot_gate(str(link), 1000), 'host_snapshot_unavailable')
        for change in ({'maintenance': None}, {'sampled_at': True},
                       {'host_available_bytes': float('nan')}, {'tmp_free_bytes': 'secret'},
                       {'unused': 'x' * 9000}):
            with self.subTest(change=next(iter(change))):
                self.assertEqual(self.gate(**change), 'host_snapshot_unavailable')


if __name__ == '__main__':
    unittest.main()
