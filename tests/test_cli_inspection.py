from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from agent_console import cli
from agent_console.config import Settings
from agent_console.database import Database
from agent_console.inspection import InspectionUnavailable
from agent_console.inspection_views import (READ_ROUTES, inspection_route, InspectionViews,
    TmuxObservation, _bounded_run, _read_file)


def fingerprint(root):
    return {str(path.relative_to(root)): (path.lstat().st_mode, path.lstat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            for path in root.rglob("*") if not path.is_symlink()}


class CliInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cli-inspection93-")
        self.root = Path(self.temp.name)
        self.env = {"PATH": os.defpath, "HOME": str(self.root / "absent-home"),
            "AGENT_CONSOLE_STATE_DIR": str(self.root / "state"),
            "AGENT_CONSOLE_DB": str(self.root / "state" / "db.sqlite3"),
            "AGENT_CONSOLE_CONFIG_DIR": str(self.root / "absent-config"),
            "AGENT_CONSOLE_WORKSPACE_ROOT": str(self.root),
            "AGENT_CONSOLE_PROFILE_DIR": str(self.root / "profiles"),
            "AGENT_CONSOLE_TMUX_SOCKET_PATH": str(self.root / "absent.sock"),
            "AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH": str(self.root / "absent-legacy.sock")}
        self.environment = patch.dict(os.environ, self.env, clear=True); self.environment.start()
        self.settings = Settings.from_env()
        Database(self.settings.database_path).migrate()
        self.writer = sqlite3.connect(self.settings.database_path)
        self.writer.execute("INSERT INTO sessions(id,tmux_name,created_at,status,tool,profile,attention_state) VALUES('s','one','2026-09-13','detached','codex-pro','reviewer','normal')")
        self.writer.execute("INSERT INTO session_groups(id,name,created_at) VALUES('g','Group','2026-09-13')")
        self.writer.execute("INSERT INTO group_members(id,group_id,session_id,added_at) VALUES('m','g','s','2026-09-13')")
        self.writer.commit()
        (self.root / "profiles").mkdir()
        (self.root / "profiles" / "reviewer.md").write_text("# Reviewer\n")

    def tearDown(self):
        self.writer.close(); self.environment.stop(); self.temp.cleanup()

    def invoke(self, argv):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = cli.main(argv)
        return code, out.getvalue(), error.getvalue()

    def test_every_parsed_leaf_has_only_the_exact_nine_read_routes(self):
        found, excluded = set(), []
        def visit(parser, values):
            sub = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
            if not sub:
                route = inspection_route(argparse.Namespace(**values))
                if route: found.add(route)
                else: excluded.append(values)
            for action in sub:
                for name, child in action.choices.items(): visit(child, {**values, action.dest: name})
        visit(cli.parser(), {})
        self.assertEqual(found, READ_ROUTES); self.assertEqual(len(found), 9)
        self.assertGreater(len(excluded), 30)
        self.assertTrue(any(item.get("integration_command") == "plan-status" for item in excluded))

    def test_all_read_commands_bypass_every_writer_constructor(self):
        commands = [["session", "list"], ["session", "inspect", "one"], ["session", "tree", "--json"],
            ["session", "review", "one", "--json"], ["session", "context", "one"],
            ["session", "group", "list"], ["session", "group", "show", "g"],
            ["profile", "list"], ["profile", "inspect", "reviewer"]]
        before = fingerprint(self.root)
        with patch.object(cli, "SessionManager", side_effect=AssertionError("writer constructor")), \
             patch("agent_console.auth.AuthRegistry", side_effect=AssertionError("auth constructor")), \
             patch.object(Database, "connect", side_effect=AssertionError("writer connect")), \
             patch.object(Database, "migrate", side_effect=AssertionError("migration")):
            for command in commands:
                with self.subTest(command=command):
                    code, out, error = self.invoke(command)
                    self.assertEqual(code, 0, error); json.loads(out)
        self.assertEqual(fingerprint(self.root), before)

    def test_integration_status_and_submit_still_use_writer_service(self):
        for verb, method in [("plan-status", "status"), ("plan-request", "submit")]:
            writer, service = Mock(), Mock()
            getattr(service, method).return_value = ({"state": "accepted"}, 0)
            with patch.object(cli, "SessionManager", return_value=writer) as constructor, \
                 patch.object(cli, "IntegrationService", return_value=service) as factory, \
                 patch.object(cli.sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"{}")})()), \
                 patch.object(cli, "read_route", side_effect=AssertionError("reader selected")):
                self.assertEqual(self.invoke(["integration", verb, "--stdin"])[0], 0)
                constructor.assert_called_once_with(); factory.assert_called_once_with(writer)
                getattr(service, method).assert_called_once_with(b"{}")

    def test_earlier_skills_branch_remains_writer_owned(self):
        manager = Mock()
        with patch.object(cli, "SessionManager", return_value=manager) as constructor, \
             patch.object(cli, "get_effective_skills", return_value={}) as effective:
            self.assertEqual(self.invoke(["skills", "effective", "reviewer"])[0], 0)
            constructor.assert_called_once(); effective.assert_called_once_with(manager.database, "reviewer")

    def test_stale_lifecycle_and_integration_content_do_not_become_receipts(self):
        self.writer.execute("UPDATE sessions SET execution_kind='integration-plan', initial_task='FROZEN SECRET', attention_note='FROZEN SECRET', exit_reason='FROZEN SECRET', archived_transcript=?", (str(self.root / "secret"),))
        self.writer.commit(); (self.root / "secret").write_text("FROZEN SECRET")
        before = fingerprint(self.root)
        for command in [["session", "inspect", "one"], ["session", "context", "one"], ["session", "review", "one", "--json"]]:
            code, out, error = self.invoke(command)
            self.assertEqual(code, 0, error); self.assertNotIn("FROZEN", out)
        result = json.loads(self.invoke(["session", "inspect", "one"])[1])
        self.assertEqual(result["status"], "detached"); self.assertFalse(result["running"])
        self.assertEqual(result["observed_status"], "not-observed")
        self.assertNotIn("receipt", result); self.assertEqual(result["actions"], [])
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_database_does_not_block_profile_reads_or_create_state(self):
        with patch.dict(os.environ, {"AGENT_CONSOLE_DB": str(self.root / "missing" / "db")}):
            before = fingerprint(self.root)
            self.assertEqual(self.invoke(["profile", "inspect", "reviewer"])[0], 0)
            code, out, error = self.invoke(["session", "tree", "--json"])
            self.assertEqual(code, 2); self.assertEqual(json.loads(out)["error"]["code"], "state-unavailable")
            self.assertEqual(fingerprint(self.root), before)

    def test_context_and_transcript_symlink_escapes_fail_before_read(self):
        outside = self.root / "outside"; outside.write_text("DO NOT READ")
        contexts = self.root / "state" / "contexts"; contexts.mkdir()
        (contexts / "one.md").symlink_to(outside)
        self.assertEqual(self.invoke(["session", "context", "one"])[0], 2)
        transcript = self.root / "state" / "transcript"; transcript.symlink_to(outside)
        self.writer.execute("UPDATE sessions SET archived_transcript=?", (str(transcript),)); self.writer.commit()
        code, out, error = self.invoke(["session", "review", "one", "--json"])
        self.assertEqual(code, 2); self.assertNotIn("DO NOT READ", out + error)

    def test_review_bounded_tail_and_human_output(self):
        transcript = self.root / "state" / "transcript"
        transcript.write_text("x" * 300000 + "\nlast\n")
        self.writer.execute("UPDATE sessions SET archived_transcript=?", (str(transcript),)); self.writer.commit()
        result = json.loads(self.invoke(["session", "review", "one", "--json", "--lines", "1"])[1])
        self.assertEqual(result["content"], "last\n"); self.assertTrue(result["truncated"])
        self.assertEqual(result["capture_scope"], "archived")
        code, text, error = self.invoke(["session", "review", "one", "--lines", "1"])
        self.assertEqual(code, 0, error); self.assertIn("--- peer terminal output begins ---", text)
        self.assertIn("untrusted", text)
        self.assertEqual(self.invoke(["session", "review", "one", "--lines", "1001"])[0], 2)

    def test_tree_cycle_is_unavailable_not_infinite_recursion(self):
        self.writer.execute("UPDATE sessions SET parent_session_id='s'"); self.writer.commit()
        self.assertEqual(self.invoke(["session", "tree"])[0], 2)

    @unittest.skipUnless(shutil.which("tmux"), "real tmux required")
    def test_real_tmux_read_capture_and_ephemeral_session_never_reconcile(self):
        sock = self.root / "fixture.sock"
        command = [shutil.which("tmux"), "-f", "/dev/null", "-S", str(sock)]
        subprocess.run(command + ["new-session", "-d", "-s", "one", "printf 'inspection-fixture'; sleep 60"], check=True)
        subprocess.run(command + ["new-session", "-d", "-s", "unmanaged", "sleep 60"], check=True)
        try:
            with patch.dict(os.environ, {"AGENT_CONSOLE_TMUX_SOCKET_PATH": str(sock)}):
                before = fingerprint(self.root)
                views = InspectionViews(Settings.from_env())
                self.assertTrue(views.inspect("one")["running"])
                self.assertFalse(views.inspect("unmanaged")["managed"])
                self.assertTrue(views.inspect("unmanaged")["id"].startswith("observed:"))
                review = views.review("one", 100)
                self.assertEqual(review["source"], "live-pane")
                self.assertIn("inspection-fixture", review["content"])
                self.assertEqual(fingerprint(self.root), before)
                self.assertEqual(self.writer.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        finally:
            subprocess.run(command + ["kill-server"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_observation_failures_never_mean_all_sessions_stopped(self):
        sockpath = self.root / "fixture.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(sockpath))
            settings = replace(self.settings, tmux_socket_path=sockpath)
            for response in [(1, "", "permission denied"), (0, "malformed", "")]:
                with patch("agent_console.inspection_views._bounded_run", return_value=response):
                    with self.assertRaises(InspectionUnavailable): TmuxObservation(settings)
            with patch("agent_console.inspection_views._bounded_run", return_value=(1, "", "no server running on fixture")):
                self.assertEqual(TmuxObservation(settings).sessions, {})

    def test_socket_collision_is_explicit(self):
        a, b = self.root / "a.sock", self.root / "b.sock"
        with socket.socket(socket.AF_UNIX) as sa, socket.socket(socket.AF_UNIX) as sb:
            sa.bind(str(a)); sb.bind(str(b))
            settings = replace(self.settings, tmux_socket_path=a, legacy_tmux_socket_path=b)
            with patch("agent_console.inspection_views._bounded_run", return_value=(0, "one|1|2|0|bash\n", "")):
                with self.assertRaises(InspectionUnavailable): TmuxObservation(settings)

    def test_bounded_client_kills_only_noisy_or_hung_probe(self):
        with self.assertRaisesRegex(InspectionUnavailable, "observation-unavailable"):
            _bounded_run([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(30)"], timeout=0.1)
        with self.assertRaisesRegex(InspectionUnavailable, "observation-too-large"):
            _bounded_run([sys.executable, "-I", "-S", "-B", "-c", "import os;os.write(1,b'x'*300000)"], timeout=2)


if __name__ == "__main__": unittest.main()
