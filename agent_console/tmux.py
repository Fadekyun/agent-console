from __future__ import annotations

import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .validation import validate_session_name

# tmux reports these when the target session, or the whole server, is already
# gone. They are the only rename failures a finished-session rename may ignore.
MISSING_SESSION_MARKERS = ("can't find session", "no server running")


def session_missing_error(error: BaseException) -> bool:
    """True only when tmux positively reported that the session is gone.

    A transient failure (socket, permissions, resource exhaustion) carries a
    different message and must stay fatal: tolerating it would rename the
    database row, launcher and context while the live tmux session keeps the old
    name, leaving the session inconsistent.
    """
    text = str(error).lower()
    return any(marker in text for marker in MISSING_SESSION_MARKERS)


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

    @staticmethod
    def session_target(name: str) -> str:
        # Bare targets allow prefix/glob matches in tmux. A removed parent must
        # never resolve to a surviving child whose name starts with its name.
        return "=" + validate_session_name(name)

    @staticmethod
    def pane_target(name: str) -> str:
        # Explicit session: disambiguates from window/pane names and indexes.
        return Tmux.session_target(name) + ":"

    def attach_command(self, name: str) -> list[str]:
        return self.command("attach-session", "-t", self.session_target(name))

    def run(
        self,
        *args: str,
        check: bool = True,
        capture_output: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                self.command(*args),
                check=False,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            operation = args[0] if args else "command"
            raise RuntimeError(f"tmux {operation} timed out; retry when the server responds") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown tmux error").strip()
            operation = args[0] if args else "command"
            raise RuntimeError(f"tmux {operation} failed: {detail}")
        return result

    def scroll_history(self, name: str, lines: int) -> None:
        """Scroll tmux's saved pane output without sending keys to the program.

        tmux renders into an alternate screen, so xterm's browser scrollback
        does not contain the complete pane history. Zero returns to live output.
        Like native tmux copy mode this view is shared by attached clients.
        """
        validate_session_name(name)
        if type(lines) is not int or not -50 <= lines <= 50:
            raise ValueError("scroll lines must be an integer between -50 and 50")
        if lines == 0:
            mode = self.run("display-message", "-p", "-t", self.pane_target(name), "#{pane_in_mode}", timeout=2).stdout.strip()
            if mode == "1":
                self.run("send-keys", "-t", self.pane_target(name), "-X", "cancel", timeout=2)
            return
        self.run("copy-mode", "-e", "-t", self.pane_target(name), timeout=2)
        self.run("send-keys", "-t", self.pane_target(name), "-X", "-N", str(abs(lines)),
                 "scroll-up" if lines < 0 else "scroll-down", timeout=2)

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
            timeout=2,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown tmux error").strip()
            if "no server running" in detail.lower() or (
                "error connecting to" in detail.lower() and "no such file or directory" in detail.lower()
            ):
                return {}
            raise RuntimeError(f"tmux list-sessions failed: {detail}")
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
        return self.run("has-session", "-t", self.session_target(name), check=False).returncode == 0

    def create(self, name: str, cwd: Path, launcher: Path) -> None:
        validate_session_name(name)
        if self.socket_path:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.socket_path.parent.chmod(0o700)
            self._remove_owned_stale_socket()
        self.run("new-session", "-d", "-s", name, "-c", str(cwd))
        self.run("set-option", "-t", self.pane_target(name), "detach-on-destroy", "on")
        self._run_launcher(name, launcher)

    def _run_launcher(self, name: str, launcher: Path) -> None:
        validate_session_name(name)
        self.run("send-keys", "-t", self.pane_target(name), "-l", shlex.quote(str(launcher)))
        self.run("send-keys", "-t", self.pane_target(name), "Enter")

    def interrupt(self, name: str) -> None:
        validate_session_name(name)
        self.run("send-keys", "-t", self.pane_target(name), "C-c")

    def restart(self, name: str, launcher: Path) -> None:
        validate_session_name(name)
        current_path = self.run(
            "list-panes", "-t", self.pane_target(name), "-F", "#{pane_current_path}"
        ).stdout.splitlines()[0]
        self.run("respawn-pane", "-k", "-t", self.pane_target(name), "-c", current_path)
        self._run_launcher(name, launcher)

    def rename(self, name: str, new_name: str) -> None:
        validate_session_name(name)
        validate_session_name(new_name)
        self.run("rename-session", "-t", self.session_target(name), new_name, timeout=2)

    def kill(self, name: str) -> None:
        validate_session_name(name)
        self.run("kill-session", "-t", self.session_target(name))

    def pane_pids(self, name: str) -> list[int]:
        validate_session_name(name)
        result = self.run("list-panes", "-t", self.pane_target(name), "-F", "#{pane_pid}", check=False)
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
        output = self.run("capture-pane", "-p", "-S", start, "-t", self.pane_target(name)).stdout
        encoded = output.encode("utf-8")
        if len(encoded) <= max_bytes:
            return output, False
        return encoded[-max_bytes:].decode("utf-8", errors="replace"), True

    def alternate_screen(self, name: str) -> bool:
        validate_session_name(name)
        result = self.run(
            "display-message", "-p", "-t", self.pane_target(name), "#{alternate_on}", check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "1"

    def attach(self, name: str) -> None:
        validate_session_name(name)
        args = self.attach_command(name)
        os.execvp(args[0], args)
