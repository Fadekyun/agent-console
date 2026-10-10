from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import socket
import tempfile
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import nullcontext

from .admission import admission_lock
from .entrypoints import entrypoint_lock
from .schema_compatibility import prepare_database_for_release, release_schema_version

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
CURRENT_LINK = "current"
CANARY_LINK = "canary"
RELEASE_NAME_RE = re.compile(r"^release-[A-Za-z0-9._-]+$")


class DeploymentMode(enum.Enum):
    DISABLED = "disabled"
    STAGING = "staging"

EXCLUDED_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", ".runtime", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".egg-info"})
EXCLUDED_FILE_NAMES = frozenset({".env", ".env.local", ".env.production"})

RUNTIME_ASSETS = (
    "node_modules/@xterm/xterm/lib/xterm.mjs",
    "node_modules/@xterm/xterm/css/xterm.css",
    "node_modules/@xterm/addon-fit/lib/addon-fit.mjs",
)


def _epoch_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_excluded(rel: str) -> bool:
    parts = Path(rel).parts
    for part in parts:
        if part in EXCLUDED_DIRS:
            return True
    if Path(rel).suffix in EXCLUDED_FILE_SUFFIXES:
        return True
    if Path(rel).name in EXCLUDED_FILE_NAMES:
        return True
    return False


def _build_manifest(release_path: Path, *, source_sha: str | None = None) -> dict[str, Any]:
    files: dict[str, str] = {}
    for entry in sorted(release_path.rglob("*")):
        if entry.is_file() and entry.name != MANIFEST_NAME and ".runtime" not in entry.relative_to(release_path).parts:
            rel = str(entry.relative_to(release_path))
            files[rel] = _hash_file(entry)
    manifest: dict[str, Any] = {
        "created_at": _epoch_iso(),
        "file_count": len(files),
        "files": files,
    }
    if source_sha:
        manifest["source_sha"] = source_sha
    return manifest


def _write_manifest(release_path: Path, *, source_sha: str | None = None) -> dict[str, Any]:
    manifest = _build_manifest(release_path, source_sha=source_sha)
    (release_path / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def validate_manifest_at(release_path: Path) -> dict[str, Any]:
    manifest_path = release_path / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"valid": False, "error": f"{MANIFEST_NAME} not found"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"valid": False, "error": f"manifest parse error: {exc}"}
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        return {"valid": False, "error": "manifest files is not a dict"}
    for filepath, expected_sha in files.items():
        if not isinstance(filepath, str) or not isinstance(expected_sha, str):
            return {"valid": False, "error": "manifest file entries must be string pairs"}
        actual_path = (release_path / filepath).resolve()
        try:
            actual_path.relative_to(release_path.resolve())
        except ValueError:
            return {"valid": False, "error": f"manifest path escapes release: {filepath}"}
        if not actual_path.is_file():
            return {"valid": False, "error": f"file missing: {filepath}"}
        try:
            actual_sha = _hash_file(actual_path)
        except OSError as exc:
            return {"valid": False, "error": f"file unreadable: {filepath}: {exc}"}
        if actual_sha != expected_sha:
            return {
                "valid": False,
                "error": f"SHA mismatch: {filepath} (expected {expected_sha[:12]}, got {actual_sha[:12]})",
            }
    return {"valid": True, "file_count": len(files), "source_sha": manifest.get("source_sha")}


def _release_name_from_path(release_path: Path) -> str:
    return release_path.name


def _current_git_sha(repo_path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, ValueError):
        return "unknown"


