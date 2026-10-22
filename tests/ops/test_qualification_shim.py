"""Regression for the qualification shim's worker.run timeout contract."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ops/agc-worker/qualify-native.sh"


def load_shim():
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def command(args")
    end = text.index("worker.run = scoped_run") + len("worker.run = scoped_run")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    namespace = {
        "subprocess": SimpleNamespace(run=fake_run),
        "Path": Path,
        "qual_slice": "agcqualification-test.slice",
        "units": set(),
        "auxiliary_units": set(),
        "launch_count": {},
        "worker": SimpleNamespace(admission_holds=lambda *a, **k: []),
    }
    exec(text[start:end], namespace)
    return namespace, calls


class QualificationShimTests(unittest.TestCase):
    def test_scoped_run_forwards_explicit_and_default_timeouts(self):
        namespace, calls = load_shim()
        namespace["scoped_run"](["systemctl", "show", "unit"], check=False, timeout=2)
        self.assertEqual(calls[-1][1]["timeout"], 2)
        namespace["scoped_run"](["systemctl", "stop", "unit"], check=False, timeout=5)
        self.assertEqual(calls[-1][1]["timeout"], 5)
        namespace["scoped_run"](["systemctl", "show", "unit"], check=False)
        self.assertEqual(calls[-1][1]["timeout"], 25)

    def test_emergency_native_shim_forwards_timeout(self):
        source = (ROOT / "ops/agc-worker/qualify-emergency-native.py").read_text()
        tree = ast.parse(source)
        node = next(item for item in tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == "isolated_run")
        calls = []

        def fake_native(args, check=True, timeout=None):
            calls.append((list(args), check, timeout))
            return "result"

        namespace = {"native_run": fake_native, "units": set(), "timers": set(),
                     "launched": []}
        exec(ast.get_source_segment(source, node), namespace)
        self.assertEqual(namespace["isolated_run"](["systemctl", "show", "unit"], timeout=4), "result")
        self.assertEqual(calls[-1][2], 4)

    def test_scoped_run_cleanup_path_accepts_timeout(self):
        namespace, calls = load_shim()
        namespace["scoped_run"](["systemctl", "stop", "agc-job-x.service"], check=False, timeout=5)
        args, kwargs = calls[-1]
        self.assertEqual(args[:3], ["systemctl", "stop", "agc-job-x.service"])
        self.assertEqual(kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
