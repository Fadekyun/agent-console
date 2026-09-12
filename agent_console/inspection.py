"""Private experimental SQL reader. Not wired into CLI or runtime yet.

This is an application-state write guard for trusted read code, not a sandbox
for running user code. Tmux observation and formatting belong in the caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


class InspectionUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"inspection unavailable: {code}")


def read_session_snapshot(database_path: Path) -> dict:
    """Obtain a guarded schema10/11 snapshot, or fail without a fallback."""
    worker = Path(__file__).with_name("_inspection_reader.py").resolve(strict=True)
    # -I ignores caller Python configuration; -S suppresses site/.pth startup;
    # -B prevents bytecode writes. No inherited LD_*, PYTHON*, HOME or hooks.
    environment = {"LC_ALL": "C", "LANG": "C", "PATH": os.defpath}
    request = json.dumps({"database": str(database_path.absolute())})
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(worker)],
            input=request, text=True, encoding="utf-8", errors="strict",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, cwd="/", close_fds=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise InspectionUnavailable("reader-unavailable") from None
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise InspectionUnavailable("snapshot-too-large")
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise InspectionUnavailable("reader-unavailable") from None
    if completed.returncode or not isinstance(result, dict) or not result.get("ok"):
        allowed = {"guard-unavailable", "state-unavailable", "schema-unsupported",
                   "schema-invalid", "snapshot-too-large", "reader-unavailable"}
        code = result.get("error") if isinstance(result, dict) else None
        raise InspectionUnavailable(code if code in allowed else "reader-unavailable")
    return result