def _git_release_files(repo_path: Path, candidate_sha: str | None) -> tuple[str, list[Path]]:
    actual_sha = _current_git_sha(repo_path)
    if actual_sha == "unknown":
        raise ValueError(f"deployment source is not a readable Git checkout: {repo_path}")
    if candidate_sha and candidate_sha != actual_sha:
        raise ValueError(
            f"candidate SHA {candidate_sha[:12]} does not match source HEAD {actual_sha[:12]}"
        )
    status = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if status.stdout.strip():
        raise ValueError("deployment source must be clean, including untracked files")
    tracked = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    files: list[Path] = []
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        path = repo_path / raw.decode("utf-8")
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(repo_path)
        except ValueError as exc:
            raise ValueError(f"tracked file escapes deployment source: {path}") from exc
        files.append(path)
    return actual_sha, files


# --- Service runner contract ---

@dataclass
class ServiceConfig:
    user_service_name: str = "agent-console-web.service"
    uvicorn_bin: str = "uvicorn"
    service_bind: str = "127.0.0.1"
    service_port: int = 3210
    canary_bind: str = "127.0.0.1"
    canary_port: int = 33100
    deployment_mode: DeploymentMode = DeploymentMode.DISABLED


class ServiceRunner(ABC):
    @abstractmethod
    def start_canary(self, release_path: Path, bind: str, port: int) -> bool:
        ...

    @abstractmethod
    def stop_canary(self) -> bool:
        ...

    @abstractmethod
    def check_health(self, *, port: int | None = None) -> bool:
        ...

    @abstractmethod
    def restart(self, *, service_name: str | None = None) -> bool:
        ...


