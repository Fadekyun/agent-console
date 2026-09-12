from __future__ import annotations

import hashlib
import html
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import Database, utc_now
from .integration_requests import ARTIFACT_MAX_BYTES, _no_duplicates
from .providers import provider_adapter
from .auth import AuthRegistry


REQUEST_UUID = re.compile(r"^ireq-[0-9a-f]{32}$")
EVENT_LINE_MAX = 1_048_576
DIAGNOSTIC_MAX = 16_777_216
ACK_TIMEOUT_SECONDS = 120
RUN_TIMEOUT_SECONDS = 1800
KILL_GRACE_SECONDS = 10
FINAL_FIELDS = frozenset({"outcome", "summary", "steps", "verification", "blockers"})
BOOT_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _current_boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeDecodeError):
        return None
    return value if BOOT_ID_PATTERN.fullmatch(value) else None


def _proc_start_time(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        end = text.rfind(")")
        if end < 0:
            return None
        fields = text[end + 2:].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError, UnicodeDecodeError):
        return None


def process_identity_matches(
    pid: int,
    start_time: str | None,
    pgid: int | None,
    boot_id: str | None,
) -> bool:
    if (
        not isinstance(pid, int)
        or pid <= 1
        or not start_time
        or pgid != pid
        or not boot_id
        or _current_boot_id() != boot_id
    ):
        return False
    if _proc_start_time(pid) != start_time:
        return False
    try:
        return os.getpgid(pid) == pgid
    except (ProcessLookupError, PermissionError):
        return False


def _active_group_members(pgid: int) -> dict[int, str] | None:
    members: dict[int, str] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text(encoding="ascii")
            end = text.rfind(")")
            if end < 0:
                return None
            fields = text[end + 2:].split()
            if fields[0] == "Z" or int(fields[2]) != pgid:
                continue
            members[int(entry.name)] = fields[19]
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, ValueError, IndexError):
            return None
    return members


def terminate_owned_group(
    pid: int,
    start_time: str | None,
    pgid: int | None,
    boot_id: str | None,
    *,
    grace_seconds: float = KILL_GRACE_SECONDS,
) -> bool:
    if not process_identity_matches(pid, start_time, pgid, boot_id):
        return False
    owned_members = _active_group_members(pgid)
    if owned_members is None or owned_members.get(pid) != start_time:
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return _active_group_members(pgid) == {}
    except PermissionError:
        return False
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        members = _active_group_members(pgid)
        if members == {}:
            return True
        if members is None:
            return False
        time.sleep(0.05)
    members = _active_group_members(pgid)
    if members is None:
        return False
    if members and all(
        owned_members.get(member_pid) == member_start
        for member_pid, member_start in members.items()
    ):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + min(grace_seconds, 2.0)
    while time.monotonic() < deadline:
        members = _active_group_members(pgid)
        if members == {}:
            return True
        if members is None:
            return False
        time.sleep(0.05)
    return _active_group_members(pgid) == {}


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(FINAL_FIELDS),
        "properties": {
            "outcome": {"type": "string", "enum": ["plan", "needs_input"]},
            "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
        },
    }


