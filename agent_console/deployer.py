from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
CURRENT_LINK = "current"
CANARY_LINK = "canary"
RELEASE_NAME_RE = re.compile(r"^release-[A-Za-z0-9._-]+$")
LIVE_ACTIONS_FORBIDDEN = True

EXCLUDED_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".egg-info"})
EXCLUDED_FILE_NAMES = frozenset({".env", ".env.local", ".env.production"})


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
        if entry.is_file() and entry.name != MANIFEST_NAME:
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
        actual_path = release_path / filepath
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


# --- Service runner contract ---

@dataclass
class ServiceConfig:
    user_service_name: str = "agent-console-web.service"
    canary_bind: str = "127.0.0.1"
    canary_port_range: tuple[int, int] = (33100, 33199)
    user_service_port: int = 3210


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
        self.config = config or ServiceConfig()
        self._canary_process: subprocess.Popen | None = None

    def start_canary(self, release_path: Path, bind: str, port: int) -> bool:
        if LIVE_ACTIONS_FORBIDDEN:
            raise RuntimeError("live service actions are forbidden during this task")
        log.info("canary start release=%s bind=%s port=%d", release_path.name, bind, port)
        try:
            self._canary_process = subprocess.Popen(
                ["uvicorn", "agent_console.web:app",
                 "--host", bind, "--port", str(port),
                 "--app-dir", str(release_path)],
                cwd=str(release_path),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                if self.check_health(port=port):
                    return True
                if self._canary_process.poll() is not None:
                    return False
                time.sleep(1)
            return False
        except OSError:
            return False

    def stop_canary(self) -> bool:
        if LIVE_ACTIONS_FORBIDDEN:
            raise RuntimeError("live service actions are forbidden during this task")
        if self._canary_process:
            self._canary_process.terminate()
            try:
                self._canary_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._canary_process.kill()
            self._canary_process = None
        return True

    def check_health(self, *, port: int | None = None) -> bool:
        if LIVE_ACTIONS_FORBIDDEN:
            raise RuntimeError("live service actions are forbidden during this task")
        target_port = port or self.config.user_service_port
        try:
            import httpx
            resp = httpx.get(f"http://127.0.0.1:{target_port}/healthz", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def restart(self, *, service_name: str | None = None) -> bool:
        if LIVE_ACTIONS_FORBIDDEN:
            raise RuntimeError("live service actions are forbidden during this task")
        name = service_name or self.config.user_service_name
        log.info("service restart name=%s", name)
        try:
            result = subprocess.run(
                ["systemctl", "--user", "restart", name],
                capture_output=True, timeout=30,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False


# --- Deployer ---

class Deployer:
    def __init__(
        self,
        releases_root: Path,
        runner: ServiceRunner,
        *,
        source_tracker: str | None = None,
    ):
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
        link = self.releases_root / link_name
        tmp = self.releases_root / f".{link_name}.tmp"
        tmp.symlink_to(release_name)
        tmp.rename(link)

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
        sha = candidate_sha or (_current_git_sha(source) if self.source_tracker else "nosha")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        release_name = f"release-{timestamp}-{sha[:12]}"
        release_path = self._release_path(release_name)
        if release_path.exists():
            raise FileExistsError(f"release already exists: {release_name}")
        release_path.mkdir(parents=True, mode=0o755)
        try:
            for entry in source.rglob("*"):
                if not entry.is_file():
                    continue
                rel = str(entry.relative_to(source))
                if _is_excluded(rel):
                    continue
                dest = release_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, dest)
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
        actual_bind = bind or "127.0.0.1"
        actual_port = port or 33100
        if not self.runner.start_canary(release_path, actual_bind, actual_port):
            self.runner.stop_canary()
            raise RuntimeError(
                f"canary verification failed for {release_name}: "
                "isolated candidate did not become healthy"
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
            if previous and previous_path:
                self._set_link(CURRENT_LINK, previous)
                self.runner.restart(service_name=service_name)
            return {
                "release_name": release_name,
                "release_path": str(release_path),
                "previous_release": previous,
                "status": "restart_failed_rolled_back",
                "error": "service restart failed; rolled back to previous release",
            }

        if not self.runner.check_health(port=health_port or 3210):
            log.error("release=%s health check failed, rolling back to %s", release_name, previous or "(none)")
            if previous and previous_path:
                self._set_link(CURRENT_LINK, previous)
                self.runner.restart(service_name=service_name)
            return {
                "release_name": release_name,
                "release_path": str(release_path),
                "previous_release": previous,
                "status": "health_failed_rolled_back",
                "error": "service health check failed; rolled back to previous release",
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
            self.runner.restart(service_name=service_name)
            return {
                "release_name": target_name,
                "release_path": str(target_path),
                "status": "rollback_restart_failed_restored",
                "error": f"service restart after rollback failed; restored {current_name}",
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