class ProductionServiceRunner(ServiceRunner):
    def __init__(self, config: ServiceConfig | None = None):
        config = config or ServiceConfig()
        self.config = config
        self._deployment_mode = config.deployment_mode
        self._canary_process: subprocess.Popen | None = None
        self._canary_directory = None
        self._canary_identity = None
        self._canary_address = None
        self.expected_release = None

    def _check_mode(self) -> None:
        if self._deployment_mode != DeploymentMode.STAGING:
            raise RuntimeError(
                f"live service actions are disabled (mode={self._deployment_mode.value})"
            )

    def start_canary(self, release_path: Path, bind: str, port: int) -> bool:
        self._check_mode()
        if self._canary_process is not None:
            raise RuntimeError("a canary is already running")
        # A canary never listens on a public interface or shares a live socket.
        import ipaddress
        if not ipaddress.ip_address(bind).is_loopback:
            raise ValueError("canary bind must be a loopback address")
        try:
            with socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET) as probe:
                probe.bind((bind, port))
        except OSError:
            return False
        self._canary_directory = tempfile.TemporaryDirectory(prefix='agent-console-canary-')
        isolated = Path(self._canary_directory.name)
        self._canary_identity = secrets.token_hex(24)
        self._canary_address = (bind, port)
        # Allow only process essentials. No production credentials, DB references,
        # native harness homes, proxy configuration or workflow destinations.
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
        env.update({
            'HOME': str(isolated), 'TMPDIR': str(isolated),
            'PYTHONPATH': str(release_path),
            'AGENT_CONSOLE_STATE_DIR': str(isolated / 'state'),
            'AGENT_CONSOLE_CONFIG_DIR': str(isolated / 'config'),
            'AGENT_CONSOLE_DB': str(isolated / 'state/console.sqlite3'),
            'AGENT_CONSOLE_WORKSPACE_ROOT': str(isolated / 'workspace'),
            'AGENT_CONSOLE_WORKTREE_ROOT': str(isolated / 'worktrees'),
            'AGENT_CONSOLE_HANDOFF_DIR': str(isolated / 'handoffs'),
            'AGENT_CONSOLE_PROFILE_DIR': str(release_path / 'agent-profiles'),
            'AGENT_CONSOLE_TMUX_SOCKET_PATH': str(isolated / 'tmux.sock'),
            'AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH': str(isolated / 'legacy.sock'),
            'AGENT_CONSOLE_DEPLOYMENT_MODE': 'disabled',
            'AGENT_CONSOLE_CANARY': '1',
            'AGENT_CONSOLE_HEALTH_IDENTITY': self._canary_identity,
            'AGENT_CONSOLE_TRUSTED_HOSTS': 'localhost,127.0.0.1,::1',
            'AGENT_CONSOLE_LOG_DIR': str(isolated / 'logs'),
        })
        executable = release_path / '.runtime/bin/uvicorn'
        if not executable.is_file():
            executable = Path(self.config.uvicorn_bin)
        try:
            self._canary_process = subprocess.Popen(
                [str(executable), 'agent_console.web:app', '--host', bind,
                 '--port', str(port), '--app-dir', str(release_path), '--no-proxy-headers'],
                cwd=str(release_path), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                if self._canary_process.poll() is not None:
                    break
                if self.check_health(port=port):
                    return True
                time.sleep(1)
        except OSError:
            pass
        self.stop_canary()
        return False

    def stop_canary(self) -> bool:
        self._check_mode()
        if self._canary_process:
            if self._canary_process.poll() is None:
                self._canary_process.terminate()
                try:
                    self._canary_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._canary_process.kill()
                    self._canary_process.wait(timeout=10)
            self._canary_process = None
        if self._canary_directory:
            self._canary_directory.cleanup()
            self._canary_directory = None
        self._canary_identity = None
        self._canary_address = None
        return True

    def check_health(self, *, port: int | None = None) -> bool:
        self._check_mode()
        target_port = port or self.config.service_port
        is_canary = self._canary_address is not None and target_port == self._canary_address[1]
        if is_canary and (self._canary_process is None or self._canary_process.poll() is not None):
            return False
        target_host = self._canary_address[0] if is_canary else self.config.service_bind
        if target_host in {'0.0.0.0', '::'}:
            target_host = '127.0.0.1'
        authority = f'[{target_host}]' if ':' in target_host else target_host
        try:
            import httpx
            resp = httpx.get(f'http://{authority}:{target_port}/healthz', timeout=5, trust_env=False)
            if resp.status_code != 200 or resp.text.strip() != 'ok':
                return False
            if is_canary:
                return (resp.headers.get('X-Agent-Console-Identity') == self._canary_identity
                        and resp.headers.get('X-Agent-Console-Pid') == str(self._canary_process.pid)
                        and self._canary_process.poll() is None)
            if self.expected_release:
                from .maintenance import service_pid, process_belongs_to_service
                pid = service_pid(self.config.user_service_name)
                return (process_belongs_to_service(resp.headers.get('X-Agent-Console-Pid'), pid)
                        and resp.headers.get('X-Agent-Console-Release') == self.expected_release)
            return True
        except Exception:
            return False

    def restart(self, *, service_name: str | None = None) -> bool:
        self._check_mode()
        name = service_name or self.config.user_service_name
        log.info("service restart name=%s", name)
        try:
            from .maintenance import service_action
            service_action('restart', name=name)
            for _ in range(30):
                if self.check_health(port=self.config.service_port):
                    return True
                time.sleep(1)
            return False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, ValueError):
            return False


# --- Deployer ---

