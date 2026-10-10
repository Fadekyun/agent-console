from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime
import unittest

from agent_console.compute_policy import (
    GiB, MiB, LAPTOP_RAM_CEILING_BYTES, PROFILES, WORKERS, admission,
    get_profile, laptop_availability, laptop_schedule_available,
    laptop_schedule_reason,
)


class ComputePolicyTests(unittest.TestCase):
    now = 1_791_111_600.0

    def telemetry(self, **changes):
        sample = {
            'physical_host': 'n100', 'guest_id': 'lxc-115',
            'enabled': True, 'online': True, 'operator_hold': False,
            'sampled_at': self.now, 'healthy_since': self.now - 300,
            'host_cpu_count': 4, 'host_memory_total_bytes': 16 * GiB,
            'host_available_bytes': 6 * GiB, 'guest_available_bytes': 2 * GiB,
            'guest_disk_free_bytes': 10 * GiB,
            'memory_full_avg60': 0.1, 'io_some_avg60': 0.5,
            'thinpool_data_percent': 50, 'thinpool_metadata_percent': 20,
        }
        sample.update(changes)
        return sample

    def reason(self, reservations=(), **changes):
        return admission('maintenance.report', self.telemetry(**changes), reservations, self.now)

    def reservation(self, **changes):
        held = {'physical_host': 'n100', 'guest_id': 'lxc-115',
                'memory_max_bytes': 512 * MiB, 'scratch_bytes': 256 * MiB,
                'cpu_cores': .25}
        held.update(changes)
        return held

    def test_healthy_report_admission_and_immutable_envelope(self):
        self.assertIsNone(self.reason())
        profile = get_profile('maintenance.report')
        self.assertEqual((profile.cpu_cores, profile.memory_high_bytes,
                          profile.memory_max_bytes, profile.memory_swap_max_bytes,
                          profile.pids_max, profile.scratch_bytes, profile.timeout_seconds),
                         (.25, 384 * MiB, 512 * MiB, 0, 64, 256 * MiB, 300))
        with self.assertRaises(FrozenInstanceError):
            profile.memory_max_bytes = 4 * GiB
        with self.assertRaises(TypeError):
            PROFILES['arbitrary.shell'] = profile
        self.assertEqual(admission(replace(profile, memory_max_bytes=8 * GiB),
                                   self.telemetry(), (), self.now), 'invalid_profile')
        self.assertEqual(admission('arbitrary.shell', self.telemetry(), (), self.now),
                         'unknown_profile')

    def test_disabled_worker_and_missing_telemetry_fail_closed(self):
        self.assertTrue(all(not worker.enabled for worker in WORKERS.values()))
        self.assertEqual(self.reason(enabled=False), 'worker_disabled')
        sample = self.telemetry()
        del sample['enabled']
        self.assertEqual(admission('maintenance.report', sample, (), self.now), 'worker_disabled')
        self.assertEqual(admission('maintenance.report', None, (), self.now), 'telemetry_missing')
        self.assertEqual(self.reason(online=False), 'waiting_for_worker')
        self.assertEqual(self.reason(operator_hold=True), 'operator_hold')
        self.assertEqual(self.reason(operator_hold=None), 'operator_hold')
        self.assertEqual(self.reason(enabled=1), 'worker_disabled')

    def test_existing_host_admission_veto_is_preserved(self):
        self.assertEqual(self.reason(host_gate='host_maintenance'), 'host_maintenance')
        self.assertEqual(self.reason(host_gate='host_snapshot_stale'), 'host_snapshot_stale')
        for value in ('arbitrary', [], True, ''):
            self.assertEqual(self.reason(host_gate=value), 'invalid_telemetry:host_gate')

    def test_placement_uses_physical_host_and_exact_guest(self):
        self.assertEqual(self.reason(physical_host='lxc-115'), 'physical_host_mismatch')
        self.assertEqual(self.reason(guest_id='lxc-106'), 'guest_mismatch')

    def test_freshness_and_healthy_history_boundaries(self):
        self.assertIsNone(self.reason(sampled_at=self.now - 30, healthy_since=self.now - 330))
        self.assertEqual(self.reason(sampled_at=self.now - 30.001), 'telemetry_stale')
        self.assertEqual(self.reason(sampled_at=self.now + .001), 'telemetry_from_future')
        self.assertEqual(self.reason(healthy_since=self.now - 299.999), 'warming_up')
        self.assertEqual(self.reason(healthy_since=self.now + 1), 'invalid_telemetry:healthy_since')

    def test_numeric_telemetry_rejects_missing_nonfinite_boolean_and_negative(self):
        numeric_keys = (
            'sampled_at', 'healthy_since', 'host_cpu_count', 'host_memory_total_bytes',
            'host_available_bytes', 'guest_available_bytes', 'guest_disk_free_bytes',
            'memory_full_avg60', 'io_some_avg60', 'thinpool_data_percent',
            'thinpool_metadata_percent',
        )
        for key in numeric_keys:
            for invalid in (None, True, '1', -1, float('nan'), float('inf'), 10 ** 1000):
                with self.subTest(key=key, invalid=str(invalid)[:20]):
                    self.assertEqual(self.reason(**{key: invalid}), 'invalid_telemetry:' + key)
        for invalid in (None, True, '1', -1, float('nan'), float('inf')):
            with self.subTest(now=invalid):
                self.assertEqual(admission('maintenance.report', self.telemetry(), (), invalid),
                                 'invalid_clock')

    def test_rejects_impossible_inventory_without_assuming_laptop_capacity(self):
        for cpu in (0, 1.5):
            self.assertEqual(self.reason(host_cpu_count=cpu), 'invalid_telemetry:host_cpu_count')
        self.assertEqual(self.reason(host_memory_total_bytes=0),
                         'invalid_telemetry:host_memory_total_bytes')
        self.assertEqual(self.reason(host_available_bytes=17 * GiB),
                         'invalid_telemetry:host_available_bytes')
        self.assertEqual(self.reason(guest_available_bytes=17 * GiB),
                         'invalid_telemetry:guest_available_bytes')
        for field in ('memory_full_avg60', 'io_some_avg60',
                      'thinpool_data_percent', 'thinpool_metadata_percent'):
            self.assertEqual(self.reason(**{field: 101}), 'invalid_telemetry:' + field)

    def test_pressure_and_thin_pool_gates_are_strict(self):
        for changes, expected in (
            ({'memory_full_avg60': 1}, 'memory_pressure'),
            ({'io_some_avg60': 10}, 'io_pressure'),
            ({'thinpool_data_percent': 80}, 'thinpool_data_pressure'),
            ({'thinpool_metadata_percent': 70}, 'thinpool_metadata_pressure'),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(self.reason(**changes), expected)
        self.assertIsNone(self.reason(memory_full_avg60=.999, io_some_avg60=9.999,
                                      thinpool_data_percent=79.999, thinpool_metadata_percent=69.999))

    def test_current_resource_failure_is_not_hidden_by_restarting_warmup(self):
        self.assertEqual(self.reason(healthy_since=self.now, thinpool_data_percent=89.87),
                         'thinpool_data_pressure')
        self.assertEqual(self.reason(healthy_since=self.now, guest_disk_free_bytes=3 * GiB),
                         'guest_disk_reserve')

    def test_host_guest_and_disk_reserve_boundaries(self):
        self.assertIsNone(self.reason(host_available_bytes=4 * GiB,
                                      guest_available_bytes=GiB,
                                      guest_disk_free_bytes=6 * GiB + 256 * MiB))
        self.assertEqual(self.reason(host_available_bytes=4 * GiB - 1), 'host_memory_headroom')
        self.assertEqual(self.reason(guest_available_bytes=GiB - 1), 'guest_memory_reserve')
        self.assertEqual(self.reason(guest_disk_free_bytes=6 * GiB + 256 * MiB - 1),
                         'guest_disk_reserve')

    def test_other_guest_reservation_consumes_same_physical_host_ram(self):
        held = self.reservation(guest_id='lxc-106', memory_max_bytes=2 * GiB)
        self.assertEqual(self.reason(reservations=[held], host_available_bytes=4 * GiB),
                         'host_memory_reserve')
        held['physical_host'] = 'agc-laptop'
        self.assertIsNone(self.reason(reservations=[held], host_available_bytes=4 * GiB))

    def test_unknown_attempt_reservation_still_consumes_resources(self):
        held = self.reservation(status='unknown', memory_max_bytes=GiB)
        self.assertEqual(self.reason(reservations=[held], guest_available_bytes=GiB),
                         'guest_memory_reserve')
        held.update(memory_max_bytes=0, scratch_bytes=GiB)
        self.assertEqual(self.reason(reservations=[held], guest_disk_free_bytes=7 * GiB),
                         'guest_disk_reserve')

    def test_cpu_pool_sizing_uses_observed_cpu_count(self):
        held = self.reservation(guest_id='lxc-106', cpu_cores=1.8)
        self.assertEqual(self.reason(reservations=[held], host_cpu_count=2), 'host_cpu_capacity')
        self.assertIsNone(self.reason(reservations=[held], host_cpu_count=4))

    def test_malformed_reservation_never_frees_capacity(self):
        for invalid in (None, 'reservation', {}, [None], [{}],
                        [self.reservation(memory_max_bytes=float('nan'))],
                        [self.reservation(scratch_bytes=-1)],
                        [self.reservation(cpu_cores=True)]):
            with self.subTest(reservation=invalid):
                self.assertEqual(self.reason(reservations=invalid), 'invalid_reservations')

    def test_laptop_inventory_is_disabled_and_ceiling_is_reclaimable(self):
        laptop = WORKERS['agc-laptop']
        self.assertFalse(laptop.enabled)
        self.assertTrue(laptop.ram_reclaimable)
        self.assertEqual(laptop.ram_ceiling_bytes, LAPTOP_RAM_CEILING_BYTES)
        self.assertEqual(laptop.ram_ceiling_bytes, 16_000_000_000)
        self.assertEqual(laptop.verified_memory_total_bytes, 33_066_012_672)
        self.assertEqual(laptop.verified_logical_cpus, 16)
        self.assertEqual(laptop.max_concurrent_jobs, 1)
        for name in ('yuyutei.collect', 'yuyutei.parse', 'agc.build', 'agc.test'):
            with self.subTest(profile=name):
                self.assertEqual(admission(name, self.telemetry(), (), self.now), 'profile_disabled')
                forged = replace(get_profile(name), enabled=True)
                self.assertEqual(admission(forged, self.telemetry(), (), self.now), 'invalid_profile')
        self.assertEqual(asdict(laptop)['schedule'], 'Wednesday-Sunday; Sunday admission closes 23:45')

    def test_schedule_is_singapore_wed_sun_with_sunday_drain(self):
        cases = (
            ('2026-10-06T15:59:59+00:00', 'schedule_closed'),  # Tuesday 23:59 SGT
            ('2026-10-06T16:00:00+00:00', None),  # Wednesday 00:00 SGT
            ('2026-10-11T15:44:59+00:00', None),
            ('2026-10-11T15:45:00+00:00', 'schedule_draining'),
            ('2026-10-11T16:00:00+00:00', 'schedule_closed'),  # Monday
        )
        for instant, expected in cases:
            with self.subTest(instant=instant):
                now = datetime.fromisoformat(instant).timestamp()
                self.assertEqual(laptop_schedule_reason(now), expected)
                self.assertEqual(laptop_schedule_available(now), expected is None)
                self.assertEqual(laptop_availability(now), expected or 'waiting_for_worker')
                self.assertEqual(laptop_availability(now, online=True), expected or 'worker_disabled')
        for invalid in (float('nan'), -1, float('inf'), 1e300):
            self.assertEqual(laptop_schedule_reason(invalid), 'invalid_clock')


if __name__ == '__main__':
    unittest.main()
