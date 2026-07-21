from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    state_dir: Path
    database_path: Path
    profile_dir: Path
    handoff_dir: Path
    worktree_root: Path
    tmux_socket: str | None
    tmux_socket_path: Path | None = None
    legacy_tmux_socket_path: Path | None = None
    config_dir: Path | None = None
    releases_root: Path | None = None
    max_children_per_parent: int = 3
    max_managed_sessions: int = 12
    deployment_mode: str = "disabled"

    @classmethod
    def from_env(cls) -> "Settings":
        home = Path.home()
        workspace_root = Path(
            os.getenv("AGENT_CONSOLE_WORKSPACE_ROOT", ".")
        ).expanduser()
        state_dir = Path(
            os.getenv(
                "AGENT_CONSOLE_STATE_DIR",
                home / ".local" / "share" / "agent-console",
            )
        ).expanduser()
        return cls(
            workspace_root=workspace_root,
            state_dir=state_dir,
            database_path=Path(
                os.getenv("AGENT_CONSOLE_DB", state_dir / "agent-console.sqlite3")
            ).expanduser(),
            profile_dir=Path(
                os.getenv("AGENT_CONSOLE_PROFILE_DIR", workspace_root / "agent-profiles")
            ).expanduser(),
            handoff_dir=Path(
                os.getenv("AGENT_CONSOLE_HANDOFF_DIR", workspace_root / "handoffs")
            ).expanduser(),
            worktree_root=Path(
                os.getenv("AGENT_CONSOLE_WORKTREE_ROOT", workspace_root / "worktrees")
            ).expanduser(),
            tmux_socket=os.getenv("AGENT_CONSOLE_TMUX_SOCKET") or None,
            tmux_socket_path=Path(
                os.getenv(
                    "AGENT_CONSOLE_TMUX_SOCKET_PATH",
                    f"/run/user/{os.getuid()}/agent-console/tmux.sock",
                )
            ).expanduser(),
            legacy_tmux_socket_path=Path(
                os.getenv(
                    "AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH",
                    f"/tmp/tmux-{os.getuid()}/default",
                )
            ).expanduser(),
            config_dir=Path(
                os.getenv(
                    "AGENT_CONSOLE_CONFIG_DIR",
                    home / ".config" / "agent-console",
                )
            ).expanduser(),
            releases_root=Path(
                os.getenv(
                    "AGENT_CONSOLE_RELEASES_ROOT",
                    state_dir / "releases",
                )
            ).expanduser(),
            max_children_per_parent=int(os.getenv("AGENT_CONSOLE_MAX_CHILDREN", "3")),
            max_managed_sessions=int(os.getenv("AGENT_CONSOLE_MAX_SESSIONS", "12")),
            deployment_mode=os.getenv("AGENT_CONSOLE_DEPLOYMENT_MODE", "disabled").lower().strip(),
        )

    def ensure_state_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / "launchers").mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / "transcripts").mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / "contexts").mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / "model-cache").mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.releases_root:
            self.releases_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        config_dir = self.config_dir or self.state_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_dir.chmod(0o700)
        if self.tmux_socket_path:
            self.tmux_socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.tmux_socket_path.parent.chmod(0o700)
