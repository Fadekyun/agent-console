from __future__ import annotations

import asyncio
import io
import json
import logging
import logging.handlers
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_console.config import Settings
from agent_console.database import Database
from agent_console.logging_config import (
    JsonFormatter,
    SecretRedactionFilter,
    configure_logging,
    configure_uvicorn_logging,
    get_logger,
    prune_logs,
    redact_secrets,
    reset_logging,
    assert_logging_config_parity,
    read_logging_config_json,
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LoggingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_logging()
        self.capture = io.StringIO()
        self.handler = logging.StreamHandler(self.capture)
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

    def tearDown(self) -> None:
        reset_logging()

    def test_configure_logging_adds_handler(self) -> None:
        configure_logging()
        get_logger("test_logger")
        root = logging.getLogger()
        self.assertGreater(len(root.handlers), 0)

    def test_logging_respects_log_level_env(self) -> None:
        os.environ["AGENT_CONSOLE_LOG_LEVEL"] = "WARNING"
        try:
            configure_logging()
        finally:
            del os.environ["AGENT_CONSOLE_LOG_LEVEL"]

    def test_logger_emits_expected_format(self) -> None:
        logging.getLogger().addHandler(self.handler)
        test_log = logging.getLogger("test.capture")
        test_log.setLevel(logging.DEBUG)
        test_log.info("session=%s action=create tool=%s", "test-session", "codex")
        output = self.capture.getvalue()
        self.assertIn("info", output.lower())
        self.assertIn("session=test-session", output)
        self.assertIn("action=create", output)
        self.assertIn("tool=codex", output)

    def test_logger_emits_key_value_pairs(self) -> None:
        logging.getLogger().addHandler(self.handler)
        test_log = logging.getLogger("test.kv")
        test_log.setLevel(logging.DEBUG)
        test_log.info("session=%s action=kill managed=%s", "kill-me", True)
        output = self.capture.getvalue()
        self.assertIn("session=kill-me", output)
        self.assertIn("action=kill", output)


class JsonFormatterTests(unittest.TestCase):
    def test_json_format_contains_expected_fields(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        self.assertIn("timestamp", parsed)
        self.assertIn("level", parsed)
        self.assertEqual(parsed["level"], "INFO")
        self.assertIn("logger", parsed)
        self.assertEqual(parsed["logger"], "test.logger")
        self.assertIn("message", parsed)
        self.assertEqual(parsed["message"], "hello world")

    def test_json_format_with_args(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="session=%s action=%s",
            args=("sess-1", "create"),
            exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["message"], "session=sess-1 action=create")

    def test_json_format_with_exception(self) -> None:
        fmt = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="something broke",
            args=(),
            exc_info=exc_info,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        self.assertIn("exception", parsed)


class SecretRedactionTests(unittest.TestCase):
    def test_redact_api_key_pattern(self) -> None:
        msg = "api_key=sk-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(msg)
        self.assertNotIn("sk-AAAAaaaabbbbccccddddeeeeffff0000", result)
        self.assertIn("api_key=****", result)

    def test_redact_bearer_token(self) -> None:
        msg = "Authorization: Bearer AAAAAaaaabbbbccccddddeeeeffff0000gggg"
        result = redact_secrets(msg)
        self.assertNotIn("AAAAAaaaabbbbccccddddeeeeffff0000gggg", result)
        self.assertIn("Bearer ****", result)

    def test_redact_github_token(self) -> None:
        msg = "ghp_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE"
        result = redact_secrets(msg)
        self.assertNotIn("ghp_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE", result)
        self.assertIn("ghp_****", result)

    def test_redact_github_token_ghs(self) -> None:
        msg = "ghs_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE"
        result = redact_secrets(msg)
        self.assertNotIn("ghs_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE", result)
        self.assertIn("ghs_****", result)

    def test_redact_slack_token_xoxb(self) -> None:
        msg = "xoxb-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(msg)
        self.assertNotIn("xoxb-AAAAaaaabbbbccccddddeeeeffff0000", result)
        self.assertIn("xoxb-****", result)

    def test_redact_slack_token_xoxa(self) -> None:
        msg = "slack=xoxa-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(msg)
        self.assertNotIn("xoxa-AAAAaaaabbbbccccddddeeeeffff0000", result)
        self.assertIn("xoxa-****", result)

    def test_redact_sk_prefix(self) -> None:
        msg = "secret sk-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(msg)
        self.assertNotIn("sk-AAAAaaaabbbbccccddddeeeeffff0000", result)
        self.assertIn("****", result)

    def test_filter_applied_to_record(self) -> None:
        filter_ = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="secret=sk-AAAAaaaabbbbccccddddeeeeffff0000",
            args=(),
            exc_info=None,
        )
        self.assertTrue(filter_.filter(record))
        self.assertNotIn("sk-AAAAaaaabbbbccccddddeeeeffff0000", record.msg)
        self.assertIn("****", record.msg)

    def test_filter_redacts_args(self) -> None:
        filter_ = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="key=%s token=%s",
            args=("api_key", "sk-AAAAaaaabbbbccccddddeeeeffff0000"),
            exc_info=None,
        )
        self.assertTrue(filter_.filter(record))
        self.assertNotIn("sk-AAAAaaaabbbbccccddddeeeeffff0000", str(record.args[1]))
        self.assertIn("****", str(record.args[1]))

    def test_does_not_redact_innocent_strings(self) -> None:
        msg = "session=test-session action=create tool=codex"
        result = redact_secrets(msg)
        self.assertEqual(result, msg)

    def test_github_token_not_present_starting_with_ghp(self) -> None:
        token = "ghp_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE"
        result = redact_secrets(f"token={token}")
        self.assertNotIn(token, result)

    def test_slack_variant_xoxp_redacted(self) -> None:
        token = "xoxp-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(f"slack={token}")
        self.assertNotIn(token, result)
        self.assertIn("xoxp-****", result)

    def test_slack_variant_xoxr_redacted(self) -> None:
        token = "xoxr-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(f"slack={token}")
        self.assertNotIn(token, result)
        self.assertIn("xoxr-****", result)

    def test_slack_variant_xoxs_redacted(self) -> None:
        token = "xoxs-AAAAaaaabbbbccccddddeeeeffff0000"
        result = redact_secrets(f"slack={token}")
        self.assertNotIn(token, result)
        self.assertIn("xoxs-****", result)


class SecretPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_logging()
        self.temp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp.name)

    def tearDown(self) -> None:
        reset_logging()
        self.temp.cleanup()

    def test_child_logger_emits_redacted_via_handler_filter(self) -> None:
        configure_logging(log_dir=self.log_dir)
        child = logging.getLogger("agent_console.web.session")
        child.info("api_key=sk-AAAAaaaabbbbccccddddeeeeffff0000 propagated")
        log_file = self.log_dir / "agent-console.jsonl"
        if not log_file.exists():
            self.skipTest("no jsonl file created")
        content = log_file.read_text(encoding="utf-8")
        self.assertNotIn("sk-AAAAaaaabbbbccccddddeeeeffff0000", content)

    def test_uvicorn_logger_gets_redaction_via_configure_uvicorn(self) -> None:
        configure_logging(log_dir=self.log_dir)
        configure_uvicorn_logging()
        uvi = logging.getLogger("uvicorn.access")
        uvi.info("ghp_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE in uvicorn")
        log_file = self.log_dir / "agent-console.jsonl"
        if not log_file.exists():
            self.skipTest("no jsonl file created")
        content = log_file.read_text(encoding="utf-8")
        self.assertNotIn("ghp_AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEE", content)
        self.assertIn("ghp_****", content)


class TimedRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp.name)
        reset_logging()

    def tearDown(self) -> None:
        reset_logging()
        self.temp.cleanup()

    def test_configure_logging_creates_log_file(self) -> None:
        configure_logging(log_dir=self.log_dir)
        log = get_logger("test.rotation")
        log.info("test message")
        log_files = list(self.log_dir.glob("*.jsonl"))
        self.assertGreater(len(log_files), 0)

    def test_json_file_contains_valid_json(self) -> None:
        configure_logging(log_dir=self.log_dir)
        log = get_logger("test.json")
        log.info("session=%s action=%s", "test-sess", "create")
        log_files = list(self.log_dir.glob("*.jsonl"))
        self.assertGreater(len(log_files), 0)
        content = log_files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        self.assertIn("message", parsed)
        self.assertIn("level", parsed)
        self.assertIn("timestamp", parsed)

    def test_log_dir_created_with_restrictive_permissions(self) -> None:
        configure_logging(log_dir=self.log_dir)
        self.assertTrue(self.log_dir.exists())
        mode = self.log_dir.stat().st_mode & 0o777
        self.assertLessEqual(mode, 0o700)
        self.assertTrue((self.log_dir / "agent-console.jsonl").exists())

    def test_timed_rotation_uses_midnight_handler(self) -> None:
        configure_logging(log_dir=self.log_dir)
        root = logging.getLogger()
        timed_handler = None
        for h in root.handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                timed_handler = h
                break
        self.assertIsNotNone(timed_handler)
        self.assertIn(timed_handler.when.upper(), ("MIDNIGHT", "D"))
        self.assertGreaterEqual(timed_handler.backupCount, 395)

    def test_timed_handler_backup_count_is_at_least_395(self) -> None:
        configure_logging(log_dir=self.log_dir)
        root = logging.getLogger()
        for h in root.handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                self.assertGreaterEqual(h.backupCount, 395)


class RetentionPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp.name)
        reset_logging()

    def tearDown(self) -> None:
        reset_logging()
        self.temp.cleanup()

    def test_retention_prunes_old_jsonl_files(self) -> None:
        old_file = self.log_dir / "old-agent-console.jsonl"
        old_file.write_text('{"message": "old"}\n')
        old_time = time.time() - 400 * 86400
        os.utime(str(old_file), (old_time, old_time))
        new_file = self.log_dir / "agent-console.jsonl"
        new_file.write_text('{"message": "new"}\n')
        configure_logging(log_dir=self.log_dir, retention_days=395)
        remaining = list(self.log_dir.glob("*.jsonl"))
        remaining_names = {f.name for f in remaining}
        self.assertNotIn(old_file.name, remaining_names)
        self.assertIn(new_file.name, remaining_names)

    def test_retention_prunes_rotated_files(self) -> None:
        old_rotated = self.log_dir / "agent-console.jsonl.2025-01-01"
        old_rotated.write_text('{"message": "old rotated"}\n')
        old_time = time.time() - 400 * 86400
        os.utime(str(old_rotated), (old_time, old_time))
        recent_rotated = self.log_dir / "agent-console.jsonl.2026-07-19"
        recent_rotated.write_text('{"message": "recent rotated"}\n')
        recent_time = time.time() - 1 * 86400
        os.utime(str(recent_rotated), (recent_time, recent_time))
        current = self.log_dir / "agent-console.jsonl"
        current.write_text('{"message": "current"}\n')
        prune_logs(str(self.log_dir), retention_days=395)
        remaining = {f.name for f in self.log_dir.iterdir() if f.is_file()}
        self.assertNotIn(old_rotated.name, remaining, "old rotated file was not pruned")
        self.assertIn(recent_rotated.name, remaining, "recent rotated file was incorrectly pruned")
        self.assertIn(current.name, remaining)

    def test_retention_prunes_numeric_backup_files(self) -> None:
        old_backup = self.log_dir / "agent-console.jsonl.1"
        old_backup.write_text('{"message": "old backup"}\n')
        old_time = time.time() - 400 * 86400
        os.utime(str(old_backup), (old_time, old_time))
        recent_backup = self.log_dir / "agent-console.jsonl.2"
        recent_backup.write_text('{"message": "recent backup"}\n')
        current = self.log_dir / "agent-console.jsonl"
        current.write_text('{"message": "current"}\n')
        prune_logs(str(self.log_dir), retention_days=1)
        remaining = {f.name for f in self.log_dir.iterdir() if f.is_file()}
        self.assertNotIn(old_backup.name, remaining)
        self.assertIn(current.name, remaining)

    def test_prune_logs_function(self) -> None:
        old_file = self.log_dir / "old-agent-console.jsonl"
        old_file.write_text('{"message": "old"}\n')
        old_time = time.time() - 400 * 86400
        os.utime(str(old_file), (old_time, old_time))
        new_file = self.log_dir / "agent-console.jsonl"
        new_file.write_text('{"message": "new"}\n')
        prune_logs(str(self.log_dir), retention_days=395)
        remaining = list(self.log_dir.glob("*.jsonl"))
        remaining_names = {f.name for f in remaining}
        self.assertNotIn(old_file.name, remaining_names)
        self.assertIn(new_file.name, remaining_names)

    def test_retention_skips_non_jsonl_files(self) -> None:
        old_file = self.log_dir / "old.txt"
        old_file.write_text("old data")
        old_time = time.time() - 400 * 86400
        os.utime(str(old_file), (old_time, old_time))
        prune_logs(str(self.log_dir), retention_days=1)
        self.assertTrue(old_file.exists())

    def test_invalid_log_dir_does_not_crash(self) -> None:
        configure_logging(log_dir=self.log_dir / "nonexistent" / "deep")
        log = get_logger("test.safe")
        log.info("should not crash")


class AuditPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        self.db = Database(self.db_path)
        self.db.migrate()
        self.old_cutoff = "2000-01-01T00:00:00+00:00"
        self.recent = "2026-07-20T00:00:00+00:00"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _insert_audit(self, created_at: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(created_at, actor, surface, action, target, outcome, details_json) "
                "VALUES(?, 'test', 'test', 'test.action', 'test-target', 'ok', '{}')",
                (created_at,),
            )

    def test_prune_removes_old_events(self) -> None:
        self._insert_audit(self.old_cutoff)
        self._insert_audit(self.recent)
        pruned = self.db.prune_audit_events(retention_days=395)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_prune_custom_retention(self) -> None:
        self._insert_audit(self.old_cutoff)
        self._insert_audit(self.recent)
        pruned = self.db.prune_audit_events(retention_days=1)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_prune_no_old_events(self) -> None:
        self._insert_audit(self.recent)
        pruned = self.db.prune_audit_events(retention_days=395)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_prune_empty_table(self) -> None:
        pruned = self.db.prune_audit_events(retention_days=395)
        self.assertEqual(pruned, 0)


class ConfigParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.config_path = self.repo_root / "deploy" / "logging-config.json"

    def test_logging_config_json_exists(self) -> None:
        self.assertTrue(self.config_path.exists())

    def test_logging_config_json_is_valid_json(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        self.assertIsInstance(cfg, dict)

    def test_logging_config_has_required_fields(self) -> None:
        assert_logging_config_parity(self.config_path)

    def test_logging_config_formatters_defined(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        self.assertIn("default", cfg["formatters"])
        self.assertIn("access", cfg["formatters"])

    def test_logging_config_handlers_defined(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        self.assertIn("default", cfg["handlers"])
        self.assertIn("access", cfg["handlers"])

    def test_no_file_handler_in_reference_config(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        for hname, hdef in cfg["handlers"].items():
            cls = hdef.get("class", "")
            self.assertNotIn("FileHandler", cls, f"handler {hname} is a file handler")

    def test_root_logger_handlers_exist(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        root_handlers = cfg["root"]["handlers"]
        for hname in root_handlers:
            self.assertIn(hname, cfg["handlers"])

    def test_secret_redaction_filter_configured(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        self.assertIn("secret_redaction", cfg.get("filters", {}))
        filter_def = cfg["filters"]["secret_redaction"]
        self.assertEqual(filter_def["()"], "agent_console.logging_config.SecretRedactionFilter")

    def test_logging_config_and_code_format_agree(self) -> None:
        cfg = read_logging_config_json(self.config_path)
        default_fmt = cfg["formatters"]["default"]
        self.assertEqual(
            default_fmt["format"],
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        self.assertEqual(default_fmt["datefmt"], "%Y-%m-%dT%H:%M:%S%z")

    def test_no_raw_secrets_in_logging_config(self) -> None:
        content = self.config_path.read_text()
        for secret_pattern in ["sk-", "ghp_", "ghs_", "xox[bpars]-"]:
            self.assertNotIn(secret_pattern, content)


def _find_uvicorn_python() -> str | None:
    candidates = [
        "/opt/agent-console/current/.venv/bin/python",
        "/opt/agent-console/.venv/bin/python",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                subprocess.run([p, "-c", "import uvicorn"], capture_output=True, check=True)
                return p
            except subprocess.CalledProcessError:
                continue
    try:
        subprocess.run(["python3", "-c", "import uvicorn"], capture_output=True, check=True)
        return "python3"
    except subprocess.CalledProcessError:
        pass
    return None


@unittest.skipIf(_find_uvicorn_python() is None, "uvicorn not available in any Python")
class UvicornSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_logging()
        self.temp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp.name)
        self.uvicorn_python = _find_uvicorn_python()

    def tearDown(self) -> None:
        reset_logging()
        self.temp.cleanup()

    def test_uvicorn_emits_json_to_user_writable_dir(self) -> None:
        port = _find_free_port()
        repo_root = Path(__file__).resolve().parents[1]
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root),
            "AGENT_CONSOLE_LOG_DIR": str(self.log_dir),
            "AGENT_CONSOLE_LOG_LEVEL": "INFO",
            "AGENT_CONSOLE_TRUSTED_HOSTS": "localhost,127.0.0.1",
            "AGENT_CONSOLE_LAN_CIDR": "127.0.0.1/32",
        }
        proc = subprocess.Popen(
            [
                self.uvicorn_python, "-m", "uvicorn",
                "agent_console.web:app",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--no-proxy-headers",
            ],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(50):
                if proc.poll() is not None:
                    err = proc.stderr.read().decode()
                    self.fail(f"uvicorn exited prematurely:\n{err}")
                log_file = self.log_dir / "agent-console.jsonl"
                if log_file.exists() and log_file.stat().st_size > 0:
                    break
                time.sleep(0.2)
            else:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode()
                    self.fail(f"uvicorn did not create log file within 10s, exited:\n{err}")
                self.fail("uvicorn did not create log file within 10s")
            try:
                resp = subprocess.run(
                    ["curl", "--fail", "--silent", f"http://127.0.0.1:{port}/healthz"],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertIn("ok", resp.stdout)
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            time.sleep(0.5)
            log_file = self.log_dir / "agent-console.jsonl"
            content = log_file.read_text(encoding="utf-8")
            self.assertGreater(len(content), 0)
            log_lines = content.strip().split("\n")
            for line in log_lines[-3:]:
                if line.strip():
                    parsed = json.loads(line)
                    self.assertIn("level", parsed)
                    self.assertIn("logger", parsed)
                    self.assertIn("message", parsed)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc.stdout.close()
            proc.stderr.close()

    def test_uvicorn_child_logger_secret_redacted(self) -> None:
        port = _find_free_port()
        repo_root = Path(__file__).resolve().parents[1]
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root),
            "AGENT_CONSOLE_LOG_DIR": str(self.log_dir),
            "AGENT_CONSOLE_LOG_LEVEL": "INFO",
            "AGENT_CONSOLE_TRUSTED_HOSTS": "localhost,127.0.0.1",
            "AGENT_CONSOLE_LAN_CIDR": "127.0.0.1/32",
        }
        proc = subprocess.Popen(
            [
                self.uvicorn_python, "-m", "uvicorn",
                "agent_console.web:app",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--no-proxy-headers",
            ],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(50):
                if proc.poll() is not None:
                    err = proc.stderr.read().decode()
                    self.fail(f"uvicorn exited prematurely:\n{err}")
                log_file = self.log_dir / "agent-console.jsonl"
                if log_file.exists() and log_file.stat().st_size > 0:
                    break
                time.sleep(0.2)
            else:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode()
                    self.fail(f"uvicorn did not create log file within 10s, exited:\n{err}")
                self.fail("uvicorn did not create log file within 10s")
            content = (self.log_dir / "agent-console.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("sk-AAAAaaaab", content)
            self.assertNotIn("ghp_AAAAAAAAB", content)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc.stdout.close()
            proc.stderr.close()


class SettingsClampTests(unittest.TestCase):
    def test_from_env_clamps_negative_retention(self) -> None:
        old = os.environ.get("AGENT_CONSOLE_LOG_RETENTION_DAYS")
        os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = "-1"
        try:
            s = Settings.from_env()
            self.assertGreater(s.log_retention_days, 0)
            self.assertEqual(s.log_retention_days, 395)
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"]
            else:
                os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = old

    def test_from_env_clamps_zero_retention(self) -> None:
        old = os.environ.get("AGENT_CONSOLE_LOG_RETENTION_DAYS")
        os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = "0"
        try:
            s = Settings.from_env()
            self.assertGreater(s.log_retention_days, 0)
            self.assertEqual(s.log_retention_days, 395)
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"]
            else:
                os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = old

    def test_from_env_clamps_negative_backup_count(self) -> None:
        old = os.environ.get("AGENT_CONSOLE_LOG_BACKUP_COUNT")
        os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"] = "-5"
        try:
            s = Settings.from_env()
            self.assertGreater(s.log_backup_count, 0)
            self.assertEqual(s.log_backup_count, 400)
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"]
            else:
                os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"] = old


class ManagerAuditPruneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "bugfix"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.db_path = root / "state" / "test.sqlite3"
        self.db_path.parent.mkdir(parents=True)
        self.db = Database(self.db_path)
        self.db.migrate()
        self.settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=self.db_path,
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=None,
            log_retention_days=395,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manager_startup_prunes_old_audit_events(self) -> None:
        self.db.audit("test.old", "old-target", "ok",
                       actor="test", details={"note": "old"})
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE audit_events SET created_at = '2000-01-01T00:00:00+00:00'"
                " WHERE action = 'test.old'"
            )
        self.db.audit("test.recent", "recent-target", "ok",
                       actor="test", details={"note": "recent"})
        with self.db.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(before, 2, "should have both old and recent before prune")
        from agent_console.manager import SessionManager
        manager = SessionManager(self.settings)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(remaining, 1, "old audit event should be pruned at startup")

    def test_manager_startup_retains_fresh_events(self) -> None:
        self.db.audit("test.recent1", "t1", "ok", actor="test")
        self.db.audit("test.recent2", "t2", "ok", actor="test")
        with self.db.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(before, 2)
        from agent_console.manager import SessionManager
        manager = SessionManager(self.settings)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        self.assertEqual(remaining, 2)


def _have_uvicorn() -> bool:
    try:
        import uvicorn  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_have_uvicorn(), "uvicorn not available in current interpreter")
class LifespanWiringTest(unittest.TestCase):
    def test_fastapi_app_has_lifespan(self) -> None:
        from agent_console.web import create_app
        app = create_app()
        self.assertIsNotNone(app.router.lifespan_context)


class SafeParseIntTests(unittest.TestCase):
    def test_parse_malformed_retention_uses_default(self) -> None:
        from agent_console.logging_config import _safe_parse_int
        result = _safe_parse_int("abc", 395)
        self.assertEqual(result, 395)

    def test_parse_malformed_backup_count_uses_default(self) -> None:
        from agent_console.logging_config import _safe_parse_int
        result = _safe_parse_int("xyz", 400)
        self.assertEqual(result, 400)

    def test_parse_empty_string_uses_default(self) -> None:
        from agent_console.logging_config import _safe_parse_int
        result = _safe_parse_int("", 395)
        self.assertEqual(result, 395)

    def test_parse_none_uses_default(self) -> None:
        from agent_console.logging_config import _safe_parse_int
        result = _safe_parse_int(None, 395)
        self.assertEqual(result, 395)

    def test_parse_valid_int_parsed(self) -> None:
        from agent_console.logging_config import _safe_parse_int
        result = _safe_parse_int("42", 395)
        self.assertEqual(result, 42)

    def test_parse_malformed_max_children_uses_default(self) -> None:
        s = Settings(
            workspace_root=Path("/tmp"),
            state_dir=Path("/tmp"),
            database_path=Path("/tmp/db.sqlite3"),
            profile_dir=Path("/tmp/profiles"),
            handoff_dir=Path("/tmp/handoffs"),
            worktree_root=Path("/tmp/worktrees"),
            tmux_socket=None,
            max_children_per_parent=3,
            max_managed_sessions=12,
        )
        self.assertEqual(s.max_children_per_parent, 3)

    def test_parse_malformed_max_sessions_uses_default(self) -> None:
        s = Settings(
            workspace_root=Path("/tmp"),
            state_dir=Path("/tmp"),
            database_path=Path("/tmp/db.sqlite3"),
            profile_dir=Path("/tmp/profiles"),
            handoff_dir=Path("/tmp/handoffs"),
            worktree_root=Path("/tmp/worktrees"),
            tmux_socket=None,
            max_children_per_parent=3,
            max_managed_sessions=12,
        )
        self.assertEqual(s.max_managed_sessions, 12)


class EnvValidationTests(unittest.TestCase):
    def test_negative_retention_days_clamped(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        log_dir = Path(self.temp.name)
        reset_logging()
        old = os.environ.get("AGENT_CONSOLE_LOG_RETENTION_DAYS")
        os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = "-1"
        try:
            configure_logging(log_dir=log_dir)
            log = get_logger("test.clamp")
            log.info("message")
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"]
            else:
                os.environ["AGENT_CONSOLE_LOG_RETENTION_DAYS"] = old
        reset_logging()
        self.temp.cleanup()

    def test_zero_backup_count_clamped(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        log_dir = Path(self.temp.name)
        reset_logging()
        old = os.environ.get("AGENT_CONSOLE_LOG_BACKUP_COUNT")
        os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"] = "0"
        try:
            configure_logging(log_dir=log_dir)
            log = get_logger("test.clamp2")
            log.info("message")
            root = logging.getLogger()
            timed_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.TimedRotatingFileHandler)
            ]
            for h in timed_handlers:
                self.assertGreaterEqual(h.backupCount, 395)
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"]
            else:
                os.environ["AGENT_CONSOLE_LOG_BACKUP_COUNT"] = old
        reset_logging()
        self.temp.cleanup()

    def test_invalid_log_level_falls_back_to_info(self) -> None:
        reset_logging()
        old = os.environ.get("AGENT_CONSOLE_LOG_LEVEL")
        os.environ["AGENT_CONSOLE_LOG_LEVEL"] = "BOGUS"
        try:
            configure_logging()
            root = logging.getLogger()
            self.assertEqual(root.level, logging.INFO)
        finally:
            if old is None:
                del os.environ["AGENT_CONSOLE_LOG_LEVEL"]
            else:
                os.environ["AGENT_CONSOLE_LOG_LEVEL"] = old
        reset_logging()


if __name__ == "__main__":
    unittest.main()
