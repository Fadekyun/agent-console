import os
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile

from agent_console.workbench_visibility import WorkbenchVisibility


class WorkbenchVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbench-visibility-")
        self.state_dir = Path(self.temp.name)
        self.visibility = WorkbenchVisibility(self.state_dir)

    def tearDown(self):
        self.temp.cleanup()

    def assertPermissions(self, path, expected):
        actual = path.stat().st_mode & 0o777
        self.assertEqual(actual, expected)

    def test_persistence_true_false_same_id_across_instances(self):
        self.visibility.set("session-1", True)
        self.visibility.set("session-1", False)

        second = WorkbenchVisibility(self.state_dir)
        values = second.read()
        self.assertEqual(len(values), 1)
        self.assertIs(values["session-1"], False)

    def test_concurrent_independent_updates_no_lost_updates(self):
        n = 20
        def worker(i):
            WorkbenchVisibility(self.state_dir).set(f"session-{i:03d}", i % 2 == 0)
            return f"session-{i:03d}"

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(worker, range(n)))
        self.assertEqual(len(set(ids)), n)

        values = WorkbenchVisibility(self.state_dir).read()
        self.assertEqual(len(values), n)
        for i in range(n):
            key = f"session-{i:03d}"
            self.assertIn(key, values)
            self.assertIs(values[key], i % 2 == 0)

    def test_malformed_json_raises_and_preserves_bytes(self):
        self.visibility.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.visibility.read()
        with self.assertRaises(ValueError):
            self.visibility.set("session-2", True)
        self.assertEqual(self.visibility.path.read_bytes(), b"not json")

    def test_private_file_mode_0600(self):
        self.visibility.set("session-1", True)
        self.assertPermissions(self.visibility.path, 0o600)


if __name__ == "__main__":
    unittest.main()
