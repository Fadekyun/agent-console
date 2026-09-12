from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.database import Database, utc_now
from agent_console.integration_requests import (
    IntegrationError,
    IntegrationService,
    REQUEST_FIELDS,
    parse_json_object,
    validate_request,
)
from agent_console.manager import SessionManager
from agent_console.providers import provider_adapter
from agent_console.task_runner import (
    _active_group_members,
    _clean_provider_environment,
    _current_boot_id,
    _proc_start_time,
    process_identity_matches,
    run_request,
    terminate_owned_group,
)
from agent_console.tmux import TmuxSession
from agent_console.validation import PROFILES


OWNER = "191524132624531458"
CHANNEL = "1493588468884836402"
REQUEST_ID = "12345678901234567"


class _ProcessFakeTmux:
    scope = "canonical"

    def __init__(self) -> None:
        self.names: set[str] = set()

    def exists(self, name: str) -> bool:
        return name in self.names

    def create(self, name: str, _cwd: Path, _launcher: Path) -> None:
        time.sleep(0.1)
        self.names.add(name)

    def list_sessions(self) -> dict[str, TmuxSession]:
        epoch = int(time.time())
        return {
            name: TmuxSession(name, epoch, epoch, 0, 1, "python")
            for name in self.names
        }

    def kill(self, name: str) -> None:
        self.names.discard(name)


def _ordinary_admission_process(settings: Settings, barrier: Any, output: Any) -> None:
    manager = SessionManager(settings)
    manager.tmux = _ProcessFakeTmux()
    barrier.wait()
    try:
        manager.create(
            tool="shell", profile="general", name="ordinary-racer",
            repository=str(settings.workspace_root),
        )
        output.put(("ordinary", "accepted"))
    except RuntimeError:
        output.put(("ordinary", "busy"))


def _integration_admission_process(settings: Settings, barrier: Any, output: Any, raw: bytes) -> None:
    manager = SessionManager(settings)
    manager.tmux = _ProcessFakeTmux()
    barrier.wait()
    response, _ = IntegrationService(manager).submit(raw)
    output.put(("integration", "accepted" if response["accepted"] else response["state"]))


class IntegrationRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        workspace = root / "workspace"
        workspace.mkdir()
        profiles = root / "profiles"
        profiles.mkdir()
        for profile in PROFILES:
            (profiles / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.settings = Settings(
            workspace_root=workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profiles,
            handoff_dir=root / "handoffs",
            worktree_root=workspace / "worktrees",
            tmux_socket=f"integration-test-{os.getpid()}-{id(self)}",
            max_managed_sessions=4,
        )
        self.manager = SessionManager(self.settings)
        self.context_root = root / "prepared-contexts"
        self.context_dir = self.context_root / "n100"
        self.context_dir.mkdir(parents=True)
        (self.context_dir / "facts.txt").write_text("frozen fact\n", encoding="utf-8")
        now = utc_now()
        with self.manager.database.connect() as conn:
            conn.execute(
                "INSERT INTO projects(id,name,repository,status,created_at,updated_at) "
                "VALUES('project-n100','N100',NULL,'active',?,?)",
                (now, now),
            )
        auth_home = self.manager.auth.codex_home("default")
        (auth_home / "auth.json").write_text("{}\n", encoding="utf-8")
        (auth_home / "auth.json").chmod(0o600)
        self.capability = b"test-relay-capability\n"
        self.capability_path = root / "relay.capability"
        self.capability_path.write_bytes(self.capability)
        self.capability_path.chmod(0o600)
        self.capability_fd = os.open(self.capability_path, os.O_RDONLY)
        self.old_fd = os.environ.get("AGENT_CONSOLE_RELAY_CAPABILITY_FD")
        os.environ["AGENT_CONSOLE_RELAY_CAPABILITY_FD"] = str(self.capability_fd)
        self.write_config(enabled=True, verified=True)
        self.service = IntegrationService(self.manager)

    def tearDown(self) -> None:
        os.close(self.capability_fd)
        if self.old_fd is None:
            os.environ.pop("AGENT_CONSOLE_RELAY_CAPABILITY_FD", None)
        else:
            os.environ["AGENT_CONSOLE_RELAY_CAPABILITY_FD"] = self.old_fd
        self.manager.tmux.run("kill-server", check=False)
        self.temp.cleanup()

    def write_config(self, *, enabled: bool, verified: bool) -> None:
        config = {
            "protocol_version": 1,
            "enabled": enabled,
            "provider_capability_verified": verified,
            "relay_capability_sha256": hashlib.sha256(self.capability).hexdigest(),
            "owners": [OWNER, "96272748430528512"],
            "channels": [CHANNEL],
            "context_root": str(self.context_root),
            "provider": {"tool": "codex", "auth_context": "default"},
            "projects": {
                "n100": {
                    "project_id": "project-n100",
                    "context_dir": str(self.context_dir),
                    "provenance": "unit-test snapshot",
                    "collected_at": "2026-09-13T00:00:00Z",
                }
            },
        }
        path = self.manager.auth.config_dir / "plan-integration.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        path.chmod(0o600)

    def request(self, *, text: str = "Prepare a safe plan", request_id: str = REQUEST_ID,
                owner: str = OWNER, channel: str = CHANNEL) -> bytes:
        return json.dumps({
            "request_id": request_id,
            "requester_id": owner,
            "channel_id": channel,
            "project": "n100",
            "text": text,
        }).encode()

    def accept_without_tmux(self, raw: bytes | None = None) -> dict:
        with patch.object(self.manager.tmux, "create") as create:
            response, code = self.service.submit(raw or self.request())
        self.assertEqual(code, 0)
        self.assertTrue(response["accepted"])
        create.assert_called_once()
        return response

    def fake_argv(self, *, final: dict | None = None, exit_code: int = 0,
                  events: list[dict] | None = None, stderr_bytes: int = 0) -> list[str]:
        final = final or {
            "outcome": "plan",
            "summary": "Safe summary",
            "steps": ["one"],
            "verification": ["check"],
            "blockers": [],
        }
        events = events if events is not None else [
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started", "thread_id": "thread-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(final)}},
            {"type": "turn.completed", "thread_id": "thread-1"},
        ]
        script = (
            "import json,sys; sys.stdin.buffer.read(); "
            + (f"sys.stderr.buffer.write(b'x'*{stderr_bytes}); sys.stderr.flush(); " if stderr_bytes else "")
            + f"events={events!r}; "
            + "[print(json.dumps(e), flush=True) for e in events]; "
            + f"raise SystemExit({exit_code})"
        )
        return [sys.executable, "-c", script]

    def row(self):
        with self.manager.database.connect() as conn:
            return conn.execute(
                "SELECT * FROM integration_requests WHERE request_key=?", (REQUEST_ID,)
            ).fetchone()

    def test_strict_contract_rejects_duplicate_extra_invalid_and_controls(self) -> None:
        valid = self.request()
        self.assertEqual(validate_request(parse_json_object(valid, fields=REQUEST_FIELDS))["text"], "Prepare a safe plan")
        invalid = [
            b'{"request_id":"12345678901234567","request_id":"12345678901234567","requester_id":"191524132624531458","channel_id":"1493588468884836402","project":"n100","text":"x"}',
            valid[:-1] + b',"extra":1}',
            valid + b" trailing",
            b"\xff",
            self.request(text="   "),
            self.request(text="bad\x00control"),
            self.request(text="bad\x1bcontrol"),
            self.request(text="bad\x7fcontrol"),
            self.request(text="x" * 4001),
            valid + b" " * 32769,
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(IntegrationError):
                    validate_request(parse_json_object(raw, fields=REQUEST_FIELDS))

    def test_unauthorized_disabled_unverified_and_bad_relay_create_nothing(self) -> None:
        cases = [
            (self.request(owner="11111111111111111"), "unauthorized", 3),
            (self.request(channel="11111111111111111"), "unauthorized", 3),
        ]
        for raw, reason, expected_code in cases:
            with self.assertRaises(IntegrationError) as caught:
                self.service.submit(raw)
            self.assertEqual((caught.exception.reason_code, caught.exception.exit_code), (reason, expected_code))
        self.write_config(enabled=False, verified=True)
        response, code = self.service.submit(self.request())
        self.assertEqual((response["reason_code"], code), ("integration_disabled", 4))
        self.write_config(enabled=True, verified=False)
        response, code = self.service.submit(self.request())
        self.assertEqual((response["reason_code"], code), ("provider_capability_unverified", 4))
        self.write_config(enabled=True, verified=True)
        os.environ["AGENT_CONSOLE_RELAY_CAPABILITY_FD"] = "999"
        with self.assertRaises(IntegrationError) as caught:
            self.service.submit(self.request())
        self.assertEqual((caught.exception.reason_code, caught.exception.exit_code), ("unauthorized", 3))
        with self.manager.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM integration_requests").fetchone()[0], 0)

    def test_acceptance_freezes_context_prompt_and_does_not_shell_interpret_text(self) -> None:
        marker = Path(self.temp.name) / "SHOULD_NOT_EXIST"
        text = f"`touch {marker}` $(touch {marker})\n--dangerously-bypass-approvals-and-sandbox"
        self.accept_without_tmux(self.request(text=text))
        before = self.row()
        self.assertEqual(before["state"], "accepted")
        (self.context_dir / "facts.txt").write_text("changed later\n", encoding="utf-8")
        after = self.row()
        self.assertEqual(after["frozen_prompt"], before["frozen_prompt"])
        frozen = Path(after["artifact_dir"]) / "context" / "facts.txt"
        self.assertEqual(frozen.read_text(), "frozen fact\n")
        self.assertFalse(marker.exists())
        launcher = Path(self.settings.state_dir / "launchers" / f"n8n-plan-{REQUEST_ID}.sh").read_text()
        self.assertNotIn(text, launcher)
        self.assertIn("agent_console.task_runner", launcher)

    def test_concurrent_duplicate_and_conflict_reserve_once(self) -> None:
        results: list[tuple[dict, int]] = []
        lock = threading.Lock()
        def submit_one() -> None:
            result = self.service.submit(self.request())
            with lock:
                results.append(result)
        with patch.object(self.manager.tmux, "create") as create:
            threads = [threading.Thread(target=submit_one) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(results), 20)
        self.assertTrue(all(code == 0 and response["accepted"] for response, code in results))
        self.assertEqual(sum(not response["duplicate"] for response, _ in results), 1)
        self.assertEqual(create.call_count, 1)
        conflict, code = self.service.submit(self.request(text="changed"))
        self.assertEqual((conflict["reason_code"], code), ("idempotency_conflict", 2))
        with self.manager.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM integration_requests").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE execution_kind='integration-plan'").fetchone()[0], 1)

    def test_cross_process_ordinary_and_request_share_global_admission(self) -> None:
        constrained = Settings(
            **{
                **self.settings.__dict__,
                "max_managed_sessions": 1,
            }
        )
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        output = context.Queue()
        ordinary = context.Process(
            target=_ordinary_admission_process, args=(constrained, barrier, output),
        )
        integration = context.Process(
            target=_integration_admission_process,
            args=(constrained, barrier, output, self.request()),
        )
        ordinary.start()
        integration.start()
        ordinary.join(timeout=10)
        integration.join(timeout=10)
        self.assertEqual((ordinary.exitcode, integration.exitcode), (0, 0))
        outcomes = {output.get(timeout=1), output.get(timeout=1)}
        self.assertEqual(sum(value == "accepted" for _, value in outcomes), 1)
        self.assertEqual({value for _, value in outcomes}, {"accepted", "busy"})

    def test_admission_counts_request_once_and_holds_unknown(self) -> None:
        self.accept_without_tmux()
        busy, code = self.service.submit(self.request(request_id="22345678901234567"))
        self.assertEqual(code, 0)
        self.assertEqual((busy["state"], busy["reason_code"]), ("busy", "admission_busy"))
        self.assertFalse(busy["accepted"])
        self.assertEqual(busy["retry_after_seconds"], 60)
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE integration_requests SET state='delivery_unknown' WHERE request_key=?",
                (REQUEST_ID,),
            )
        busy_again, _ = self.service.submit(self.request(request_id="32345678901234567"))
        self.assertEqual(busy_again["state"], "busy")

    def test_lost_tmux_response_never_overwrites_claim_or_acknowledgement(self) -> None:
        def started_then_lost(*_args) -> None:
            with self.manager.database.connect() as conn:
                conn.execute(
                    "UPDATE integration_requests SET state='started', claimed_at=accepted_at, "
                    "acknowledged=1, reason_code='provider_started' WHERE request_key=?",
                    (REQUEST_ID,),
                )
            raise RuntimeError("simulated lost tmux response")

        with patch.object(self.manager.tmux, "create", side_effect=started_then_lost):
            response, code = self.service.submit(self.request())
        self.assertEqual(code, 0)
        self.assertEqual(response["state"], "started")
        self.assertTrue(response["acknowledged"])
        row = self.row()
        self.assertEqual(row["state"], "started")
        self.assertTrue(row["admission_held"])

    def test_lost_tmux_response_after_claim_becomes_unknown_and_holds_slot(self) -> None:
        def claimed_then_lost(*_args) -> None:
            with self.manager.database.connect() as conn:
                conn.execute(
                    "UPDATE integration_requests SET state='launching', claimed_at=accepted_at, "
                    "reason_code='launch_claimed' WHERE request_key=?",
                    (REQUEST_ID,),
                )
            raise RuntimeError("simulated lost tmux response")

        with patch.object(self.manager.tmux, "create", side_effect=claimed_then_lost):
            response, code = self.service.submit(self.request())
        self.assertEqual(code, 0)
        self.assertEqual(response["state"], "delivery_unknown")
        self.assertTrue(self.row()["admission_held"])

    def test_ordinary_create_cannot_reuse_integration_owned_name(self) -> None:
        self.accept_without_tmux()
        name = f"n8n-plan-{REQUEST_ID}"
        artifact_dir = Path(self.row()["artifact_dir"])

        def artifact_snapshot() -> dict[str, bytes]:
            return {
                str(path.relative_to(artifact_dir)): path.read_bytes()
                for path in artifact_dir.rglob("*")
                if path.is_file()
            }

        for state, held in (("accepted", 1), ("started", 1), ("failed", 0)):
            with self.subTest(state=state):
                with self.manager.database.connect() as conn:
                    conn.execute(
                        "UPDATE integration_requests SET state=?, admission_held=? "
                        "WHERE request_key=?",
                        (state, held, REQUEST_ID),
                    )
                    request_before = dict(conn.execute(
                        "SELECT * FROM integration_requests WHERE request_key=?", (REQUEST_ID,)
                    ).fetchone())
                    session_before = dict(conn.execute(
                        "SELECT * FROM sessions WHERE tmux_name=?", (name,)
                    ).fetchone())
                files_before = artifact_snapshot()
                with patch.object(self.manager.tmux, "exists", return_value=False), patch.object(
                    self.manager.tmux, "create"
                ) as create:
                    with self.assertRaises(FileExistsError):
                        self.manager.create(
                            tool="shell",
                            profile="general",
                            name=name,
                            repository=str(self.settings.workspace_root),
                        )
                create.assert_not_called()
                with self.manager.database.connect() as conn:
                    request_after = dict(conn.execute(
                        "SELECT * FROM integration_requests WHERE request_key=?", (REQUEST_ID,)
                    ).fetchone())
                    session_after = dict(conn.execute(
                        "SELECT * FROM sessions WHERE tmux_name=?", (name,)
                    ).fetchone())
                self.assertEqual(request_after, request_before)
                self.assertEqual(session_after, session_before)
                self.assertEqual(artifact_snapshot(), files_before)

    def test_ordinary_archived_name_remains_reusable(self) -> None:
        now = utc_now()
        with self.manager.database.connect() as conn:
            conn.execute(
                "INSERT INTO sessions(id,tmux_name,created_at,status,managed,creator_surface) "
                "VALUES('sess-archived-reuse','archived-reuse',?,'archived',1,'CLI')",
                (now,),
            )
        with patch.object(self.manager.tmux, "exists", return_value=False), patch.object(
            self.manager.tmux, "create"
        ) as create:
            self.manager.create(
                tool="shell",
                profile="general",
                name="archived-reuse",
                task="stored brief",
                repository=str(self.settings.workspace_root),
            )
        create.assert_called_once()
        with self.manager.database.connect() as conn:
            row = conn.execute(
                "SELECT id,execution_kind,initial_task FROM sessions WHERE tmux_name=?",
                ("archived-reuse",),
            ).fetchone()
        self.assertEqual(
            (row["id"], row["execution_kind"], row["initial_task"]),
            ("sess-archived-reuse", "interactive", "stored brief"),
        )

    def test_status_repeats_binding_before_reconciliation(self) -> None:
        self.accept_without_tmux()
        other_owner = "96272748430528512"
        raw = json.dumps({
            "request_id": REQUEST_ID,
            "requester_id": other_owner,
            "channel_id": CHANNEL,
        }).encode()
        with patch.object(self.manager.tmux, "create") as create:
            response, code = self.service.status(raw)
        self.assertEqual((response["reason_code"], code), ("unauthorized", 3))
        create.assert_not_called()
        with self.manager.database.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM integration_requests WHERE request_key=?", (REQUEST_ID,)
            ).fetchone()["state"], "accepted")

    def test_runner_success_requires_ordered_receipt_valid_final_and_exit_zero(self) -> None:
        self.accept_without_tmux()
        row = self.row()
        result = run_request(
            row["id"], settings=self.settings, argv_override=self.fake_argv(), env_override={},
            ack_timeout=2, run_timeout=5,
        )
        self.assertEqual(result, 0)
        completed = self.row()
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["acknowledged"])
        receipt = json.loads(completed["receipt_json"])
        self.assertEqual(receipt["request_hash"], completed["canonical_hash"])
        self.assertEqual(receipt["prompt_hash"], completed["prompt_hash"])
        self.assertEqual([event["type"] for event in receipt["events"] if event["type"].startswith(("thread", "turn"))], [
            "thread.started", "turn.started", "turn.completed"
        ])
        self.assertEqual(self.service.result(REQUEST_ID)["summary"], "Safe summary")
        self.assertEqual(run_request(row["id"], settings=self.settings, argv_override=self.fake_argv(), env_override={}), 0)

    def test_acknowledged_is_preserved_when_provider_later_fails(self) -> None:
        self.accept_without_tmux()
        result = run_request(
            self.row()["id"], settings=self.settings,
            argv_override=self.fake_argv(exit_code=7), env_override={}, ack_timeout=2, run_timeout=5,
        )
        self.assertEqual(result, 1)
        failed = self.row()
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["acknowledged"])
        self.assertEqual(failed["reason_code"], "provider_nonzero_exit")

    def test_missing_mismatched_duplicate_and_truncated_receipts_fail_closed(self) -> None:
        matrices = [
            ([{"type": "thread.started", "thread_id": "t"}], False),
            ([{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started", "thread_id": "wrong"}], False),
            ([{"type": "thread.started", "thread_id": "t"}, {"type": "thread.started", "thread_id": "t"}], False),
        ]
        for index, (events, acknowledged) in enumerate(matrices):
            request_id = str(index + 4) + REQUEST_ID[1:]
            self.accept_without_tmux(self.request(request_id=request_id))
            with self.manager.database.connect() as conn:
                row = conn.execute("SELECT * FROM integration_requests WHERE request_key=?", (request_id,)).fetchone()
            run_request(
                row["id"], settings=self.settings,
                argv_override=self.fake_argv(events=events), env_override={}, ack_timeout=2, run_timeout=5,
            )
            with self.manager.database.connect() as conn:
                failed = conn.execute("SELECT * FROM integration_requests WHERE id=?", (row["id"],)).fetchone()
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(bool(failed["acknowledged"]), acknowledged)
            with self.manager.database.connect() as conn:
                conn.execute("UPDATE integration_requests SET admission_held=0 WHERE id=?", (row["id"],))

    def test_final_agent_message_outside_active_turn_is_rejected(self) -> None:
        final_item = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps({
                "outcome": "plan", "summary": "x", "steps": [],
                "verification": [], "blockers": [],
            })},
        }
        event_sets = [
            [final_item, {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}],
            [
                {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
                {"type": "turn.completed"}, final_item,
            ],
        ]
        for index, events in enumerate(event_sets):
            request_id = str(index + 7) + REQUEST_ID[1:]
            self.accept_without_tmux(self.request(request_id=request_id))
            with self.manager.database.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM integration_requests WHERE request_key=?", (request_id,)
                ).fetchone()
            run_request(
                row["id"], settings=self.settings, argv_override=self.fake_argv(events=events),
                env_override={}, ack_timeout=2, run_timeout=5,
            )
            with self.manager.database.connect() as conn:
                failed = conn.execute(
                    "SELECT state,reason_code FROM integration_requests WHERE id=?", (row["id"],)
                ).fetchone()
            self.assertEqual((failed["state"], failed["reason_code"]), (
                "failed", "invalid_provider_event_sequence"
            ))

    def test_pipe_flood_is_bounded_and_stale_identity_is_not_killed(self) -> None:
        self.accept_without_tmux()
        result = run_request(
            self.row()["id"], settings=self.settings,
            argv_override=self.fake_argv(stderr_bytes=4096), env_override={},
            diagnostic_max=512, ack_timeout=2, run_timeout=5,
        )
        self.assertEqual(result, 1)
        self.assertEqual(self.row()["reason_code"], "provider_output_limit")
        self.assertFalse(process_identity_matches(
            os.getpid(), "definitely-wrong", os.getpid(), _current_boot_id()
        ))

    def test_early_events_without_full_stdin_do_not_bypass_ack_timeout(self) -> None:
        self.accept_without_tmux()
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE integration_requests SET frozen_prompt=? WHERE request_key=?",
                ("x" * 200_000, REQUEST_ID),
            )
        script = (
            "import json,time; "
            "print(json.dumps({'type':'thread.started','thread_id':'t'}),flush=True); "
            "print(json.dumps({'type':'turn.started'}),flush=True); time.sleep(5)"
        )
        result = run_request(
            self.row()["id"], settings=self.settings,
            argv_override=[sys.executable, "-c", script], env_override={},
            ack_timeout=0.15, run_timeout=2,
        )
        self.assertEqual(result, 1)
        row = self.row()
        self.assertEqual((row["state"], bool(row["acknowledged"])), ("failed", False))
        self.assertEqual(row["reason_code"], "provider_ack_timeout")

    def test_kill_claim_race_and_stale_pid_remain_unknown_and_held(self) -> None:
        self.accept_without_tmux()
        name = f"n8n-plan-{REQUEST_ID}"
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE integration_requests SET state='launching', claimed_at=accepted_at, "
                "child_pid=NULL, child_start_time=NULL, child_pgid=NULL WHERE request_key=?",
                (REQUEST_ID,),
            )
        self.manager.kill(name)
        row = self.row()
        self.assertEqual((row["state"], bool(row["admission_held"])), ("delivery_unknown", True))
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE integration_requests SET state='launching', child_pid=?, "
                "child_start_time='stale', child_pgid=? WHERE request_key=?",
                (os.getpid(), os.getpid(), REQUEST_ID),
            )
        self.manager.kill(name)
        row = self.row()
        self.assertEqual((row["state"], bool(row["admission_held"])), ("delivery_unknown", True))
        os.kill(os.getpid(), 0)

    def test_missing_or_mismatched_boot_identity_never_kills_or_releases(self) -> None:
        self.accept_without_tmux()
        name = f"n8n-plan-{REQUEST_ID}"
        for saved_boot_id in (None, "00000000-0000-0000-0000-000000000000"):
            with self.subTest(saved_boot_id=saved_boot_id):
                with self.manager.database.connect() as conn:
                    conn.execute(
                        "UPDATE integration_requests SET state='launching', claimed_at=accepted_at, "
                        "child_pid=4242, child_start_time='123', child_pgid=4242, "
                        "child_boot_id=?, admission_held=1 WHERE request_key=?",
                        (saved_boot_id, REQUEST_ID),
                    )
                with patch("agent_console.task_runner._current_boot_id", return_value=(
                    "11111111-1111-1111-1111-111111111111"
                )), patch("agent_console.task_runner._proc_start_time", return_value="123"), patch(
                    "agent_console.task_runner.os.getpgid", return_value=4242
                ), patch("agent_console.task_runner.os.killpg") as killpg:
                    self.manager.kill(name)
                killpg.assert_not_called()
                row = self.row()
                self.assertEqual(
                    (row["state"], bool(row["admission_held"])),
                    ("delivery_unknown", True),
                )

    def test_proc_scan_uncertainty_is_not_an_empty_owned_group(self) -> None:
        with patch("agent_console.task_runner.Path.iterdir", side_effect=PermissionError):
            self.assertIsNone(_active_group_members(4242))
        with patch(
            "agent_console.task_runner.process_identity_matches", return_value=True
        ), patch(
            "agent_console.task_runner._active_group_members", return_value=None
        ), patch("agent_console.task_runner.os.killpg") as killpg:
            self.assertFalse(terminate_owned_group(
                4242,
                "123",
                4242,
                "11111111-1111-1111-1111-111111111111",
                grace_seconds=0.01,
            ))
        killpg.assert_not_called()

    def test_missing_leader_with_surviving_group_is_not_declared_terminated(self) -> None:
        script = (
            "import os,sys,time; child=os.fork(); "
            "(time.sleep(30),os._exit(0)) if child==0 else "
            "(print(child,flush=True),time.sleep(.3))"
        )
        leader = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        assert leader.stdout is not None
        child_pid = int(leader.stdout.readline().strip())
        start_time = _proc_start_time(leader.pid)
        pgid = leader.pid
        leader.wait(timeout=2)
        try:
            self.assertIsNotNone(start_time)
            self.assertFalse(terminate_owned_group(
                leader.pid, start_time, pgid, _current_boot_id(), grace_seconds=0.1
            ))
            os.kill(child_pid, 0)
        finally:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_result_artifact_is_bounded_escaped_and_symlink_rejected(self) -> None:
        final = {
            "outcome": "needs_input",
            "summary": "<script>alert(1)</script> https://example.invalid",
            "steps": [], "verification": [], "blockers": ["missing"],
        }
        self.accept_without_tmux()
        run_request(
            self.row()["id"], settings=self.settings,
            argv_override=self.fake_argv(final=final), env_override={}, ack_timeout=2, run_timeout=5,
        )
        row = self.row()
        self.assertEqual(row["state"], "needs_input")
        rendered = (Path(row["artifact_dir"]) / "plan.md").read_text()
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("://", rendered)
        plan = Path(row["artifact_dir"]) / "plan.json"
        plan.unlink()
        plan.symlink_to(Path(self.temp.name) / "outside.json")
        with self.assertRaises(RuntimeError):
            self.service.result(REQUEST_ID)

    def test_expiry_removes_content_but_keeps_idempotency_and_receipt(self) -> None:
        self.accept_without_tmux()
        run_request(
            self.row()["id"], settings=self.settings,
            argv_override=self.fake_argv(), env_override={}, ack_timeout=2, run_timeout=5,
        )
        before = self.row()
        receipt = before["receipt_json"]
        request_hash = before["canonical_hash"]
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE integration_requests SET content_expires_at='2000-01-01T00:00:00+00:00' "
                "WHERE id=?", (before["id"],)
            )
        raw_status = json.dumps({
            "request_id": REQUEST_ID, "requester_id": OWNER, "channel_id": CHANNEL,
        }).encode()
        response, code = self.service.status(raw_status)
        self.assertEqual((response["state"], code), ("completed", 0))
        self.assertNotIn("artifact_ref", response)
        after = self.row()
        self.assertEqual(after["canonical_payload_json"], "")
        self.assertEqual(after["frozen_prompt"], "")
        self.assertEqual(after["canonical_hash"], request_hash)
        self.assertEqual(after["receipt_json"], receipt)
        self.assertFalse(Path(after["artifact_dir"]).exists())

    def test_request_owned_lifecycle_is_view_only(self) -> None:
        self.accept_without_tmux()
        name = f"n8n-plan-{REQUEST_ID}"
        with self.assertRaises(PermissionError):
            self.manager.restart(name)
        with self.assertRaises(PermissionError):
            self.manager.rename(name, "other-name")
        with self.assertRaises(PermissionError):
            self.manager.archive(name)
        self.assertEqual(self.manager.inspect(name)["actions"], ["view"])

    def test_native_task_adapter_has_fixed_read_only_stdin_shape(self) -> None:
        adapter = provider_adapter("codex", self.manager.auth)
        argv = adapter.build_planning_task_argv(
            cwd=self.context_dir,
            output_schema=Path(self.temp.name) / "schema.json",
            final_output=Path(self.temp.name) / "final.json",
        )
        self.assertIn("exec", argv)
        self.assertIn("--json", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertEqual(argv[-1], "-")
        self.assertNotIn("workspace-write", argv)
        self.assertNotIn("AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS", " ".join(argv))
        clean = _clean_provider_environment(
            Path(self.temp.name) / "home", Path(self.temp.name) / "home" / ".codex"
        )
        self.assertEqual(set(clean), {"HOME", "CODEX_HOME", "PATH", "LANG", "LC_ALL"})
        for forbidden in (
            "SSH_AUTH_SOCK", "AGENT_CONSOLE_EVIDENCE_CAPABILITY",
            "AGENT_CONSOLE_RELAY_CAPABILITY_FD", "AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS",
        ):
            self.assertNotIn(forbidden, clean)

    def test_doctor_reports_disabled_and_unverified_without_secrets(self) -> None:
        self.write_config(enabled=False, verified=False)
        status = self.manager.doctor()["integration_planner"]
        self.assertEqual(status["reason_code"], "integration_disabled")
        self.assertNotIn("relay_capability_sha256", status)
        self.write_config(enabled=True, verified=False)
        status = self.manager.doctor()["integration_planner"]
        self.assertEqual(status["reason_code"], "provider_capability_unverified")
        self.assertFalse(status["available"])

    def test_cli_is_value_blind_and_requires_stdin_contract(self) -> None:
        self.write_config(enabled=False, verified=False)
        secret_text = "SECRET-SENTINEL-DO-NOT-ECHO"
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "AGENT_CONSOLE_WORKSPACE_ROOT": str(self.settings.workspace_root),
            "AGENT_CONSOLE_STATE_DIR": str(self.settings.state_dir),
            "AGENT_CONSOLE_DB": str(self.settings.database_path),
            "AGENT_CONSOLE_PROFILE_DIR": str(self.settings.profile_dir),
            "AGENT_CONSOLE_CONFIG_DIR": str(self.manager.auth.config_dir),
            "AGENT_CONSOLE_TMUX_SOCKET": self.settings.tmux_socket,
            "AGENT_CONSOLE_TMUX_SOCKET_PATH": str(self.settings.state_dir / "tmux.sock"),
            "AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH": str(self.settings.state_dir / "legacy.sock"),
            "AGENT_CONSOLE_RELAY_CAPABILITY_FD": str(self.capability_fd),
        }
        result = __import__("subprocess").run(
            [sys.executable, "-m", "agent_console.cli", "integration", "plan-request", "--stdin"],
            input=self.request(text=secret_text), capture_output=True, env=environment,
            pass_fds=(self.capability_fd,), check=False,
        )
        self.assertEqual(result.returncode, 4, result.stderr.decode(errors="replace"))
        response = json.loads(result.stdout)
        self.assertEqual(response["reason_code"], "integration_disabled")
        self.assertNotIn(secret_text.encode(), result.stdout)
        self.assertNotIn(secret_text.encode(), result.stderr)


class MigrationTests(unittest.TestCase):
    def test_schema_migration_preserves_ordinary_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            database = Database(path)
            database.migrate()
            with database.connect() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,tmux_name,created_at,status,managed,creator_surface) "
                    "VALUES('s1','ordinary','now','detached',1,'CLI')"
                )
                conn.execute("DROP TABLE integration_requests")
                conn.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
            database.migrate()
            with Database(path).connect() as migrated:
                row = migrated.execute("SELECT tmux_name,execution_kind FROM sessions WHERE id='s1'").fetchone()
                self.assertEqual((row["tmux_name"], row["execution_kind"]), ("ordinary", "interactive"))
                self.assertIsNotNone(migrated.execute("SELECT name FROM sqlite_master WHERE name='integration_requests'").fetchone())
                columns = {
                    item[1] for item in migrated.execute(
                        "PRAGMA table_info(integration_requests)"
                    ).fetchall()
                }
                self.assertIn("child_boot_id", columns)


if __name__ == "__main__":
    unittest.main()
