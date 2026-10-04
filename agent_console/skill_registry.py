"""Portable skill packages, hash-bound policy receipts and immutable delivery.

Policy receipts are a versioned, owner-only JSON registry beside the Console DB.
They are serialized independently of the session admission lock; readers never
create state. Skill scripts are inspected as files and are never executed here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from urllib.parse import urlsplit
import uuid
from typing import Any, Iterator

import yaml

from .database import utc_now

ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{16,}|"
    r"(?im:^\s*(?:[A-Z_]*(?:TOKEN|PASSWORD|API_KEY|SECRET))\s*[=:]\s*['\"]?(?!\$|\{|<|example|test|placeholder|none|null)[A-Za-z0-9+/_.-]{12,})"
)
FIELDS = {"version", "source", "revision", "scope", "project", "compatible_harnesses", "compatible_profiles", "risk", "approval", "required_binaries", "required_services", "scripts"}
MAX_FILE = 2 * 1024 * 1024
MAX_PACKAGE = 16 * 1024 * 1024


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueLoader, node: yaml.Node, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("skill YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def frontmatter(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_FILE:
        raise ValueError("SKILL.md exceeds the file limit")
    return _parse_frontmatter(path.read_text(encoding="utf-8"))


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=2)
    if len(parts) != 3:
        raise ValueError("SKILL.md has an unterminated frontmatter block")
    try:
        parsed = yaml.load(parts[1], Loader=UniqueLoader)
    except (yaml.YAMLError, ValueError, RecursionError):
        raise ValueError("invalid or duplicate skill YAML metadata") from None
    if not isinstance(parsed, dict):
        raise ValueError("skill frontmatter must be a mapping")
    return parsed


def clean(value: str) -> str:
    return SECRET.sub("[redacted]", value)


def _strings(value: Any, field: str, *, legacy: bool = False) -> list[str]:
    if value is None:
        return []
    if legacy and isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list) or any(not isinstance(item, str) or len(item) > 256 for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return list(dict.fromkeys(value))


def inspect_package(directory: Path, *, name: str | None = None) -> dict[str, Any]:
    """Return value-blind validation and content identity, including supporting files."""
    name = name or directory.name
    issues: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {"name": name, "hash": None, "revision": None, "source": str(directory), "scope": "global", "risk": "low", "approval": "allow", "compatible_harnesses": [], "compatible_profiles": [], "required_binaries": [], "required_services": [], "scripts": [], "description": "", "native_id": name, "issues": issues, "warnings": warnings, "files": [], "manifest_version": "legacy"}
    result.update(declared_source=None, declared_revision=None)
    if not ID.fullmatch(name):
        issues.append("invalid skill id")
        return result
    if directory.is_symlink() or not directory.is_dir():
        issues.append("skill directory must be a real directory")
        return result
    try:
        root = directory.resolve(strict=True)
        entries: list[tuple[str, bytes]] = []
        total = 0
        modes: dict[str, int] = {}
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
            for leaf in dirs + sorted(files):
                path = Path(current) / leaf
                rel = path.relative_to(root).as_posix()
                if path.is_symlink():
                    issues.append(f"symlink is not permitted in a skill package: {rel}")
                    continue
                mode = path.stat().st_mode
                if stat.S_ISDIR(mode):
                    continue
                if not stat.S_ISREG(mode):
                    issues.append(f"non-regular package entry: {rel}")
                    continue
                size = path.stat().st_size
                total += size
                if size > MAX_FILE or total > MAX_PACKAGE or len(entries) >= 512:
                    raise ValueError("skill package exceeds file/count/size limits")
                content, mode = _read_regular(root, rel)
                modes[rel] = mode
                if len(content) > MAX_FILE:
                    raise ValueError("skill file changed beyond size limit")
                if SECRET.search(content.decode("utf-8", errors="ignore")):
                    issues.append(f"possible secret material in {rel}; values withheld")
                entries.append((rel, content))
        if not any(rel == "SKILL.md" for rel, _ in entries):
            raise ValueError("SKILL.md is required")
        digest = hashlib.sha256()
        for rel, content in sorted(entries):
            digest.update(rel.encode() + b"\0" + str(modes[rel] & 0o111).encode() + b"\0" + hashlib.sha256(content).digest())
        result["hash"] = digest.hexdigest()
        result["files"] = [rel for rel, _ in sorted(entries)]
        contents = dict(entries)
        meta = _parse_frontmatter(contents["SKILL.md"].decode("utf-8"))
        metadata = meta.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a mapping")
        policy = {key.removeprefix("agent-console/"): value for key, value in metadata.items() if key.startswith("agent-console/")}
        sidecar = root / "agent-console.json"
        if "agent-console.json" in contents:
            if sidecar.is_symlink():
                raise ValueError("skill policy sidecar cannot be a symlink")
            side = json.loads(contents["agent-console.json"].decode("utf-8"))
            if not isinstance(side, dict):
                raise ValueError("skill policy sidecar must be an object")
            if set(side) & set(policy):
                raise ValueError("duplicate policy fields in frontmatter and sidecar")
            policy.update(side)
        if set(policy) - FIELDS:
            raise ValueError("unknown Agent Console policy fields")
        modern = bool(policy)
        if modern and str(policy.get("version")) != "1":
            raise ValueError("Agent Console skill policy version must be 1")
        if modern and (not isinstance(meta.get("name"), str) or not isinstance(meta.get("description"), str) or not meta["description"].strip()):
            raise ValueError("portable skills require name and description strings")
        for field in ("name", "description"):
            if field in meta and not isinstance(meta[field], str):
                raise ValueError(f"{field} must be a string")
        result["native_id"] = meta.get("name", name)
        if not ID.fullmatch(result["native_id"]):
            raise ValueError("invalid native skill id")
        result["description"] = clean(meta.get("description", ""))[:2000]
        result["manifest_version"] = "1" if modern else "legacy"
        if not modern:
            warnings.append("Legacy policy: migrate top-level tools/allowed_profiles/kind to namespaced metadata or agent-console.json")
        for field in ("source", "revision", "project"):
            if field in policy:
                if not isinstance(policy[field], str) or len(policy[field]) > 2000 or SECRET.search(policy[field]):
                    raise ValueError(f"{field} must be bounded text without credential material")
                if field == "source":
                    parsed = urlsplit(policy[field])
                    if parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise ValueError("source provenance cannot contain credentials or query parameters")
                result[field] = policy[field]
                if field in {'source', 'revision'}:
                    result['declared_' + field] = policy[field]
        result["revision"] = result["revision"] or result["hash"][:12]
        for field, choices, default in (("scope", {"global", "project"}, "global"), ("risk", {"low", "elevated"}, "elevated" if meta.get("kind") == "superpower" else "low"), ("approval", {"allow", "ask", "deny"}, "ask" if meta.get("requires_approval") is True or str(meta.get("requires_approval", "")).lower() in {"true", "yes", "1"} else "allow")):
            value = policy.get(field, default)
            if value not in choices:
                raise ValueError(f"invalid {field} policy")
            result[field] = value
        if result["scope"] == "project" and (not result.get("project") or not Path(result["project"]).is_absolute()):
            raise ValueError("project-scoped skill requires an absolute project root")
        for field, legacy_field in (("compatible_harnesses", "tools"), ("compatible_profiles", "allowed_profiles")):
            result[field] = _strings(policy.get(field, meta.get(legacy_field)), field, legacy=field not in policy)
        for field in ("required_binaries", "required_services", "scripts"):
            result[field] = _strings(policy.get(field), field)
        for binary in result["required_binaries"]:
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", binary):
                raise ValueError("required binaries must be executable names, not commands or paths")
            if shutil.which(binary) is None:
                issues.append(f"required binary unavailable: {binary}")
        for service in result["required_services"]:
            # A service is a requirement, never an instruction to start/install it.
            warnings.append(f"service readiness requires operator verification: {service}")
        for rel, _ in entries:
            mode = modes[rel]
            if mode & 0o111 and not rel.startswith("scripts/") and rel not in result["scripts"]:
                issues.append(f"unexpected executable; declare it in scripts: {rel}")
        for rel in result["scripts"]:
            if rel not in result["files"]:
                issues.append(f"declared script is missing: {rel}")
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError):
        # Parser exceptions can contain source excerpts. Never propagate them.
        issues.append("invalid or unreadable skill package; check metadata, paths and package limits")
    return result


class SkillRegistry:
    def __init__(self, root: Path, state: Path):
        self.root, self.state = root, state
        self.path = state / "skill-policy.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "reviews": {}, "approvals": {}, "imports": {}}
        data = json.loads(self.path.read_text())
        if data.get("version") != 1:
            raise ValueError("unsupported skill registry version")
        return data

    @contextmanager
    def edit(self) -> Iterator[dict]:
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.state / "skill-policy.lock").open("a+") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            yield data
            fd, temp = tempfile.mkstemp(prefix=".skill-policy-", dir=self.state)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(data, stream, sort_keys=True, indent=2)
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp): os.unlink(temp)

    def inspect(self, name: str) -> dict:
        if not ID.fullmatch(name):
            raise ValueError("invalid skill id")
        result = inspect_package(self.root / name, name=name)
        state = self.read()
        review = state["reviews"].get(name)
        result["trust"] = "local-trusted"
        if review:
            result["trust"] = review["decision"] if review["hash"] == result["hash"] else "imported-unreviewed" if review.get("imported") else "review-required"
            result["review"] = review
            imported = state['imports'].get(review.get('import_id'))
            if imported:
                self._provenance(result, imported)
        result["validation"] = "invalid" if result["issues"] else "valid"
        return result

    def decide(self, name: str, *, decision: str, expected_hash: str, actor: str, services_verified: bool = False) -> dict:
        if decision not in {"reviewed", "blocked"}:
            raise ValueError("decision must be reviewed or blocked")
        package = self.inspect(name)
        if package["hash"] != expected_hash or not expected_hash:
            raise ValueError("skill content changed; inspect the current revision first")
        if decision == "reviewed" and package["issues"]:
            raise ValueError("invalid skill cannot be reviewed for activation")
        if decision == "reviewed" and package["required_services"] and not services_verified:
            raise ValueError("verify declared services before approving this revision")
        with self.edit() as data:
            previous = data["reviews"].get(name, {})
            imported = previous.get("imported", False)
            data["reviews"][name] = {"hash": expected_hash, "decision": decision, "actor": actor, "at": utc_now(), "imported": imported, "services_verified": services_verified}
            if previous.get('import_id'):
                data['reviews'][name]['import_id'] = previous['import_id']
        return self.inspect(name)

    def approve(self, name: str, profile: str, *, expected_hash: str, actor: str) -> dict:
        from .validation import PROFILES
        if profile not in PROFILES:
            raise ValueError("unknown profile")
        package = self.inspect(name)
        if package["hash"] != expected_hash or package["issues"] or package["trust"] not in {"local-trusted", "reviewed"}:
            raise ValueError("inspect and review the current valid skill revision before approval")
        with self.edit() as data:
            receipt = {"hash": expected_hash, "actor": actor, "at": utc_now()}
            data["approvals"][f"{profile}/{name}"] = receipt
        return receipt

    def revoke(self, name: str, profile: str) -> None:
        with self.edit() as data:
            data["approvals"].pop(f"{profile}/{name}", None)

    def explain(self, name: str, profile: str, tool: str | None = None, repository: str | None = None) -> dict:
        package = self.inspect(name)
        reasons = list(package["issues"])
        if package["trust"] not in {"local-trusted", "reviewed"}:
            reasons.append(f"trust state: {package['trust']}")
        if package["approval"] == "deny": reasons.append("skill policy denies access")
        if package["compatible_profiles"] and profile not in package["compatible_profiles"]:
            reasons.append("profile is incompatible")
        if tool and package["compatible_harnesses"] and tool not in package["compatible_harnesses"]:
            reasons.append("harness is incompatible")
        if package["scope"] == "project":
            try:
                if not repository: raise ValueError()
                Path(repository).resolve().relative_to(Path(package["project"]).resolve())
            except (ValueError, KeyError): reasons.append("outside the declared project scope")
        if package["required_services"] and not package.get("review", {}).get("services_verified"):
            reasons.append("declared services have not been verified for this content hash")
        receipt = self.read()["approvals"].get(f"{profile}/{name}")
        needs_approval = package["approval"] == "ask" and (not receipt or receipt["hash"] != package["hash"])
        package["effective_policy"] = "deny" if reasons else "ask" if needs_approval else "allow"
        package["reasons"] = reasons or (["requires explicit human approval for this content hash"] if needs_approval else [])
        package["approval_receipt"] = receipt
        return package

    def stage(self, source: Path) -> dict:
        if SECRET.search(str(source)):
            raise ValueError('import path contains possible credential material')
        return self._stage(source, {'kind': 'local', 'source': str(source)})

    def stage_git(self, source: str, *, revision: str = 'HEAD', subdirectory: str = '') -> dict:
        from .skill_git import git_package
        with git_package(source, revision, subdirectory) as (package, provenance):
            return self._stage(package, provenance)

    @staticmethod
    def _provenance(package: dict, record: dict) -> dict:
        provenance = record.get('provenance')
        if provenance:
            package['provenance'] = provenance
            package['provenance_content_matches'] = package['hash'] == record['hash']
            package['source'] = provenance['source']
            package['revision'] = provenance.get('revision', package['revision'])
        return package

    def _stage(self, source: Path, provenance: dict) -> dict:
        package = inspect_package(source)
        if package["issues"]: raise ValueError("import rejected: " + "; ".join(package["issues"]))
        provenance = {**provenance, 'content_hash': package['hash']}
        identifier = "import-" + uuid.uuid4().hex
        target = self.state / "skill-imports" / identifier / package["name"]
        target.parent.mkdir(parents=True, mode=0o700)
        _copy_validated(source, target, package)
        if inspect_package(target)["hash"] != package["hash"]:
            shutil.rmtree(target.parent)
            raise ValueError("skill changed during import; retry after source is stable")
        record = {"id": identifier, "name": package["name"], "hash": package["hash"], "source": provenance['source'], "provenance": provenance, "status": "imported-unreviewed", "at": utc_now()}
        with self.edit() as data: data["imports"][identifier] = record
        return record

    def imports(self) -> list[dict]:
        return list(self.read()["imports"].values())

    def inspect_import(self, identifier: str) -> dict:
        record = self.read()["imports"].get(identifier)
        if not record: raise KeyError("unknown staged skill import")
        package = inspect_package(self.state / "skill-imports" / identifier / record["name"])
        return {**record, "staged_path": str(self.state / "skill-imports" / identifier / record["name"]), "package": self._provenance(package, record)}

    def activate(self, identifier: str, *, expected_hash: str, actor: str, services_verified: bool = False) -> dict:
        staged = self.inspect_import(identifier)
        package = staged["package"]
        if not expected_hash or package["hash"] != expected_hash or staged["hash"] != expected_hash or package["issues"]:
            raise ValueError("import content changed or is invalid; stage and inspect it again")
        if package["required_services"] and not services_verified:
            raise ValueError("verify declared services before activation")
        source = self.state / "skill-imports" / identifier / staged["name"]
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / staged["name"]
        # Persist an unreviewed receipt before exposing any canonical files.
        # A crash at either boundary remains deny-by-default on the next launch.
        with self.edit() as data:
            if target.exists() or target.is_symlink(): raise ValueError("skill id already exists; existing content is preserved")
            data["reviews"][staged["name"]] = {"hash": expected_hash, "decision": "imported-unreviewed", "actor": actor, "at": utc_now(), "imported": True, "import_id": identifier, "services_verified": False}
        _copy_validated(source, target, package)
        if inspect_package(target)["hash"] != expected_hash:
            shutil.rmtree(target)
            raise ValueError("import changed during activation")
        with self.edit() as data:
            data["reviews"][staged["name"]] = {"hash": expected_hash, "decision": "reviewed", "actor": actor, "at": utc_now(), "imported": True, "import_id": identifier, "services_verified": services_verified}
            data["imports"][identifier]["status"] = "activated"
        return self.inspect(staged["name"])


def snapshot_skill(source: Path, target: Path, expected_hash: str | None = None) -> dict:
    package = inspect_package(source)
    if package["issues"] or (expected_hash and package["hash"] != expected_hash):
        raise ValueError("skill is invalid or changed after launch preview")
    if target.exists() or target.is_symlink():
        raise ValueError("skill snapshot target already exists")
    _copy_validated(source, target, package)
    copied = inspect_package(target, name=source.name)
    if copied["hash"] != package["hash"]:
        shutil.rmtree(target)
        raise ValueError("skill changed during materialization")
    return {"name": source.name, "hash": package["hash"], "revision": package["revision"], "source": str(source), "materialized_at": str(target), "delivered_at": utc_now()}


def _copy_validated(source: Path, target: Path, package: dict) -> None:
    """Copy only checked regular files, without following swapped symlinks."""
    target.mkdir(mode=0o700)
    try:
        for rel in package["files"]:
            directory_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parts = Path(rel).parts
                for part in parts[:-1]:
                    child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    os.close(directory_fd); directory_fd = child_fd
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as stream:
                    metadata = os.fstat(stream.fileno())
                    if not stat.S_ISREG(metadata.st_mode): raise ValueError("non-regular skill file")
                    content = stream.read(MAX_FILE + 1)
                    if len(content) > MAX_FILE or SECRET.search(content.decode("utf-8", errors="ignore")):
                        raise ValueError("skill changed or contains possible credential material")
            finally:
                os.close(directory_fd)
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(content)
            destination.chmod(stat.S_IMODE(metadata.st_mode) & 0o777)
        copied = inspect_package(target, name=package["name"])
        if copied["issues"] or copied["hash"] != package["hash"]:
            raise ValueError("skill changed while copying its snapshot")
    except BaseException:
        shutil.rmtree(target)
        raise


def record_delivery(state: Path, session_id: str, isolated_root: Path, *, tool: str, profile: str, isolated: bool) -> dict:
    """Keep launch receipts after transient snapshots and terminal sessions are removed."""
    if not ID.fullmatch(session_id):
        raise ValueError("invalid session id")
    manifest = json.loads((isolated_root / "delivery.json").read_text())
    receipt = {**manifest, "session_id": session_id, "tool": tool, "profile": profile,
               "isolated": isolated, "at": utc_now(), "created_ns": time.time_ns(), "id": uuid.uuid4().hex,
               "coverage": "Console-selected skills only; native/plugin/project discovery can add other skills"}
    directory = state / "skill-deliveries" / session_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".delivery-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, directory / (str(receipt["created_ns"]) + "-" + receipt["id"] + ".json"))
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return receipt


def read_deliveries(state: Path, session_id: str) -> dict:
    if not ID.fullmatch(session_id):
        raise ValueError("invalid session id")
    directory = state / "skill-deliveries" / session_id
    receipts = [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))] if directory.is_dir() else []
    return {"session_id": session_id, "deliveries": receipts,
            "latest": receipts[-1] if receipts else None,
            "notice": "Recorded Console delivery, not a claim that the model used each skill." if receipts else "This session predates delivery receipts; its actual skill set is unknown."}


def _read_regular(root: Path, relative: str) -> tuple[bytes, int]:
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd); directory_fd = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            mode = os.fstat(stream.fileno()).st_mode
            if not stat.S_ISREG(mode): raise ValueError("non-regular skill file")
            content = stream.read(MAX_FILE + 1)
            if len(content) > MAX_FILE: raise ValueError("skill file exceeds limit")
            return content, mode
    finally:
        os.close(directory_fd)
