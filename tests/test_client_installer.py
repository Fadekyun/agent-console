from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


INSTALLER = Path(__file__).parents[1] / "client" / "install-client.py"
SPEC = importlib.util.spec_from_file_location("agent_console_client_installer", INSTALLER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClientInstallerTests(unittest.TestCase):
    def test_managed_block_replaces_only_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "id_ed25519"
            key.write_text("test fixture only\n", encoding="utf-8")
            unrelated = "Host github.com\n    User git\n"
            first = MODULE.managed_block("router.example.ts.net", "192.0.2.10", key, "agent")
            second = MODULE.managed_block("router.example.ts.net", "192.0.2.11", key, "agent")
            combined = second + "\n" + MODULE.remove_block(first + "\n" + unrelated)
            self.assertEqual(combined.count(MODULE.BEGIN), 1)
            self.assertIn("192.0.2.11", combined)
            self.assertNotIn("192.0.2.10", combined)
            self.assertIn(unrelated, combined)


if __name__ == "__main__":
    unittest.main()
