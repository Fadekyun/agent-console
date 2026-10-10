"""Small filesystem fixtures; no host probes, subprocesses or native agents."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    'resource_sampler', Path(__file__).resolve().parents[1] / 'ops/n100-resource-sampler.py')
sampler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampler)
MIB = 1024 ** 2


class ResourceSamplerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write(self.root, limit=4096 * MIB, current=3900 * MIB, inactive=1800 * MIB)
        self.write(self.root / 'ns', limit='max')

    def write(self, path, *, limit, current=0, inactive=0, psi=0):
        path.mkdir(exist_ok=True)
        for name, content in {
            'memory.max': str(limit), 'memory.current': str(current),
            'memory.stat': 'inactive_file ' + str(inactive) + '\nanon 100\n',
            'memory.pressure': 'full avg10=' + str(psi) + ' avg60=0 avg300=0 total=0\n',
        }.items():
            (path / name).write_text(content)

    def test_cache_reclaims_margin_without_hiding_raw_usage(self):
        result = sampler.container_memory(self.root)
        self.assertEqual(result['ct115_memory_headroom_bytes'], 1996 * MIB)
        self.assertEqual(result['ct115_memory_raw_headroom_bytes'], 196 * MIB)
        self.assertEqual(result['ct115_memory_current_bytes'], 3900 * MIB)
        self.assertEqual(result['ct115_memory_limit_bytes'], 4096 * MIB)
        self.assertEqual(result['ct115_memory_inactive_file_bytes'], 1800 * MIB)
        self.assertTrue(result['ct115_memory_sample_valid'])

    def test_real_noncache_usage_remains_blocked(self):
        self.write(self.root, limit=4096 * MIB, current=3900 * MIB, inactive=10 * MIB)
        self.assertLess(sampler.container_memory(self.root)['ct115_memory_headroom_bytes'], 512 * MIB)

    def test_each_finite_limit_uses_its_own_usage(self):
        self.write(self.root / 'ns', limit=2048 * MIB, current=1900 * MIB, inactive=100 * MIB)
        result = sampler.container_memory(self.root)
        self.assertEqual(result['ct115_memory_headroom_bytes'], 248 * MIB)
        self.assertEqual(result['ct115_memory_current_bytes'], 1900 * MIB)
        self.assertEqual(result['ct115_memory_limit_bytes'], 2048 * MIB)
        self.write(self.root, limit=4096 * MIB, current=4000 * MIB, inactive=0)
        self.assertEqual(sampler.container_memory(self.root)['ct115_memory_headroom_bytes'], 96 * MIB)

    def test_pressure_vetoes_even_with_reclaimable_cache(self):
        self.write(self.root / 'ns', limit=2048 * MIB, current=1000 * MIB, inactive=500 * MIB, psi=5.01)
        result = sampler.container_memory(self.root)
        self.assertEqual(result['ct115_memory_headroom_bytes'], 0)
        self.assertEqual(result['ct115_full_psi_avg10'], 5.01)
        self.assertTrue(result['ct115_memory_sample_valid'])

    def test_missing_invalid_or_impossible_counters_fail_closed(self):
        for filename, value in (
            ('memory.stat', None), ('memory.stat', 'anon 10\n'),
            ('memory.stat', 'inactive_file -1\n'), ('memory.stat', 'inactive_file invalid\n'),
            ('memory.stat', 'inactive_file ' + str(2 ** 64) + '\n'),
            ('memory.stat', 'inactive_file ' + str(4000 * MIB) + '\n'),
            ('memory.current', '-1'), ('memory.current', 'bad'),
            ('memory.max', '-1'), ('memory.max', '0'),
            ('memory.pressure', 'full avg10=nan\n'),
            ('memory.pressure', 'full avg10=-1\n'),
            ('memory.pressure', 'some avg10=0\n'),
        ):
            with self.subTest(filename=filename, value=value):
                self.write(self.root, limit=4096 * MIB, current=3900 * MIB, inactive=1800 * MIB)
                path = self.root / filename
                path.unlink() if value is None else path.write_text(value)
                result = sampler.container_memory(self.root)
                self.assertEqual(result['ct115_memory_headroom_bytes'], 0)
                self.assertFalse(result['ct115_memory_sample_valid'])

    def test_missing_or_unbounded_limit_fails_closed(self):
        for name in ('ns/memory.max', 'memory.max'):
            with self.subTest(name=name):
                self.write(self.root, limit='max')
                self.write(self.root / 'ns', limit='max')
                (self.root / name).unlink()
                self.assertFalse(sampler.container_memory(self.root)['ct115_memory_sample_valid'])
        self.write(self.root, limit='max')
        self.write(self.root / 'ns', limit='max')
        self.assertEqual(sampler.container_memory(self.root)['ct115_memory_headroom_bytes'], 0)
