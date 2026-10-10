"""Jev ghost probe review route: reader, aggregation and route contract.

Offline only: no API calls, no live probe state required.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_console.jev_ghost import default_dir, install, load_runs, snapshot


def _run(at: str, scenario: str, status: str, **extra) -> dict:
    run = {"at": at, "cycle": 0, "scenario": scenario, "status": status,
           "http_status": 200, "model": "jev-test", "checks_passed": 3,
           "checks_total": 3, "failed_checks": [], "duration_ms": 12,
           "typed": {"intent": {"type": "choice", "choice": "request", "confidence": 0.9}},
           "skill_paths": {"canonical": True, "isolated_roots_with_skill": 1},
           "key_present": True}
    run.update(extra)
    return run


class JevGhostReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_runs(self, runs: list[dict], *, trailing_junk: bool = False) -> None:
        lines = [json.dumps(run) for run in runs]
        if trailing_junk:
            lines.append("{not json")
        (self.directory / "runs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_default_dir_honours_env(self) -> None:
        with patch.dict(os.environ, {"AGENT_CONSOLE_JEV_GHOST_DIR": "/tmp/jev-ghost-test"}):
            self.assertEqual(default_dir(), Path("/tmp/jev-ghost-test"))
        with patch.dict(os.environ, {"AGENT_CONSOLE_STATE_DIR": "/srv/state"}, clear=False):
            os.environ.pop("AGENT_CONSOLE_JEV_GHOST_DIR", None)
            self.assertEqual(default_dir(), Path("/srv/state/jev-ghost"))

    def test_missing_directory_is_available_false(self) -> None:
        data = snapshot(self.directory / "absent")
        self.assertFalse(data["available"])
        self.assertEqual(data["runs"], [])
        self.assertIsNone(data["latest"])

    def test_load_runs_newest_first_and_skips_junk(self) -> None:
        self._write_runs([_run("2026-09-18T00:00:00+00:00", "a", "pass"),
                          _run("2026-09-18T01:00:00+00:00", "b", "fail")],
                         trailing_junk=True)
        runs = load_runs(self.directory)
        self.assertEqual([r["scenario"] for r in runs], ["b", "a"])
        self.assertEqual(len(runs), 2)

    def test_load_runs_applies_limit(self) -> None:
        self._write_runs([_run(f"2026-09-18T0{i}:00:00+00:00", f"s{i}", "pass") for i in range(5)])
        self.assertEqual(len(load_runs(self.directory, limit=2)), 2)

    def test_clean_run_drops_unknown_fields(self) -> None:
        self._write_runs([_run("2026-09-18T00:00:00+00:00", "a", "pass", secret="leak")])
        self.assertNotIn("secret", load_runs(self.directory)[0])

    def test_snapshot_reads_summary_and_shared_skills(self) -> None:
        self._write_runs([_run("2026-09-18T00:00:00+00:00", "a", "pass")])
        (self.directory / "summary.json").write_text(json.dumps({"total_runs": 1, "pass_ratio": 1.0}))
        with patch.dict(os.environ, {"AGCONSOLE_SHARED_SKILLS": "typesafe-ai, other"}):
            data = snapshot(self.directory)
        self.assertTrue(data["available"])
        self.assertEqual(data["summary"]["total_runs"], 1)
        self.assertEqual(data["shared_skills"], ["typesafe-ai", "other"])
        self.assertEqual(data["latest"]["scenario"], "a")
        self.assertTrue(data["latest"]["skill_paths"]["canonical"])


class JevGhostRouteTests(unittest.TestCase):
    def _client(self, directory: Path) -> TestClient:
        app = FastAPI()

        def identity() -> str:
            return "test-actor"

        install(app, identity, directory=directory)
        return TestClient(app)

    def test_route_returns_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "runs.jsonl").write_text(
                json.dumps(_run("2026-09-18T00:00:00+00:00", "a", "pass")) + "\n")
            response = self._client(directory).get("/api/jev-ghost")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual(len(body["runs"]), 1)
        self.assertEqual(body["runs"][0]["status"], "pass")

    def test_route_without_history_is_empty_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            response = self._client(Path(name)).get("/api/jev-ghost?limit=5")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])
        self.assertEqual(response.json()["runs"], [])


if __name__ == "__main__":
    unittest.main()
