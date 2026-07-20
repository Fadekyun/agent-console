from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.database import EVIDENCE_TYPES, EVIDENCE_RESULTS, REQUIRED_EVIDENCE_TYPES


def _setup_plan(manager: SessionManager, artifact_name: str) -> dict:
    workspace = Path(manager.settings.workspace_root)
    repository = workspace / artifact_name
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "gate-test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Gate Test"],
        check=True,
    )
    (repository / "README.md").write_text("gate fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "gate fixture"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    art = manager.settings.handoff_dir / artifact_name
    art.mkdir(parents=True)
    (art / "plan.md").write_text("# Gate test plan\n", encoding="utf-8")
    (art / "metadata.json").write_text(
        json.dumps({"title": artifact_name, "repository": str(repository),
                     "repository_revision": revision, "profile": "planner"}),
        encoding="utf-8",
    )
    return {"repository": repository, "revision": revision, "plan_id": artifact_name}


def _read_capability(manager: SessionManager, session_name: str) -> str:
    path = manager.settings.state_dir / "launchers" / f"{session_name}.sh"
    text = path.read_text(encoding="utf-8")
    m = re.search(r"export AGENT_CONSOLE_EVIDENCE_CAPABILITY=(\S+)", text)
    if m:
        return shlex.split(m.group(1))[0]
    raise ValueError(f"capability not found in {path}")


@unittest.skipUnless(
    subprocess.run(["sh", "-c", "command -v tmux"], capture_output=True).returncode == 0,
    "tmux required",
)
class ReleaseGateProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "reviewer", "verifier", "scout"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"gate-prov-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            max_children_per_parent=4,
            max_managed_sessions=12,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")
        self.plan = _setup_plan(self.manager, "prov-gate")
        self.sha = self.plan["revision"]
        self.plan_id = self.plan["plan_id"]

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        os.environ.pop("AGENT_CONSOLE_EVIDENCE_CAPABILITY", None)
        self.temp.cleanup()

    def _create_session(self, profile: str, name: str) -> dict:
        session = self.manager.create(
            tool="shell", profile=profile, name=name,
            repository=str(self.workspace),
        )
        self.manager.set_attention(session["tmux_name"], state="ready_for_review", actor="test")
        return self.manager.inspect(name)

    def _capability(self, session_name: str) -> str:
        return _read_capability(self.manager, session_name)

    def _record_evidence(self, plan_id: str, cap: str, etype: str,
                         result: str, detail: str | None = None) -> dict:
        return self.manager.record_evidence(
            plan_id, evidence_type=etype, result=result,
            candidate_sha=self.sha, detail=detail, capability=cap,
        )

    def _record_full_evidence(self) -> None:
        rev = self._create_session("reviewer", f"rev-{int(time.time())}")
        sc = self._create_session("scout", f"sc-{int(time.time())}")
        ver = self._create_session("verifier", f"ver-{int(time.time())}")
        self._record_evidence(self.plan_id, self._capability(rev["tmux_name"]), "review", "pass")
        self._record_evidence(self.plan_id, self._capability(sc["tmux_name"]), "scout", "pass")
        self._record_evidence(self.plan_id, self._capability(ver["tmux_name"]), "verification", "pass")

    # --- Provenance: capability (Fix 1) ---

    def test_capability_missing_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass", candidate_sha=self.sha,
            )
        self.assertIn("evidence capability", str(ctx.exception))

    def test_capability_invalid_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass",
                candidate_sha=self.sha, capability="deadbeef",
            )
        self.assertIn("invalid evidence capability", str(ctx.exception))

    def test_capability_wrong_profile_raises(self) -> None:
        sess = self._create_session("coder", "coder-cap-ev")
        cap = self._capability(sess["tmux_name"])
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass",
                candidate_sha=self.sha, capability=cap,
            )
        self.assertIn("requires profile 'reviewer'", str(ctx.exception))

    def test_capability_blocked_session_raises(self) -> None:
        sess = self._create_session("verifier", "ver-cap-blocked")
        self.manager.set_attention(sess["tmux_name"], state="blocked", actor="test")
        cap = self._capability(sess["tmux_name"])
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="verification", result="pass",
                candidate_sha=self.sha, capability=cap,
            )
        self.assertIn("attention_state", str(ctx.exception))

    def test_capability_valid_evidence_accepted(self) -> None:
        self._record_full_evidence()
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertTrue(gate["allowed"])

    def test_capability_not_in_list_inspect_or_api(self) -> None:
        rev = self._create_session("reviewer", "rev-secret-out")
        cap = self._capability(rev["tmux_name"])
        self._record_evidence(self.plan_id, cap, "review", "pass")
        for session in self.manager.list_sessions():
            self.assertNotIn(cap, str(session))
            self.assertNotIn("evidence_capability_hash", session)
        inspected = self.manager.inspect(rev["tmux_name"])
        self.assertNotIn(cap, str(inspected))
        self.assertNotIn("evidence_capability_hash", inspected)

    def test_capability_not_in_api_log_or_db(self) -> None:
        rev = self._create_session("reviewer", "rev-cap-secret")
        cap = self._capability(rev["tmux_name"])
        result = self._record_evidence(self.plan_id, cap, "review", "pass")
        self.assertNotIn(cap, str(result))
        with self.manager.database.connect() as conn:
            rows = conn.execute(
                "SELECT details_json FROM audit_events WHERE action='plan.evidence.recorded'"
            ).fetchall()
            for row in rows:
                self.assertNotIn(cap, row["details_json"])

    def test_capability_not_in_evidence_list(self) -> None:
        rev = self._create_session("reviewer", "rev-ev-list")
        cap = self._capability(rev["tmux_name"])
        self._record_evidence(self.plan_id, cap, "review", "pass")
        records = self.manager.list_evidence(self.plan_id)
        for record in records:
            self.assertNotIn(cap, str(record))

    def test_launcher_file_is_owner_only(self) -> None:
        rev = self._create_session("reviewer", "rev-launcher-perm")
        launcher = self.manager.settings.state_dir / "launchers" / f"{rev['tmux_name']}.sh"
        self.assertTrue(launcher.is_file())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
        context = self.manager.settings.state_dir / "contexts" / f"{rev['tmux_name']}.md"
        self.assertTrue(context.is_file())
        self.assertEqual(context.stat().st_mode & 0o777, 0o600)

    def test_capability_forged_name_not_accepted(self) -> None:
        rev = self._create_session("reviewer", "rev-forge-test")
        real_cap = self._capability(rev["tmux_name"])
        os.environ["AGENT_CONSOLE_EVIDENCE_CAPABILITY"] = real_cap
        coder = self._create_session("coder", "coder-forge-test")
        os.environ["AGENT_CONSOLE_SESSION_NAME"] = coder["tmux_name"]
        try:
            result = self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass",
                candidate_sha=self.sha,
            )
            self.assertEqual(result["session_name"], rev["tmux_name"])
            self.assertNotEqual(result["session_name"], coder["tmux_name"])
        finally:
            os.environ.pop("AGENT_CONSOLE_EVIDENCE_CAPABILITY", None)
            os.environ.pop("AGENT_CONSOLE_SESSION_NAME", None)

    def test_capability_unmanaged_session_raises(self) -> None:
        sess = self.manager.create(
            tool="shell", profile="reviewer", name="rev-unmanaged-cap",
            repository=str(self.workspace),
        )
        self.manager.set_attention(sess["tmux_name"], state="ready_for_review", actor="test")
        cap = self._capability(sess["tmux_name"])
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE sessions SET managed=0, evidence_capability_hash=NULL WHERE tmux_name=?",
                (sess["tmux_name"],),
            )
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass",
                candidate_sha=self.sha, capability=cap,
            )
        self.assertIn("invalid evidence capability", str(ctx.exception))

    # --- Candidate integrity (Fix 2) ---

    def test_promote_rejects_changed_revision(self) -> None:
        repo = Path(self.plan["repository"])
        (repo / "CHANGE.md").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "CHANGE.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change"], check=True)
        with self.assertRaises(ValueError) as ctx:
            self.manager.promote_plan(self.plan_id)
        self.assertIn("revision state", str(ctx.exception))

    def test_gate_rejects_changed_revision(self) -> None:
        repo = Path(self.plan["repository"])
        (repo / "CHANGE2.md").write_text("change\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "CHANGE2.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change2"], check=True)
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "revision_changed")

    def test_promote_with_wrong_candidate_sha_raises(self) -> None:
        bad_sha = "a" * 40
        with self.assertRaises(ValueError) as ctx:
            self.manager.promote_plan(self.plan_id, candidate_sha=bad_sha)
        self.assertIn("does not match", str(ctx.exception))

    # --- Deterministic ordering (Fix 3) ---

    def test_evidence_ordering_is_deterministic(self) -> None:
        rev = self._create_session("reviewer", "rev-ordering")
        sc = self._create_session("scout", "sc-ordering")
        ver = self._create_session("verifier", "ver-ordering")
        self._record_evidence(self.plan_id, self._capability(rev["tmux_name"]),
                              "review", "pass", detail="review pass")
        self._record_evidence(self.plan_id, self._capability(sc["tmux_name"]),
                              "scout", "pass", detail="scout pass")
        self._record_evidence(self.plan_id, self._capability(ver["tmux_name"]),
                              "verification", "pass", detail="verification pass")
        first = self.manager.list_evidence(self.plan_id)
        second = self.manager.list_evidence(self.plan_id)
        self.assertEqual(len(first), 3)
        self.assertSequenceEqual(
            [(r["id"], r["evidence_type"]) for r in first],
            [(r["id"], r["evidence_type"]) for r in second],
        )

    def test_gate_uses_recorded_revision_not_head(self) -> None:
        self._record_full_evidence()
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["candidate_sha"], self.sha)

    # --- Release-selection boundary (Fix 4) ---

    def test_promote_returns_promotion_selected_not_releasable(self) -> None:
        self._record_full_evidence()
        result = self.manager.promote_plan(self.plan_id)
        self.assertEqual(result["status"], "promotion_selected")
        self.assertNotEqual(result["status"], "releasable")

    def test_promote_includes_deployer_note(self) -> None:
        self._record_full_evidence()
        result = self.manager.promote_plan(self.plan_id)
        self.assertIn("deployer_note", result)
        self.assertIn("Issue #14", result["deployer_note"])

    def test_database_not_set_to_releasable(self) -> None:
        self._record_full_evidence()
        self.manager.promote_plan(self.plan_id)
        plan = self.manager.inspect_plan(self.plan_id)
        self.assertNotEqual(plan.get("status"), "releasable")


class ReleaseGateUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.profile_dir = root / "profiles"
        self.profile_dir.mkdir()
        for profile in ("general", "planner", "coder", "reviewer", "verifier", "scout"):
            (self.profile_dir / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket_name = f"gate-unit-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=self.profile_dir,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket_name,
            max_children_per_parent=4,
            max_managed_sessions=12,
        )
        self.manager = SessionManager(settings)
        self.plan = _setup_plan(self.manager, "unit-gate")
        self.sha = self.plan["revision"]
        self.plan_id = self.plan["plan_id"]

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket_name, "kill-server"], capture_output=True)
        os.environ.pop("AGENT_CONSOLE_EVIDENCE_CAPABILITY", None)
        self.temp.cleanup()

    def _profile_for_evidence(self, etype: str) -> str:
        return {"review": "reviewer", "verification": "verifier", "scout": "scout"}.get(etype, "general")

    def _record_evidence_direct(self, etype: str, result: str,
                                sha: str | None = None,
                                detail: str | None = None) -> dict:
        session = self.manager.create(
            tool="shell", profile=self._profile_for_evidence(etype),
            name=f"direct-{etype}-{int(time.time())}",
            repository=str(self.workspace),
        )
        self.manager.set_attention(session["tmux_name"], state="ready_for_review", actor="test")
        cap = _read_capability(self.manager, session["tmux_name"])
        return self.manager.record_evidence(
            self.plan_id, evidence_type=etype, result=result,
            candidate_sha=sha or self.sha, detail=detail, capability=cap,
        )

    def _record_three_required(self, review_result="pass", scout_result="pass",
                                ver_result="pass") -> None:
        def _one(profile, etype, res):
            if res is None:
                return
            sess = self.manager.create(
                tool="shell", profile=profile,
                name=f"thr-{profile}-{int(time.time())}",
                repository=str(self.workspace),
            )
            self.manager.set_attention(sess["tmux_name"], state="ready_for_review", actor="test")
            cap = _read_capability(self.manager, sess["tmux_name"])
            self.manager.record_evidence(
                self.plan_id, evidence_type=etype, result=res,
                candidate_sha=self.sha, capability=cap,
            )
        _one("reviewer", "review", review_result)
        _one("scout", "scout", scout_result)
        _one("verifier", "verification", ver_result)

    def test_release_gate_missing_evidence_is_blocked(self) -> None:
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "missing_evidence")

    def test_release_gate_partial_evidence_is_blocked(self) -> None:
        self._record_evidence_direct("review", "pass", detail="LGTM")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "missing_scout")

    def test_release_gate_failed_review_is_blocked(self) -> None:
        self._record_three_required(review_result="fail")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "review_fail")

    def test_release_gate_blocked_verification_is_blocked(self) -> None:
        self._record_three_required(ver_result="blocked")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "verification_blocked")

    def test_release_gate_stale_sha_is_blocked(self) -> None:
        wrong_sha = "0" * 40
        self._record_evidence_direct("review", "pass", sha=wrong_sha, detail="LGTM")
        self._record_evidence_direct("scout", "pass", sha=wrong_sha, detail="Scouted")
        self._record_evidence_direct("verification", "pass", sha=wrong_sha,
                                      detail="All tests pass")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "missing_evidence")

    def test_release_gate_missing_scout_is_blocked(self) -> None:
        self._record_evidence_direct("review", "pass", detail="Review OK")
        self._record_evidence_direct("verification", "pass", detail="Verification OK")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "missing_scout")

    def test_release_gate_failed_scout_is_blocked(self) -> None:
        self._record_three_required(scout_result="fail")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "scout_fail")

    def test_release_gate_blocked_scout_is_blocked(self) -> None:
        self._record_three_required(scout_result="blocked")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "scout_blocked")

    def test_release_gate_stale_scout_is_blocked(self) -> None:
        wrong_sha = "f" * 40
        self._record_evidence_direct("scout", "pass", sha=wrong_sha, detail="Stale scout")
        self._record_evidence_direct("review", "pass", detail="Review OK")
        self._record_evidence_direct("verification", "pass", detail="Verification OK")
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["blocked_by"], "missing_scout")

    def test_release_gate_complete_passing_evidence_is_allowed(self) -> None:
        self._record_three_required()
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertTrue(gate["allowed"])
        self.assertIsNone(gate["blocked_by"])
        self.assertIn("passed", gate["reason"])

    def test_promote_with_passing_gate_succeeds(self) -> None:
        self._record_three_required()
        result = self.manager.promote_plan(self.plan_id)
        self.assertEqual(result["status"], "promotion_selected")

    def test_promote_with_blocked_gate_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.promote_plan(self.plan_id)
        self.assertIn("release gate blocked", str(ctx.exception))

    def test_promote_with_failed_evidence_raises_value_error(self) -> None:
        self._record_three_required(review_result="fail")
        with self.assertRaises(ValueError) as ctx:
            self.manager.promote_plan(self.plan_id)
        self.assertIn("release gate blocked", str(ctx.exception))

    def test_record_evidence_invalid_type_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="invalid", result="pass",
                candidate_sha=self.sha, capability="x" * 64,
            )
        self.assertIn("evidence_type", str(ctx.exception))

    def test_record_evidence_invalid_result_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="invalid",
                candidate_sha=self.sha, capability="x" * 64,
            )
        self.assertIn("result", str(ctx.exception))

    def test_record_evidence_missing_sha_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.record_evidence(
                self.plan_id, evidence_type="review", result="pass",
                candidate_sha="", capability="x" * 64,
            )

    def test_inspect_plan_includes_gate_info(self) -> None:
        plan = self.manager.inspect_plan(self.plan_id)
        self.assertIn("release_gate", plan)
        self.assertFalse(plan["release_gate"]["allowed"])

        self._record_three_required()
        after = self.manager.inspect_plan(self.plan_id)
        self.assertIn("release_gate", after)
        self.assertTrue(after["release_gate"]["allowed"])

    def test_gate_block_persists_on_plan(self) -> None:
        self.manager.check_release_gate(self.plan_id)
        with self.manager.database.connect() as conn:
            row = conn.execute(
                "SELECT release_blocked_at, release_blocked_reason FROM plans WHERE id=?",
                (self.plan_id,),
            ).fetchone()
        self.assertIsNotNone(row["release_blocked_at"])
        self.assertIsNotNone(row["release_blocked_reason"])

    def test_gate_clear_after_passing_evidence(self) -> None:
        self.manager.check_release_gate(self.plan_id)
        self._record_three_required()
        gate = self.manager.check_release_gate(self.plan_id)
        self.assertTrue(gate["allowed"])
        with self.manager.database.connect() as conn:
            row = conn.execute(
                "SELECT release_blocked_at, release_blocked_reason FROM plans WHERE id=?",
                (self.plan_id,),
            ).fetchone()
        self.assertIsNone(row["release_blocked_at"])
        self.assertIsNone(row["release_blocked_reason"])

    def test_list_evidence_returns_records(self) -> None:
        self._record_evidence_direct("review", "pass", detail="review notes")
        self._record_evidence_direct("scout", "pass", detail="scout notes")
        self._record_evidence_direct("verification", "pass", detail="verification notes")
        records = self.manager.list_evidence(self.plan_id)
        self.assertEqual(len(records), 3)
        types = {r["evidence_type"] for r in records}
        self.assertEqual(types, {"review", "scout", "verification"})

    def test_evidence_constants_are_stable(self) -> None:
        self.assertEqual(EVIDENCE_TYPES, frozenset({"review", "verification", "scout"}))
        self.assertEqual(EVIDENCE_RESULTS, frozenset({"pass", "fail", "blocked"}))
        self.assertEqual(REQUIRED_EVIDENCE_TYPES, frozenset({"review", "verification", "scout"}))