class Deployer:
    def __init__(
        self,
        releases_root: Path,
        runner: ServiceRunner,
        *,
        source_tracker: str | None = None,
        database_path: Path | None = None,
        config_dir: Path | None = None,
        state_dir: Path | None = None,
        prepare_runtime: bool = False,
    ):
        self.prepare_runtime = prepare_runtime
        self.database_path = database_path
        self.config_dir = config_dir
        self.state_dir = state_dir or (database_path.parent if database_path else None)
        self.releases_root = releases_root.resolve()
        self.runner = runner
        self.source_tracker = source_tracker
        self.releases_root.mkdir(parents=True, exist_ok=True, mode=0o755)

    # --- containment ---

    def _contained_release_name(self, name: str) -> str:
        name = str(name).strip()
        if not RELEASE_NAME_RE.fullmatch(name):
            raise ValueError(
                f"invalid release name {name!r}: must match {RELEASE_NAME_RE.pattern}"
            )
        return name

    def _release_path(self, release_name: str) -> Path:
        safe = self._contained_release_name(release_name)
        candidate = (self.releases_root / safe).resolve()
        try:
            candidate.relative_to(self.releases_root)
        except ValueError:
            raise ValueError(
                f"release path {candidate} escapes releases_root {self.releases_root}"
            )
        return candidate

    def _link_target(self, link_name: str) -> Path | None:
        with entrypoint_lock(self.state_dir or self.releases_root.parent) if link_name == CURRENT_LINK else nullcontext():
            return self._link_target_unlocked(link_name)

    def _link_target_unlocked(self, link_name: str) -> Path | None:
        link = self.releases_root / link_name
        if not link.is_symlink():
            return None
        target = link.resolve()
        if not target.is_dir():
            return None
        try:
            target.relative_to(self.releases_root)
        except ValueError:
            log.warning("link=%s target=%s escapes releases_root, removing", link_name, target)
            link.unlink()
            return None
        return target

    def _set_link(self, link_name: str, release_name: str) -> None:
        self._contained_release_name(release_name)
        guarded = link_name == CURRENT_LINK and self.database_path is not None
        with admission_lock(self.state_dir) if guarded else nullcontext(), \
                entrypoint_lock(self.state_dir or self.releases_root.parent) if link_name == CURRENT_LINK else nullcontext():
            if guarded:
                if self.config_dir is None:
                    raise ValueError("release schema guard requires configuration directory")
                prepare_database_for_release(
                    self.database_path, release_schema_version(self._release_path(release_name)),
                    self.config_dir,
                )
            link = self.releases_root / link_name
            tmp = self.releases_root / f".{link_name}.tmp"
            tmp.symlink_to(release_name)
            tmp.rename(link)
            if link_name == CURRENT_LINK and isinstance(self.runner, ProductionServiceRunner):
                self.runner.expected_release = release_name

    def _clear_link(self, link_name: str) -> None:
        with entrypoint_lock(self.state_dir or self.releases_root.parent) if link_name == CURRENT_LINK else nullcontext():
            link = self.releases_root / link_name
            if link.is_symlink():
                link.unlink()

    def _restore_current(self, previous: str | None) -> None:
        if previous:
            self._set_link(CURRENT_LINK, previous)
        else:
            self._clear_link(CURRENT_LINK)

    # --- core operations ---

    def create_release(
        self,
        source_dir: Path,
        *,
        candidate_sha: str | None = None,
    ) -> dict[str, Any]:
        source = source_dir.resolve()
        if not source.is_dir():
            raise ValueError(f"source directory does not exist: {source}")
        if self.source_tracker:
            sha, source_files = _git_release_files(source, candidate_sha)
        else:
            sha = candidate_sha or "nosha"
            source_files = [entry for entry in source.rglob("*") if entry.is_file()]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        release_name = f"release-{timestamp}-{sha[:12]}"
        release_path = self._release_path(release_name)
        if release_path.exists():
            raise FileExistsError(f"release already exists: {release_name}")
        release_path.mkdir(parents=True, mode=0o755)
        try:
            for entry in source_files:
                rel = str(entry.relative_to(source))
                if _is_excluded(rel):
                    continue
                dest = release_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, dest)

            has_app_dir = (source / "agent_console").is_dir()
            if has_app_dir:
                missing = [r for r in RUNTIME_ASSETS if not (source / r).is_file()]
                if missing:
                    raise RuntimeError(
                        f"missing required runtime assets: {missing}; "
                        "run `npm install` in the project root first"
                    )
                for asset_rel in RUNTIME_ASSETS:
                    asset_src = source / asset_rel
                    asset_dest = release_path / asset_rel
                    asset_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(asset_src, asset_dest)

            if self.prepare_runtime and has_app_dir:
                from .maintenance import prepare_runtime
                prepare_runtime(release_path)
            manifest = _write_manifest(release_path, source_sha=sha[:12])
            log.info(
                "release=%s files=%d source=%s sha=%s",
                release_name, manifest["file_count"], source, sha[:12],
            )
        except BaseException:
            shutil.rmtree(release_path, ignore_errors=True)
            raise
        return {
            "release_name": release_name,
            "release_path": str(release_path),
            "file_count": manifest["file_count"],
            "created_at": manifest["created_at"],
            "source_sha": manifest.get("source_sha"),
        }

    def validate_release(self, release_name: str) -> dict[str, Any]:
        release_path = self._release_path(release_name)
        if not release_path.is_dir():
            return {"valid": False, "error": f"release directory not found: {release_name}"}
        return validate_manifest_at(release_path)

    def select_release(self, release_name: str) -> dict[str, Any]:
        release_path = self._release_path(release_name)
        if not release_path.is_dir():
            raise ValueError(f"release not found: {release_name}")
        validation = self.validate_release(release_name)
        if not validation["valid"]:
            raise ValueError(
                f"release validation failed for {release_name}: {validation['error']}"
            )
        is_first = not self._link_target(CURRENT_LINK)
        previous = _release_name_from_path(self._link_target(CURRENT_LINK)) if self._link_target(CURRENT_LINK) else None
        self._set_link(CURRENT_LINK, release_name)
        log.info("release=%s selected previous=%s", release_name, previous or "(none)")
        return {
            "release_name": release_name,
            "release_path": str(release_path),
            "previous_release": previous,
            "is_first": is_first,
        }

    def promote_canary(
        self,
        release_name: str,
        *,
        bind: str | None = None,
        port: int | None = None,
    ) -> dict[str, Any]:
        release_path = self._release_path(release_name)
        if not release_path.is_dir():
            raise ValueError(f"release not found: {release_name}")
        validation = self.validate_release(release_name)
        if not validation["valid"]:
            raise ValueError(
                f"release validation failed for {release_name}: {validation['error']}"
            )
        config = getattr(self.runner, "config", None)
        actual_bind = bind or (config.canary_bind if config else "127.0.0.1")
        actual_port = port or (config.canary_port if config else 33100)
        if not self.runner.start_canary(release_path, actual_bind, actual_port):
            self.runner.stop_canary()
            raise RuntimeError(
                f"canary verification failed for {release_name}: "
                "isolated candidate did not become healthy"
            )
        if not self.runner.check_health(port=actual_port):
            self.runner.stop_canary()
            raise RuntimeError(
                f"canary health check failed for {release_name}: "
                "health check did not pass before canary link change"
            )
        self.runner.stop_canary()
        previous = _release_name_from_path(self._link_target(CANARY_LINK)) if self._link_target(CANARY_LINK) else None
        self._set_link(CANARY_LINK, release_name)
        log.info("release=%s canary verified+promoted previous=%s", release_name, previous or "(none)")
        return {
            "release_name": release_name,
            "release_path": str(release_path),
            "previous_canary": previous,
        }

    def _runner_service_port(self) -> int:
        cfg = getattr(self.runner, "config", None)
        if cfg is not None:
            return cfg.service_port
        return 3210

    def promote_user_service(
        self,
        release_name: str,
        *,
        service_name: str | None = None,
        health_port: int | None = None,
    ) -> dict[str, Any]:
        canary_target = self._link_target(CANARY_LINK)
        if canary_target is None:
            raise ValueError("no canary release selected; promote a canary first")
        canary_name = _release_name_from_path(canary_target)
        if canary_name != release_name:
            raise ValueError(
                f"release {release_name} is not the current canary ({canary_name}); "
                "promote the canary release to user-service"
            )
        release_path = self._release_path(release_name)
        validation = self.validate_release(release_name)
        if not validation["valid"]:
            raise ValueError(
                f"release validation failed for {release_name}: {validation['error']}"
            )
        previous = _release_name_from_path(self._link_target(CURRENT_LINK)) if self._link_target(CURRENT_LINK) else None
        previous_path = self._link_target(CURRENT_LINK)

        self._set_link(CURRENT_LINK, release_name)
        log.info("release=%s user-service (current) previous=%s", release_name, previous or "(none)")

        if not self.runner.restart(service_name=service_name):
            log.error("release=%s restart failed, rolling back to %s", release_name, previous or "(none)")
            self._restore_current(previous if previous_path else None)
            restored_healthy = True
            if previous and previous_path:
                restored_healthy = self.runner.restart(service_name=service_name)
                if restored_healthy:
                    restored_healthy = self.runner.check_health(port=self._runner_service_port())
            return {
                "release_name": release_name,
                "release_path": str(release_path),
                "previous_release": previous,
                "status": (
                    "restart_failed_unselected"
                    if previous is None
                    else (
                        "restart_failed_rolled_back"
                        if restored_healthy
                        else "restart_failed_rollback_unhealthy"
                    )
                ),
                "error": (
                    "service restart failed; no release remains selected"
                    if previous is None
                    else (
                        "service restart failed; rolled back to previous release"
                        if restored_healthy
                        else "service restart failed and restored release is unhealthy"
                    )
                ),
            }

        health_check_port = health_port or self._runner_service_port()
        if not self.runner.check_health(port=health_check_port):
            log.error("release=%s health check failed, rolling back to %s", release_name, previous or "(none)")
            self._restore_current(previous if previous_path else None)
            restored_healthy = True
            if previous and previous_path:
                self.runner.restart(service_name=service_name)
                restored_healthy = self.runner.check_health(port=health_check_port)
            return {
                "release_name": release_name,
                "release_path": str(release_path),
                "previous_release": previous,
                "status": (
                    "health_failed_unselected"
                    if previous is None
                    else (
                        "health_failed_rolled_back"
                        if restored_healthy
                        else "health_failed_rollback_unhealthy"
                    )
                ),
                "error": (
                    "service health check failed; no release remains selected"
                    if previous is None
                    else (
                        "service health check failed; rolled back to previous release"
                        if restored_healthy
                        else "service health check failed and restored release is unhealthy"
                    )
                ),
            }

        return {
            "release_name": release_name,
            "release_path": str(release_path),
            "previous_release": previous,
            "status": "user_service_active",
        }

    def rollback(
        self,
        target: str = "previous",
        *,
        service_name: str | None = None,
    ) -> dict[str, Any]:
        current = self._link_target(CURRENT_LINK)
        if current is None:
            raise ValueError("no current release selected")
        current_name = _release_name_from_path(current)
        releases = self.list_releases()
        if not releases:
            raise ValueError("no releases to rollback")
        if target == "previous":
            idx = next(
                (i for i, r in enumerate(releases) if r["release_name"] == current_name),
                None,
            )
            if idx is None or idx + 1 >= len(releases):
                raise ValueError("no previous release to rollback to")
            target_name = releases[idx + 1]["release_name"]
        else:
            self._contained_release_name(target)
            target_name = target

        target_path = self._release_path(target_name)
        if not target_path.is_dir():
            raise ValueError(f"target release not found: {target_name}")
        validation = self.validate_release(target_name)
        if not validation["valid"]:
            raise ValueError(
                f"release validation failed for {target_name}: {validation['error']}"
            )

        self._set_link(CURRENT_LINK, target_name)
        log.info("release=%s rollback from=%s", target_name, current_name)

        if not self.runner.restart(service_name=service_name):
            log.error("release=%s restart after rollback failed, restoring %s", target_name, current_name)
            self._set_link(CURRENT_LINK, current_name)
            restored_healthy = self.runner.restart(service_name=service_name)
            if restored_healthy:
                restored_healthy = self.runner.check_health(port=self._runner_service_port())
            return {
                "release_name": target_name,
                "release_path": str(target_path),
                "status": (
                    "rollback_restart_failed_restored"
                    if restored_healthy
                    else "rollback_restart_failed_restore_unhealthy"
                ),
                "error": (
                    f"service restart after rollback failed; restored {current_name}"
                    if restored_healthy
                    else f"service restart after rollback failed and restored {current_name} is unhealthy"
                ),
            }

        health_port = self._runner_service_port()
        if not self.runner.check_health(port=health_port):
            log.error("release=%s health check after rollback failed, restoring %s", target_name, current_name)
            self._set_link(CURRENT_LINK, current_name)
            self.runner.restart(service_name=service_name)
            restored_healthy = self.runner.check_health(port=health_port)
            return {
                "release_name": target_name,
                "release_path": str(target_path),
                "previous_release": current_name,
                "status": (
                    "rollback_health_failed_restored"
                    if restored_healthy
                    else "rollback_health_failed_restore_unhealthy"
                ),
                "error": (
                    f"health check failed after rollback; restored {current_name}"
                    if restored_healthy
                    else f"health check failed after rollback and restored {current_name} is unhealthy"
                ),
            }

        return {
            "release_name": target_name,
            "release_path": str(target_path),
            "previous_release": current_name,
            "status": "rollback_applied",
        }

    def current_release(self) -> dict[str, Any] | None:
        current = self._link_target(CURRENT_LINK)
        if current is None:
            return None
        release_name = _release_name_from_path(current)
        validation = self.validate_release(release_name)
        return {
            "release_name": release_name,
            "release_path": str(current),
            "valid": validation.get("valid", False),
            "validation_error": validation.get("error"),
        }

    def canary_release(self) -> dict[str, Any] | None:
        canary = self._link_target(CANARY_LINK)
        if canary is None:
            return None
        return {
            "release_name": _release_name_from_path(canary),
            "release_path": str(canary),
        }

    def list_releases(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        current_name = (
            _release_name_from_path(self._link_target(CURRENT_LINK))
            if self._link_target(CURRENT_LINK)
            else None
        )
        canary_name = (
            _release_name_from_path(self._link_target(CANARY_LINK))
            if self._link_target(CANARY_LINK)
            else None
        )
        for path in sorted(self.releases_root.iterdir(), reverse=True):
            if not path.is_dir() or not path.name.startswith("release-"):
                continue
            manifest_path = path / MANIFEST_NAME
            manifest: dict[str, Any] = {}
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            result.append({
                "release_name": path.name,
                "release_path": str(path),
                "file_count": manifest.get("file_count", 0),
                "created_at": manifest.get("created_at"),
                "source_sha": manifest.get("source_sha"),
                "is_current": path.name == current_name,
                "is_canary": path.name == canary_name,
            })
        return result


# --- Test-only fake runner (must be explicitly injected, never production default) ---

class FakeServiceRunner(ServiceRunner):
    def __init__(self, *, health_ok: bool = True, restart_ok: bool = True, canary_ok: bool = True):
        self.health_ok = health_ok
        self.restart_ok = restart_ok
        self.canary_ok = canary_ok
        self.health_calls: list[int | None] = []
        self.restart_calls: list[str | None] = []
        self.canary_starts: list[tuple[Path, str, int]] = []
        self.canary_stops: list[bool] = []

    def start_canary(self, release_path: Path, bind: str, port: int) -> bool:
        self.canary_starts.append((release_path, bind, port))
        return self.canary_ok

    def stop_canary(self) -> bool:
        self.canary_stops.append(True)
        return True

    def check_health(self, *, port: int | None = None) -> bool:
        self.health_calls.append(port)
        return self.health_ok

    def restart(self, *, service_name: str | None = None) -> bool:
        self.restart_calls.append(service_name)
        return self.restart_ok
