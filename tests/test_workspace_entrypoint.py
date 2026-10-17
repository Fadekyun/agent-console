import importlib.util
from pathlib import Path
import tempfile
import unittest


class WorkspaceEntrypointTests(unittest.TestCase):
    def test_dry_run_and_atomic_compaction_preserve_all_original_bytes(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/compact-workspace-instructions.py'
        spec = importlib.util.spec_from_file_location('workspace_compaction', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = '# AGENTS.md\n\n## Guardrails\nNever delete live work.\n\n## Old deployment\nOld facts remain available.\n'
            (root / 'AGENTS.md').write_text(original)
            module.compact(root)
            self.assertEqual((root / 'AGENTS.md').read_text(), original)
            result = module.compact(root, write=True)
            self.assertEqual((root / result['archive']).read_text(), original)
            self.assertIn('Never delete live work.', (root / 'AGENTS.md').read_text())
            self.assertFalse(module.compact(root, write=True)['changed'])