def _validate_final(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != FINAL_FIELDS:
        raise ValueError("invalid final fields")
    if value["outcome"] not in {"plan", "needs_input"}:
        raise ValueError("invalid outcome")
    if not isinstance(value["summary"], str) or len(value["summary"]) > 32_000:
        raise ValueError("invalid summary")
    for key in ("steps", "verification", "blockers"):
        items = value[key]
        if not isinstance(items, list) or len(items) > 256:
            raise ValueError("invalid list")
        if any(not isinstance(item, str) or len(item) > 32_000 for item in items):
            raise ValueError("invalid list item")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > ARTIFACT_MAX_BYTES:
        raise ValueError("final too large")
    return value


def _atomic_write(path: Path, raw: bytes) -> None:
    if len(raw) > ARTIFACT_MAX_BYTES:
        raise ValueError("artifact too large")
    if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError("unsafe artifact target")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _render_markdown(value: dict[str, Any]) -> bytes:
    def safe(text: str) -> str:
        return html.escape(text, quote=True).replace("://", ":\\/\\/")

    lines = ["# Planning result", "", safe(value["summary"]), ""]
    for title, key in (("Steps", "steps"), ("Verification", "verification"), ("Blockers", "blockers")):
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {safe(item)}" for item in value[key])
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _update(database: Database, request_uuid: str, fields: dict[str, Any],
            *, expected: tuple[str, ...] | None = None) -> bool:
    fields = {**fields, "updated_at": utc_now()}
    assignments = ", ".join(f"{key}=?" for key in fields)
    params: list[Any] = list(fields.values()) + [request_uuid]
    where = "id=?"
    if expected:
        where += " AND state IN (" + ",".join("?" for _ in expected) + ")"
        params.extend(expected)
    with database.connect() as conn:
        result = conn.execute(
            f"UPDATE integration_requests SET {assignments}, revision=revision+1 WHERE {where}",
            params,
        )
    return result.rowcount == 1


def _clean_provider_environment(job_home: Path, codex_home: Path) -> dict[str, str]:
    return {
        "HOME": str(job_home),
        "CODEX_HOME": str(codex_home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _prepare_provider(settings: Settings, row: Any) -> tuple[list[str], dict[str, str], Path]:
    artifact_dir = Path(row["artifact_dir"])
    frozen_dir = artifact_dir / "context"
    if artifact_dir.is_symlink() or frozen_dir.is_symlink() or not frozen_dir.is_dir():
        raise RuntimeError("unsafe frozen context")
    schema_path = artifact_dir / "output-schema.json"
    _atomic_write(schema_path, json.dumps(_schema(), sort_keys=True, separators=(",", ":")).encode())
    job_home = artifact_dir / "home"
    codex_home = job_home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry = AuthRegistry(settings.config_dir or settings.state_dir / "config")
    context = registry.get_context(row["provider_tool"], row["auth_context"], require_ready=True)
    source_home = registry.codex_home(context["name"], tool=row["provider_tool"])
    auth_file = source_home / "auth.json"
    if auth_file.is_symlink() or not stat.S_ISREG(auth_file.lstat().st_mode):
        raise RuntimeError("provider auth unavailable")
    source_fd = os.open(auth_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        source_meta = os.fstat(source_fd)
        if not stat.S_ISREG(source_meta.st_mode) or source_meta.st_size > 1_048_576:
            raise RuntimeError("provider auth unavailable")
        with os.fdopen(source_fd, "rb", closefd=False) as source:
            with (codex_home / "auth.json").open("wb") as destination:
                shutil.copyfileobj(source, destination, length=65_536)
    finally:
        os.close(source_fd)
    (codex_home / "auth.json").chmod(0o600)
    adapter = provider_adapter(row["provider_tool"], registry)
    if not adapter.can_run_planning_task:
        raise RuntimeError("provider task capability unavailable")
    final_output = artifact_dir / "provider-final.txt"
    argv = adapter.build_planning_task_argv(
        cwd=frozen_dir, output_schema=schema_path, final_output=final_output,
    )
    return argv, _clean_provider_environment(job_home, codex_home), final_output


def _provider_version(argv: list[str], env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [argv[0], "--version"], env=env, capture_output=True, text=True, timeout=5,
            check=False,
        )
        value = (result.stdout or result.stderr).strip().splitlines()[0][:128]
        return value
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def run_request(
    request_uuid: str,
    *,
    settings: Settings | None = None,
    argv_override: list[str] | None = None,
    env_override: dict[str, str] | None = None,
    ack_timeout: float = ACK_TIMEOUT_SECONDS,
    run_timeout: float = RUN_TIMEOUT_SECONDS,
    diagnostic_max: int = DIAGNOSTIC_MAX,
    event_line_max: int = EVENT_LINE_MAX,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    if not REQUEST_UUID.fullmatch(request_uuid):
        return 2
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    database.migrate()
    with database.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM integration_requests WHERE id=?", (request_uuid,)).fetchone()
        if row is None or row["state"] != "accepted" or row["claimed_at"] is not None:
            return 0
        now = utc_now()
        claimed = conn.execute(
            "UPDATE integration_requests SET state='launching', claimed_at=?, updated_at=?, "
            "reason_code='launch_claimed', revision=revision+1 WHERE id=? AND state='accepted' "
            "AND claimed_at IS NULL",
            (now, now, request_uuid),
        )
        if claimed.rowcount != 1:
            return 0
        row = conn.execute("SELECT * FROM integration_requests WHERE id=?", (request_uuid,)).fetchone()

    try:
        argv, env, final_output = _prepare_provider(settings, row)
        if argv_override is not None:
            argv = list(argv_override)
        if env_override is not None:
            env = dict(env_override)
    except Exception:
        _update(database, request_uuid, {
            "state": "failed", "reason_code": "provider_unavailable", "admission_held": 0,
            "terminal_at": utc_now(),
        }, expected=("launching", "delivery_unknown"))
        return 1

    version = _provider_version(argv, env)
    try:
        process = popen_factory(
            argv,
            cwd=Path(row["artifact_dir"]) / "context",
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        _update(database, request_uuid, {
            "state": "failed", "reason_code": "provider_spawn_failed", "admission_held": 0,
            "terminal_at": utc_now(), "provider_version": version,
        }, expected=("launching", "delivery_unknown"))
        return 1

    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    start_time = _proc_start_time(process.pid)
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):
        pgid = None
    boot_id = _current_boot_id()
    if start_time is None or pgid != process.pid or boot_id is None:
        # Spawning is already possible at this boundary. Without the complete
        # durable identity, even this recently observed PID is not safe to
        # signal or use as proof that descendants are absent.
        _update(database, request_uuid, {
            "state": "delivery_unknown", "reason_code": "process_identity_unavailable",
            "provider_version": version,
        }, expected=("launching", "delivery_unknown"))
        return 1
    _update(database, request_uuid, {
        "child_pid": process.pid, "child_start_time": start_time, "child_pgid": pgid,
        "child_boot_id": boot_id,
        "provider_version": version,
    }, expected=("launching", "delivery_unknown"))

    prompt = row["frozen_prompt"].encode("utf-8")
    selector = selectors.DefaultSelector()
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    sent = 0
    delivered = False
    stdout_buffer = bytearray()
    total_diagnostics = 0
    receipts: list[dict[str, Any]] = []
    thread_id: str | None = None
    turn_started = False
    turn_completed = False
    provider_failed = False
    final_value: dict[str, Any] | None = None
    failure_reason: str | None = None
    acknowledged = False
    started_at = time.monotonic()

    def persist_receipts(**extra: Any) -> bool:
        binding = {
            "request_hash": row["canonical_hash"],
            "prompt_hash": row["prompt_hash"],
            "session_id": row["session_id"],
            "launch_nonce": row["launch_nonce"],
            "provider_version": version,
            "child": {
                "pid": process.pid,
                "start_time": start_time,
                "pgid": pgid,
                "boot_id": boot_id,
            },
            "thread_id": thread_id,
            "events": receipts,
        }
        fields = {"receipt_json": json.dumps(binding, sort_keys=True, separators=(",", ":"))}
        fields.update(extra)
        return _update(
            database, request_uuid, fields,
            expected=("launching", "started", "delivery_unknown"),
        )

    def maybe_acknowledge() -> None:
        nonlocal acknowledged
        if delivered and thread_id and turn_started:
            with database.connect() as conn:
                current = conn.execute(
                    "SELECT acknowledged FROM integration_requests WHERE id=?", (request_uuid,)
                ).fetchone()
            if current and not current["acknowledged"]:
                acknowledged = persist_receipts(
                    state="started", reason_code="provider_started", acknowledged=1,
                    thread_id=thread_id,
                )
            elif current and current["acknowledged"]:
                acknowledged = True

    def handle_event(raw_line: bytes) -> None:
        nonlocal thread_id, turn_started, turn_completed, provider_failed, final_value, failure_reason
        try:
            event = json.loads(raw_line.decode("utf-8", errors="strict"), object_pairs_hook=_no_duplicates)
        except Exception:
            failure_reason = "invalid_provider_event"
            return
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            failure_reason = "invalid_provider_event"
            return
        event_type = event["type"]
        sequence = len(receipts) + 1
        if event_type == "thread.started":
            value = event.get("thread_id")
            if thread_id is not None or turn_started or not isinstance(value, str) or not value:
                failure_reason = "invalid_provider_event_sequence"
                return
            thread_id = value
            receipts.append({"sequence": sequence, "type": event_type, "thread_id": value})
            persist_receipts(thread_id=value)
        elif event_type == "turn.started":
            if (
                thread_id is None
                or turn_started
                or (event.get("thread_id") is not None and event.get("thread_id") != thread_id)
            ):
                failure_reason = "invalid_provider_event_sequence"
                return
            turn_started = True
            receipts.append({"sequence": sequence, "type": event_type})
            persist_receipts()
            maybe_acknowledge()
        elif event_type == "turn.completed":
            if (
                not turn_started
                or turn_completed
                or (event.get("thread_id") is not None and event.get("thread_id") != thread_id)
            ):
                failure_reason = "invalid_provider_event_sequence"
                return
            turn_completed = True
            receipts.append({"sequence": sequence, "type": event_type})
            persist_receipts()
        elif event_type in {"turn.failed", "error"}:
            provider_failed = True
            receipts.append({"sequence": sequence, "type": event_type})
            persist_receipts()
        elif event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                if not turn_started or turn_completed or final_value is not None:
                    failure_reason = "invalid_provider_event_sequence"
                    return
                candidate = item.get("text", item.get("content"))
                if isinstance(candidate, str) and len(candidate.encode("utf-8")) <= ARTIFACT_MAX_BYTES:
                    try:
                        parsed = json.loads(candidate, object_pairs_hook=_no_duplicates)
                        final_value = _validate_final(parsed)
                    except Exception:
                        failure_reason = "invalid_final_output"

    try:
        while selector.get_map() or process.poll() is None:
            elapsed = time.monotonic() - started_at
            if elapsed > run_timeout:
                failure_reason = "provider_timeout"
                break
            if not acknowledged and elapsed > ack_timeout:
                failure_reason = "provider_ack_timeout"
                break
            for key, mask in selector.select(timeout=0.05):
                stream = key.fileobj
                if key.data == "stdin" and mask & selectors.EVENT_WRITE:
                    if sent < len(prompt):
                        try:
                            count = os.write(stream.fileno(), prompt[sent:])
                        except BlockingIOError:
                            count = 0
                        except BrokenPipeError:
                            failure_reason = "prompt_delivery_failed"
                            selector.unregister(stream)
                            stream.close()
                            continue
                        if count <= 0 and sent < len(prompt):
                            continue
                        sent += count
                    if sent == len(prompt):
                        selector.unregister(stream)
                        stream.close()
                        delivered = True
                        maybe_acknowledge()
                elif key.data in {"stdout", "stderr"} and mask & selectors.EVENT_READ:
                    try:
                        chunk = os.read(stream.fileno(), 65_536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    total_diagnostics += len(chunk)
                    if total_diagnostics > diagnostic_max:
                        failure_reason = "provider_output_limit"
                        break
                    if key.data == "stdout":
                        stdout_buffer.extend(chunk)
                        if len(stdout_buffer) > event_line_max and b"\n" not in stdout_buffer:
                            failure_reason = "provider_event_too_large"
                            break
                        while b"\n" in stdout_buffer:
                            line, _, remainder = stdout_buffer.partition(b"\n")
                            stdout_buffer = bytearray(remainder)
                            if len(line) > event_line_max:
                                failure_reason = "provider_event_too_large"
                                break
                            if line:
                                handle_event(bytes(line))
                            if failure_reason:
                                break
                if failure_reason:
                    break
            if failure_reason:
                break
        if stdout_buffer and not failure_reason:
            failure_reason = "truncated_provider_event"
    finally:
        selector.close()

    cleanup_uncertain = False
    if failure_reason and process.poll() is None:
        cleanup_uncertain = not terminate_owned_group(
            process.pid,
            start_time,
            pgid,
            boot_id,
            grace_seconds=min(KILL_GRACE_SECONDS, 1.0),
        )
    try:
        exit_code = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        cleanup_uncertain = not terminate_owned_group(
            process.pid, start_time, pgid, boot_id, grace_seconds=0.2
        ) or cleanup_uncertain
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _update(database, request_uuid, {
                "state": "delivery_unknown", "reason_code": "termination_unconfirmed",
                "admission_held": 1,
            }, expected=("launching", "started", "delivery_unknown"))
            return 1
    remaining_members = _active_group_members(pgid)
    if remaining_members is None or remaining_members:
        cleanup_uncertain = True

    if cleanup_uncertain:
        _update(database, request_uuid, {
            "state": "delivery_unknown", "reason_code": "termination_unconfirmed",
            "admission_held": 1,
        }, expected=("launching", "started", "delivery_unknown"))
        return 1

    with database.connect() as conn:
        current = conn.execute("SELECT * FROM integration_requests WHERE id=?", (request_uuid,)).fetchone()
    if final_value is None and final_output.is_file() and not final_output.is_symlink():
        try:
            raw_final = final_output.read_bytes()
            if len(raw_final) > ARTIFACT_MAX_BYTES:
                raise ValueError
            final_value = _validate_final(
                json.loads(raw_final.decode("utf-8", errors="strict"), object_pairs_hook=_no_duplicates)
            )
        except Exception:
            failure_reason = failure_reason or "invalid_final_output"
    if failure_reason or provider_failed or exit_code != 0 or not delivered or not turn_completed or final_value is None:
        reason = failure_reason or (
            "provider_reported_failure" if provider_failed else
            "provider_nonzero_exit" if exit_code != 0 else
            "prompt_delivery_failed" if not delivered else
            "completion_receipt_missing" if not turn_completed else
            "invalid_final_output"
        )
        _update(database, request_uuid, {
            "state": "failed", "reason_code": reason, "admission_held": 0,
            "terminal_at": utc_now(),
        }, expected=("launching", "started", "delivery_unknown"))
        return 1

    artifact_dir = Path(row["artifact_dir"])
    plan_raw = json.dumps(final_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    try:
        _atomic_write(artifact_dir / "plan.json", plan_raw)
        _atomic_write(artifact_dir / "plan.md", _render_markdown(final_value))
    except Exception:
        _update(database, request_uuid, {
            "state": "failed", "reason_code": "artifact_write_failed", "admission_held": 0,
            "terminal_at": utc_now(),
        }, expected=("started", "delivery_unknown"))
        return 1
    outcome = "completed" if final_value["outcome"] == "plan" else "needs_input"
    _update(database, request_uuid, {
        "state": outcome, "reason_code": outcome, "admission_held": 0,
        "terminal_at": utc_now(), "final_artifact_name": "plan.json",
        "final_artifact_hash": hashlib.sha256(plan_raw).hexdigest(),
    }, expected=("started", "delivery_unknown"))
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        return 2
    return run_request(values[0])


if __name__ == "__main__":
    raise SystemExit(main())
