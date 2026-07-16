from __future__ import annotations

import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .validation import validate_session_name


@dataclass(frozen=True)
class TmuxSession:
    name: str
    created_epoch: int
    activity_epoch: int
    attached_clients: int
    windows: int
    current_command: str


class Tmux:
    def __init__(
        self,
        socket_name: str | None = None,
        socket_path: Path | None = None,
        *,
        scope: str = "canonical",
    ):
        if socket_name and socket_path:
            raise ValueError("tmux socket name and socket path are mutually exclusive")
        self.socket_name = socket_name
        self.socket_path = socket_path
        self.scope = scope

    def command(self, *args: str) -> list[str]:
        command = ["tmux"]
        if self.socket_path:
            command.extend(["-S", str(self.socket_path)])
        elif self.socket_name:
            command.extend(["-L", self.socket_name])
        command.extend(args)
        return command

    def run(
        self,
        *args: str,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self.command(*args),
            check=False,
            capture_output=capture_output,
            text=True,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown tmux error").strip()
            operation = args[0] if args else "command"
            raise RuntimeError(f"tmux {operation} failed: {detail}")
        return result

    def _remove_owned_stale_socket(self) -> bool:
        if not self.socket_path or not self.socket_path.exists():
            return False
        probe = self.run("list-sessions", check=False)
        if probe.returncode == 0:
            return False
        metadata = self.socket_path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError(f"refusing to remove unowned or non-socket tmux path: {self.socket_path}")
        self.socket_path.unlink()
        return True

    def list_sessions(self) -> dict[str, TmuxSession]:
        result = self.run(
            "list-sessions",
            "-F",
            "#{session_name}\t#{session_created}\t#{session_activity}\t#{session_attached}\t#{session_windows}\t#{pane_current_command}",
            check=False,
        )
        if result.returncode != 0:
            return {}
        sessions: dict[str, TmuxSession] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t", 5)
            if len(parts) != 6:
                continue
            name, created, activity, attached, windows, command = parts
            sessions[name] = TmuxSession(
                name=name,
                created_epoch=int(created),
                activity_epoch=int(activity),
                attached_clients=int(attached),
                windows=int(windows),
                current_command=command,
            )
        return sessions

    def exists(self, name: str) -> bool:
        validate_session_name(name)
        return self.run("has-session", "-t", name, check=False).returncode == 0

    def create(self, name: str, cwd: Path, launcher: Path) -> None:
        validate_session_name(name)
        if self.socket_path:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.socket_path.parent.chmod(0o700)
            self._remove_owned_stale_socket()
        self.run("new-session", "-d", "-s", name, "-c", str(cwd))
        self.run("set-option", "-t", name, "detach-on-destroy", "on")
        self._run_launcher(name, launcher)

    def _run_launcher(self, name: str, launcher: Path) -> None:
        validate_session_name(name)
        self.run("send-keys", "-t", name, "-l", shlex.quote(str(launcher)))
        self.run("send-keys", "-t", name, "Enter")

    def interrupt(self, name: str) -> None:
        validate_session_name(name)
        self.run("send-keys", "-t", name, "C-c")

    def restart(self, name: str, launcher: Path) -> None:
        validate_session_name(name)
        current_path = self.run(
            "list-panes", "-t", name, "-F", "#{pane_current_path}"
        ).stdout.splitlines()[0]
        self.run("respawn-pane", "-k", "-t", name, "-c", current_path)
        self._run_launcher(name, launcher)

    def rename(self, name: str, new_name: str) -> None:
        validate_session_name(name)
        validate_session_name(new_name)
        self.run("rename-session", "-t", name, new_name)

    def kill(self, name: str) -> None:
        validate_session_name(name)
        self.run("kill-session", "-t", name)

    def pane_pids(self, name: str) -> list[int]:
        validate_session_name(name)
        result = self.run("list-panes", "-t", name, "-F", "#{pane_pid}", check=False)
        if result.returncode != 0:
            return []
        return [int(value) for value in result.stdout.splitlines() if value.isdigit()]

    def capture(
        self,
        name: str,
        *,
        lines: int | None = None,
        max_bytes: int = 262_144,
    ) -> tuple[str, bool]:
        validate_session_name(name)
        if lines is not None and not 1 <= lines <= 1000:
            raise ValueError("capture lines must be between 1 and 1000")
        start = "-" if lines is None else f"-{lines}"
        output = self.run("capture-pane", "-p", "-S", start, "-t", name).stdout
        encoded = output.encode("utf-8")
        if len(encoded) <= max_bytes:
            return output, False
        return encoded[-max_bytes:].decode("utf-8", errors="replace"), True

    def alternate_screen(self, name: str) -> bool:
        validate_session_name(name)
        result = self.run(
            "display-message", "-p", "-t", name, "#{alternate_on}", check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "1"

    def attach(self, name: str) -> None:
        validate_session_name(name)
        args = self.command("attach-session", "-t", name)
        os.execvp(args[0], args)