try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not available")
class ReleaseGateWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        profiles = root / "profiles"
        profiles.mkdir()
        for profile in ("general", "planner", "coder", "reviewer", "verifier", "scout"):
            (profiles / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket_name = f"gate-web-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profiles,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket_name,
            max_children_per_parent=4,
            max_managed_sessions=12,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")
        self.plan = _setup_plan(self.manager, "web-gate")
        self.sha = self.plan["revision"]
        self.plan_id = self.plan["plan_id"]

        os.environ["AGENT_CONSOLE_TAILSCALE_LOGIN"] = "test@example.com"
        from agent_console.web import create_app
        self.client = TestClient(create_app(self.manager), base_url="http://localhost")
        self.headers = {"Tailscale-User-Login": "test@example.com", "Host": "localhost"}

    def tearDown(self) -> None:
        os.environ.pop("AGENT_CONSOLE_TAILSCALE_LOGIN", None)
        os.environ.pop("AGENT_CONSOLE_EVIDENCE_CAPABILITY", None)
        subprocess.run(["tmux", "-L", self.socket_name, "kill-server"], capture_output=True)
        self.temp.cleanup()

    def _make_evidence_session(self, profile: str, name: str) -> str:
        resp = self.client.post("/api/sessions", headers=self.headers, json={
            "tool": "shell", "profile": profile, "name": name,
            "repository": str(self.workspace),
        })
        self.assertEqual(resp.status_code, 200,
                         f"session create failed: {resp.text[:200]}")
        sess_name = resp.json()["tmux_name"]
        self.client.patch(f"/api/sessions/{sess_name}/attention", headers=self.headers,
                          json={"state": "ready_for_review"})
        return sess_name

    def _capability(self, session_name: str) -> str:
        return _read_capability(self.manager, session_name)

    def test_release_gate_blocked_api(self) -> None:
        response = self.client.get(f"/api/plans/{self.plan_id}/gate", headers=self.headers)
        self.assertEqual(response.status_code, 200,
                         f"gate check failed: {response.text[:200]}")
        self.assertFalse(response.json()["allowed"])

    def test_release_gate_allowed_api(self) -> None:
        rev = self._make_evidence_session("reviewer", "web-rev")
        sc = self._make_evidence_session("scout", "web-sc")
        ver = self._make_evidence_session("verifier", "web-ver")
        cap_rev = self._capability(rev)
        cap_sc = self._capability(sc)
        cap_ver = self._capability(ver)
        for name, etype, cap in (("web-rev", "review", cap_rev),
                                  ("web-sc", "scout", cap_sc),
                                  ("web-ver", "verification", cap_ver)):
            resp = self.client.post(f"/api/plans/{self.plan_id}/evidence",
                                     headers=self.headers, json={
                "evidence_type": etype, "result": "pass",
                "candidate_sha": self.sha, "capability": cap,
            })
            self.assertEqual(resp.status_code, 200, f"{name}/{etype}: {resp.text[:200]}")
        response = self.client.get(f"/api/plans/{self.plan_id}/gate", headers=self.headers)
        self.assertEqual(response.status_code, 200,
                         f"gate check failed: {response.text[:200]}")
        self.assertTrue(response.json()["allowed"])

    def test_promote_api_passing_gate(self) -> None:
        rev = self._make_evidence_session("reviewer", "web-prom-rev")
        sc = self._make_evidence_session("scout", "web-prom-sc")
        ver = self._make_evidence_session("verifier", "web-prom-ver")
        for etype, cap in (("review", self._capability(rev)),
                           ("scout", self._capability(sc)),
                           ("verification", self._capability(ver))):
            self.client.post(f"/api/plans/{self.plan_id}/evidence", headers=self.headers,
                              json={"evidence_type": etype, "result": "pass",
                                    "candidate_sha": self.sha, "capability": cap})
        response = self.client.post(f"/api/plans/{self.plan_id}/promote", headers=self.headers,
                                     json={"confirmed": True})
        self.assertEqual(response.status_code, 200,
                         f"promote failed: {response.text[:200]}")
        self.assertEqual(response.json()["status"], "promotion_selected")

    def test_promote_api_blocked_gate(self) -> None:
        response = self.client.post(f"/api/plans/{self.plan_id}/promote", headers=self.headers,
                                     json={"confirmed": True})
        self.assertEqual(response.status_code, 400,
                         f"expected 400, got {response.status_code}: {response.text[:200]}")
        self.assertIn("release gate blocked", response.json()["detail"])

    def test_promote_api_missing_confirmation(self) -> None:
        response = self.client.post(f"/api/plans/{self.plan_id}/promote", headers=self.headers,
                                     json={"confirmed": False})
        self.assertEqual(response.status_code, 400,
                         f"expected 400, got {response.status_code}: {response.text[:200]}")
        self.assertIn("confirmation is required", response.json()["detail"])

    def test_evidence_api_validation(self) -> None:
        response = self.client.post(f"/api/plans/{self.plan_id}/evidence", headers=self.headers,
                                     json={"evidence_type": "invalid", "result": "pass",
                                           "candidate_sha": self.sha})
        self.assertIn(response.status_code, (400, 422),
                      f"expected 400 or 422, got {response.status_code}: {response.text[:200]}")

    def test_promote_api_returns_deployer_note(self) -> None:
        rev = self._make_evidence_session("reviewer", "web-dn-rev")
        sc = self._make_evidence_session("scout", "web-dn-sc")
        ver = self._make_evidence_session("verifier", "web-dn-ver")
        for etype, cap in (("review", self._capability(rev)),
                           ("scout", self._capability(sc)),
                           ("verification", self._capability(ver))):
            self.client.post(f"/api/plans/{self.plan_id}/evidence", headers=self.headers,
                              json={"evidence_type": etype, "result": "pass",
                                    "candidate_sha": self.sha, "capability": cap})
        response = self.client.post(f"/api/plans/{self.plan_id}/promote", headers=self.headers,
                                     json={"confirmed": True})
        self.assertEqual(response.status_code, 200,
                         f"promote failed: {response.text[:200]}")
        data = response.json()
        self.assertIn("deployer_note", data)
        self.assertIn("Issue #14", data["deployer_note"])

    def test_evidence_api_missing_capability_raises(self) -> None:
        response = self.client.post(f"/api/plans/{self.plan_id}/evidence", headers=self.headers,
                                     json={"evidence_type": "review", "result": "pass",
                                           "candidate_sha": self.sha})
        self.assertEqual(response.status_code, 400)
        self.assertIn("evidence capability", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
