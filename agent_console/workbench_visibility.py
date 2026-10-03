import fcntl
import os
import json
import tempfile
from pathlib import Path


class WorkbenchVisibility:
    """Persistent per-session hidden state for workbench visibility."""

    def __init__(self, state_dir):
        state_dir = Path(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = state_dir / "workbench-visibility.json"
        self.lock_path = state_dir / "workbench-visibility.lock"

    def _validate_entry(self, identity, value):
        if not isinstance(identity, str) or not identity:
            raise ValueError("identity must be a non-empty string")
        if not isinstance(value, bool):
            raise ValueError("hidden must be bool")

    def read(self):
        """Read visibility map without locking.

        Returns a dict mapping stable session IDs to strict booleans.
        A missing file returns {}; malformed JSON or invalid entries raise ValueError.
        """
        if not self.path.exists():
            return {}
        with self.path.open("rb") as f:
            raw = f.read()
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("malformed workbench visibility JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("workbench visibility must be a JSON object")
        for identity, hidden in data.items():
            if not isinstance(identity, str) or not identity:
                raise ValueError("workbench visibility identity must be a non-empty string")
            if not isinstance(hidden, bool):
                raise ValueError("workbench visibility hidden must be bool")
        return dict(data)

    def _atomic_write(self, data):
        fd, temp_path = tempfile.mkstemp(
            prefix=".workbench-visibility.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, sort_keys=True))
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
        except BaseException:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass
            raise

    def set(self, identity, hidden):
        """Atomically persist a visibility entry for one session identity."""
        self._validate_entry(identity, hidden)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.lock_path, flags, 0o600)
        lock = None
        try:
            with os.fdopen(fd, "a+b") as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = self.read()
                    data[identity] = bool(hidden)
                    self._atomic_write(data)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except BaseException:
            if lock is None:
                os.close(fd)
            raise
