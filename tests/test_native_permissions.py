import unittest

from agent_console.native_permissions import native_permission_mode


class NativePermissionTests(unittest.TestCase):
    def test_automatic_review_owns_workspace_write_without_sandbox_flag(self):
        self.assertEqual(native_permission_mode(['codex', '--approve-for-me', 'exec']), 'workspace-write')

    def test_explicit_permissions_are_preserved(self):
        for mode in ('read-only', 'workspace-write', 'danger-full-access'):
            self.assertEqual(native_permission_mode(['codex', '--sandbox', mode]), mode)
            self.assertEqual(native_permission_mode(['codex', '--sandbox=' + mode]), mode)

    def test_missing_or_conflicting_permissions_fail_explicitly(self):
        for argv in ([], ['--sandbox'], ['--sandbox', 'unknown'],
                     ['--approve-for-me', '--sandbox', 'read-only'],
                     ['--sandbox', 'read-only', '--sandbox', 'workspace-write']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                native_permission_mode(argv)
