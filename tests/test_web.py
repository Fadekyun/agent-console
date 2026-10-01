from __future__ import annotations

import os
import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["AGENT_CONSOLE_TAILSCALE_LOGIN"] = "test@example.com"
os.environ["AGENT_CONSOLE_TRUSTED_HOSTS"] = "localhost,127.0.0.1,testserver,10.0.0.1"
os.environ["AGENT_CONSOLE_LAN_CIDR"] = "10.0.0.0/8"
os.environ["AGCONSOLE_RETAINED_SKILLS"] = "test-skill-for-web"

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_console.config import Settings
from agent_console.database import utc_now
from agent_console.manager import SessionManager
from agent_console.providers import LaunchSpec
from agent_console.profiles import PROFILE_SCHEMA
from agent_console.validation import PROFILES
from agent_console.web import create_app


@unittest.skipUnless(
    subprocess.run(["sh", "-c", "command -v tmux"], capture_output=True).returncode == 0,
    "tmux required",
)
class WebTests(unittest.TestCase):
    def test_recipe_configuration_and_continuation_require_operator_auth(self):
        request = {'tool':'shell','profile':'general','repository':str(self.workspace),'task':'Inspect the fixture'}
        endpoint = '/api/workbench/launches'
        self.assertEqual(self.client.post(endpoint+'/preview',json={'request':request}).status_code,403)
        self.assertEqual(self.client.post('/api/workbench/recipes',json={'title':'Fixture','request':request}).status_code,403)
        recipe = self.client.post('/api/workbench/recipes',json={'title':'Fixture','request':request},headers=self.headers)
        self.assertEqual(recipe.status_code,200,recipe.text)
        request = recipe.json()['request']
        preview = self.client.post(endpoint+'/preview',json={'request':request},headers=self.headers)
        self.assertEqual(preview.status_code,200,preview.text)
        payload = {'request':request,'request_key':'api-recipe','expected_hash':preview.json()['hash']}
        self.assertEqual(self.client.post(endpoint,json=payload).status_code,403)
        run = self.client.post(endpoint,json=payload,headers=self.headers)
        self.assertEqual(run.status_code,200,run.text);identity=run.json()['session_id']
        self.assertEqual(self.client.post(endpoint,json=payload,headers=self.headers).json()['session_id'],identity)
        path = '/api/workbench/sessions/'+identity+'/configuration'
        self.assertEqual(self.client.get(path).status_code,403)
        config = self.client.get(path,headers=self.headers).json()
        self.assertEqual(config['latest']['config']['tool'],'shell')
        self.assertEqual(config['latest']['request_id'],'api-recipe')
        self.assertIsInstance(config['latest']['sequence'], str)
        self.assertGreater(int(config['latest']['sequence']), 2**53)
        self.manager.kill(run.json()['name'])
        continuation = {'request':config['latest']['config']|{'task':'Continue fixture'},'source_session_id':identity}
        reviewed = self.client.post(endpoint+'/preview',json=continuation,headers=self.headers)
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        continued=self.client.post(endpoint,json=continuation|{'expected_hash':reviewed.json()['hash'],'request_key':'api-continuation'},headers=self.headers)
        self.assertEqual(continued.status_code,200,continued.text)
        self.assertEqual(self.manager.inspect(continued.json()['name'])['parent_session_id'],identity)
        self.assertEqual(self.client.get(endpoint+'/api-continuation').status_code,403)
        self.assertEqual(self.client.post(endpoint+'/preview',json={'request':{'tool':'shell'}},headers=self.headers).status_code,400)

    def test_workbench_ownership_results_history_and_authentication(self):
        from agent_console.workflow_service import WorkflowService
        first=self.manager.create(tool='shell',profile='general',name='workbench-first')
        second=self.manager.create(tool='shell',profile='general',name='workbench-second')
        svc=WorkflowService(self.manager)
        svc.attach(first['id'],second['id'],purpose='Connected check',dependencies=[],expected_version=0,actor='test')
        svc.publish(second['id'],{'kind':'final','outcome':'fail','summary':'One check failed','checks':[],'artifacts':[],'request_key':'result-1'},'test')
        self.assertEqual(self.client.get('/api/workbench').status_code,403)
        response=self.client.get('/api/workbench',headers=self.headers);self.assertEqual(response.status_code,200,response.text)
        view=response.json();node=next(n for n in view['nodes'] if n['id']==second['id'])
        self.assertEqual(node['owner_id'],first['id']);self.assertEqual(node['mechanical'],'running');self.assertEqual(node['attention'],'normal');self.assertEqual(node['result_state'],'failed')
        self.assertEqual(view['groups'][0]['children_total'],1)
        path='/api/workbench/sessions/'+second['id']+'/history'
        self.assertEqual(self.client.get(path).status_code,403)
        history=self.client.get(path,headers=self.headers).json();self.assertTrue(any(e['action']=='result.published' for e in history['events']))
        self.assertNotIn('evidence_capability',json.dumps(view)+json.dumps(history))

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()

        skills_root = root / "skills"
        skills_root.mkdir()
        for skill_name in ("test-skill-for-web",):
            (skills_root / skill_name).mkdir(exist_ok=True)
            (skills_root / skill_name / "SKILL.md").write_text(
                "---\nname: test-skill\ndescription: Web test skill\n---\n",
                encoding="utf-8",
            )
        self._old_skills_root = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(skills_root)

        profiles = root / "profiles"
        profiles.mkdir()
        for profile in PROFILES:
            (profiles / f"{profile}.md").write_text(f"# {profile}\n", encoding="utf-8")
        self.socket = f"agent-console-web-test-{os.getpid()}-{id(self)}"
        settings = Settings(
            workspace_root=self.workspace,
            state_dir=root / "state",
            database_path=root / "state" / "test.sqlite3",
            profile_dir=profiles,
            handoff_dir=root / "handoffs",
            worktree_root=self.workspace / "worktrees",
            tmux_socket=self.socket,
            max_children_per_parent=2,
            max_managed_sessions=4,
        )
        self.manager = SessionManager(settings)
        codex_home = self.manager.auth.codex_home("default")
        (codex_home / "auth.json").write_text("{}\n", encoding="utf-8")
        self.client = TestClient(create_app(self.manager))
        self.headers = {"Tailscale-User-Login": "test@example.com"}

    def tearDown(self) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        if self._old_skills_root is not None:
            os.environ["AGCONSOLE_SKILLS_ROOT"] = self._old_skills_root
        else:
            os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)
        self.temp.cleanup()

    def test_registry_import_requires_identity_and_exact_revision(self) -> None:
        source = self.workspace / "import-me"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: import-me\ndescription: Fixture guide\nmetadata:\n  agent-console/version: '1'\n---\nRead the scope.\n")
        route = "/api/skill-registry/imports"
        self.assertEqual(self.client.post(route, json={"source": str(source)}).status_code, 403)
        staged = self.client.post(route, json={"source": str(source)}, headers=self.headers)
        self.assertEqual(staged.status_code, 200, staged.text)
        data = staged.json()
        target = Path(os.environ["AGCONSOLE_SKILLS_ROOT"]) / "import-me"
        self.assertFalse(target.exists())
        activation = route + "/" + data["id"] + "/activate"
        stale = self.client.post(activation, json={"expected_hash": "a" * 64}, headers=self.headers)
        self.assertEqual(stale.status_code, 400)
        self.assertFalse(target.exists())
        active = self.client.post(activation, json={"expected_hash": data["hash"]}, headers=self.headers)
        self.assertEqual(active.status_code, 200, active.text)
        self.assertEqual(active.json()["trust"], "reviewed")
        self.assertTrue((target / "SKILL.md").is_file())
        outside = self.client.post(route, json={"source": str(self.workspace.parent)}, headers=self.headers)
        self.assertEqual(outside.status_code, 400)

    def test_registry_preview_tracks_hash_approval_and_drift(self) -> None:
        source = Path(os.environ["AGCONSOLE_SKILLS_ROOT"]) / "test-skill-for-web"
        (source / "agent-console.json").write_text(json.dumps({"version": 1, "approval": "ask"}))
        assigned = self.client.post("/api/skills/assign", json={"profile":"general", "skill_name":"test-skill-for-web"}, headers=self.headers)
        self.assertEqual(assigned.status_code, 200, assigned.text)
        payload = {"profile":"general", "tool":"codex", "repository":str(self.workspace)}
        preview = self.client.post("/api/skill-registry/preview", json=payload, headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertFalse(data["validation"]["valid"])
        self.assertEqual(data["policies"][0]["effective_policy"], "ask")
        revision = data["policies"][0]["hash"]
        approved = self.client.post("/api/skill-registry/test-skill-for-web/approve", json={"profile":"general", "expected_hash":revision}, headers=self.headers)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(self.client.post("/api/skill-registry/preview", json=payload, headers=self.headers).json()["validation"]["valid"])
        with (source / "SKILL.md").open("a") as stream:
            stream.write("Changed instructions.\n")
        self.assertFalse(self.client.post("/api/skill-registry/preview", json=payload, headers=self.headers).json()["validation"]["valid"])
        stale = self.client.post("/api/skill-registry/test-skill-for-web/approve", json={"profile":"general", "expected_hash":revision}, headers=self.headers)
        self.assertEqual(stale.status_code, 400)

    def test_session_delivery_receipt_survives_kill(self) -> None:
        session = self.manager.create(tool="shell", profile="general", name="receipt-fixture")
        route = "/api/sessions/receipt-fixture/skills"
        before = self.client.get(route, headers=self.headers)
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["session_id"], session["id"])
        self.assertEqual(before.json()["latest"]["skills"], [])
        self.manager.kill("receipt-fixture")
        self.assertEqual(self.client.get(route, headers=self.headers).json(), before.json())

    def test_versioned_result_and_two_session_handoff(self) -> None:
        source=self.manager.create(tool="shell",profile="general",name="result-source")
        target=self.manager.create(tool="shell",profile="general",name="result-target")
        file=self.workspace / "candidate.txt";file.write_text("Selected revision")
        route=f"/api/sessions/{source['id']}/results"
        payload={"kind":"ready","outcome":"pass","summary":"Candidate ready","checks":["Targeted test passed"],"artifacts":[{"path":"candidate.txt"}],"request_key":"ready-1"}
        self.assertEqual(self.client.post(route,json=payload).status_code,403)
        response=self.client.post(route,json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(self.client.post(route,json=payload,headers=self.headers).json()['id'],result['id'])
        file.write_text("Later edit")
        artifact=self.client.get(f"/api/results/{result['id']}/artifacts/0",headers=self.headers)
        self.assertEqual(artifact.content,b"Selected revision")
        sent=self.client.post(f"/api/results/{result['id']}/send",json={"target_session_id":target['id'],"request_key":"send-1"},headers=self.headers)
        self.assertEqual(sent.status_code,200,sent.text)
        item=sent.json();inbox=f"/api/sessions/{target['id']}/inbox"
        self.assertEqual(self.client.get(inbox,headers=self.headers).json()['items'][0]['state'],'queued')
        for state in ['delivered','consumed']:
            ack=self.client.post(inbox+f"/{item['id']}/ack",json={"state":state},headers=self.headers)
            self.assertEqual(ack.status_code,200,ack.text)
            self.assertEqual(ack.json()['state'],state)
        self.manager.rename('result-target','result-renamed')
        self.assertEqual(self.client.get(inbox,headers=self.headers).json()['items'][0]['state'],'consumed')
        self.manager.kill('result-source');self.manager.kill('result-renamed')
        self.assertEqual(self.client.get(route,headers=self.headers).json()['results'][0]['id'],result['id'])

    def test_existing_session_connections_require_identity_and_preserve_process(self) -> None:
        source=self.manager.create(tool="shell",profile="general",name="graph-source")
        target=self.manager.create(tool="shell",profile="general",name="graph-target")
        route=f"/api/sessions/{source['id']}/connections"
        payload={'session_id':target['id'],'purpose':'Use this exact checkpoint','expected_version':0,
                 'dependencies':[{'source_id':source['id'],'readiness':'after-ready'}]}
        self.assertEqual(self.client.post(route+'/attach',json=payload).status_code,403)
        invalid=self.client.post(route+'/attach',json={**payload,'session_id':'graph-target'},headers=self.headers)
        self.assertEqual(invalid.status_code,400)
        response=self.client.post(route+'/attach',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['version'],1)
        self.assertEqual(self.manager.inspect('graph-target')['id'],target['id'])
        self.assertTrue(self.manager.inspect('graph-target')['running'])
        published=self.client.post(f"/api/sessions/{source['id']}/results",json={'kind':'ready','outcome':'pass','summary':'Ready input','request_key':'graph-publish'},headers=self.headers)
        self.assertEqual(published.status_code,200,published.text)
        graph=self.client.get(route,headers=self.headers).json()
        delivery={'expected_version':1,'expected_signature':graph['readiness'][target['id']]['signature']}
        deliver_route=f"/api/sessions/{target['id']}/connections/deliver"
        first=self.client.post(deliver_route,json=delivery,headers=self.headers)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.client.post(deliver_route,json=delivery,headers=self.headers).json()['id'],first.json()['id'])
        inbox=self.client.get(f"/api/sessions/{target['id']}/inbox",headers=self.headers).json()
        self.assertEqual(len(inbox['items']),1)
        self.assertEqual(inbox['items'][0]['result']['summary'],'Ready input')
        cycle={'expected_version':1,'dependencies':[{'source_id':target['id'],'readiness':'after-final'}]}
        self.assertEqual(self.client.post(route+'/dependencies',json=cycle,headers=self.headers).status_code,400)
        self.assertEqual(self.client.get(route,headers=self.headers).json()['version'],1)

    def test_workflow_suggestion_review_and_policy_require_operator_identity(self) -> None:
        from agent_console.workflow_engine import DEFAULT_POLICY
        source=self.manager.create(tool="shell",profile="general",name="workflow-api-source")
        base=f"/api/sessions/{source['id']}/workflow"
        payload={'task':'Check a bounded candidate','reason':'Independent evidence is useful','expected_output':'One useful result',
                 'config':{'tool':'shell','profile':'general','worktree':False},'dependencies':[],'request_key':'proposal-1'}
        self.assertEqual(self.client.post(base+'/proposals',json=payload).status_code,403)
        proposed=self.client.post(base+'/proposals',json=payload,headers=self.headers)
        self.assertEqual(proposed.status_code,200,proposed.text);step=proposed.json()
        self.assertEqual(step['decision'],'proposed');self.assertEqual(step['attempts'],[])
        self.assertEqual(self.client.post(base+'/proposals',json=payload,headers=self.headers).json()['id'],step['id'])
        preview=self.client.post(f"/api/workflow/steps/{step['id']}/preview",json={},headers=self.headers)
        self.assertEqual(preview.status_code,400);self.assertIn('native workflow',preview.text)
        rejected=self.client.post(f"/api/workflow/steps/{step['id']}/review",json={'decision':'rejected','expected_version':1},headers=self.headers)
        self.assertEqual(rejected.status_code,200,rejected.text)
        self.assertEqual(rejected.json()['decision'],'rejected')
        policy={'policy':DEFAULT_POLICY,'expected_version':0}
        self.assertEqual(self.client.post(base+'/policy',json=policy).status_code,403)
        self.assertEqual(self.client.post(base+'/policy',json=policy,headers=self.headers).status_code,200)
        paused=self.client.post(base+'/control',json={'state':'paused'},headers=self.headers)
        self.assertEqual(paused.json()['policy']['state'],'paused')

    def test_native_reporting_can_suggest_but_cannot_accept_or_expand_envelope(self) -> None:
        import hashlib
        source=self.manager.create(tool="shell",profile="general",name="workflow-agent-source")
        token='fixture-workflow-capability'
        with self.manager.database.connect() as db:db.execute('UPDATE sessions SET evidence_capability_hash=? WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),source['id']))
        headers={'Authorization':'Bearer '+token,'X-Agent-Console-Session':source['id']}
        payload={'task':'Check one thing','reason':'A distinct useful task','expected_output':'A result',
                 'config':{'tool':'codex','profile':'planner'},'dependencies':[{'source_id':source['id'],'readiness':'after-final'}],'request_key':'agent-proposal'}
        response=self.client.post('/api/agent-workflow',json={'command':'propose','payload':payload},headers=headers)
        self.assertEqual(response.status_code,200,response.text);step=response.json()
        self.assertEqual(step['decision'],'proposed');self.assertEqual(step['actor'],'session:'+source['id'])
        self.assertEqual(self.client.post(f"/api/workflow/steps/{step['id']}/review",json={'decision':'accepted','expected_version':1,'preview_hash':'x'},headers=headers).status_code,403)
        self.assertEqual(self.client.post('/api/agent-workflow',json={'command':'configure','payload':{}},headers=headers).status_code,422)

    def test_workflow_agent_capability_cannot_impersonate_peer(self) -> None:
        from agent_console.workflow_service import WorkflowService
        source=self.manager.create(tool="shell",profile="general",name="cap-source")
        target=self.manager.create(tool="shell",profile="general",name="cap-target")
        cap='test-capability'
        with self.manager.database.connect() as db:
            db.execute('UPDATE sessions SET evidence_capability_hash=? WHERE id=?',(hashlib.sha256(cap.encode()).hexdigest(),source['id']))
        service=WorkflowService(self.manager)
        with patch.dict(os.environ,{'AGENT_CONSOLE_SESSION_ID':source['id'],'AGENT_CONSOLE_EVIDENCE_CAPABILITY':cap}):
            self.assertEqual(service.current()['id'],source['id'])
            with self.assertRaises(PermissionError):service.current(target['id'])
        with patch.dict(os.environ,{'AGENT_CONSOLE_SESSION_ID':source['id'],'AGENT_CONSOLE_EVIDENCE_CAPABILITY':'wrong'}):
            with self.assertRaises(PermissionError):service.current()

    def test_agent_reporting_endpoint_is_capability_bound(self) -> None:
        source=self.manager.create(tool="shell",profile="general",name="report-source")
        other=self.manager.create(tool="shell",profile="general",name="report-other")
        cap='reporting-fixture-capability'
        with self.manager.database.connect() as db:
            db.execute('UPDATE sessions SET evidence_capability_hash=? WHERE id=?',(hashlib.sha256(cap.encode()).hexdigest(),source['id']))
        headers={'Authorization':'Bearer '+cap,'X-Agent-Console-Session':source['id']}
        command={'command':'publish','payload':{'kind':'final','outcome':'pass','summary':'Native reporting works','request_key':'native-1'}}
        self.assertEqual(self.client.post('/api/agent-workflow',json=command).status_code,403)
        self.assertEqual(self.client.post('/api/agent-workflow',json=command,headers={**headers,'X-Agent-Console-Session':other['id']}).status_code,403)
        response=self.client.post('/api/agent-workflow',json=command,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['session_id'],source['id'])
        self.assertNotIn(cap,response.text)
        ack=self.client.post('/api/agent-workflow',json={'command':'ack','payload':{'item_id':'wrong','state':'delivered'}},headers=headers)
        self.assertEqual(ack.status_code,400)

    def test_terminal_history_controls_do_not_send_program_input(self) -> None:
        import time
        self.manager.create(tool="shell", profile="general", name="scroll-history")
        with patch.object(self.manager.tmux, "scroll_history") as scroll:
            with self.client.websocket_connect("/ws/sessions/scroll-history", headers=self.headers) as ws:
                ws.send_text(json.dumps({"type": "scroll", "lines": -12}))
                for _ in range(40):
                    if scroll.called:
                        break
                    time.sleep(0.025)
                scroll.assert_called_with("scroll-history", -12)
                ws.send_text(json.dumps({"type": "scroll", "lines": 0}))
                for _ in range(40):
                    if scroll.call_count == 2:
                        break
                    time.sleep(0.025)
                scroll.assert_called_with("scroll-history", 0)
                ws.send_text(json.dumps({"type": "scroll", "lines": -5000}))
                ws.send_text(json.dumps({"type": "detach"}))
                try:
                    while True:
                        ws.receive_bytes()
                except WebSocketDisconnect:
                    pass
                self.assertEqual(scroll.call_count, 2)
        self.assertTrue(self.manager.tmux.exists("scroll-history"))

    def test_tmux_history_returns_to_live_view(self) -> None:
        import time
        self.manager.create(tool="shell", profile="general", name="history-buffer")
        self.manager.tmux.run("send-keys", "-t", "history-buffer", "for i in $(seq 1 200); do echo HISTORY_$i; done", "Enter")
        for _ in range(40):
            if "HISTORY_200" in self.manager.tmux.run("capture-pane", "-p", "-t", "history-buffer").stdout:
                break
            time.sleep(0.025)
        self.manager.tmux.scroll_history("history-buffer", -20)
        mode = self.manager.tmux.run("display-message", "-p", "-t", "history-buffer", "#{pane_in_mode}").stdout.strip()
        self.assertEqual(mode, "1")
        position = self.manager.tmux.run("display-message", "-p", "-t", "history-buffer", "#{scroll_position}").stdout.strip()
        self.assertGreater(int(position), 0)
        self.manager.tmux.scroll_history("history-buffer", 0)
        self.assertEqual(self.manager.tmux.run("display-message", "-p", "-t", "history-buffer", "#{pane_in_mode}").stdout.strip(), "0")
        with self.assertRaises(ValueError):
            self.manager.tmux.scroll_history("history-buffer", -5000)

    def test_workbench_selection_and_current_link_validation(self) -> None:
        with patch.dict(os.environ, {"AGENT_CONSOLE_UI": "workbench", "AGENT_CONSOLE_CURRENT_URL": "https://console.example/"}):
            self.assertIn('workbench.js', self.client.get("/", headers=self.headers).text)
            self.assertEqual(self.client.get("/api/interface", headers=self.headers).json()["current_url"], "https://console.example/")
        with patch.dict(os.environ, {"AGENT_CONSOLE_CURRENT_URL": "javascript:alert(1)"}):
            self.assertEqual(self.client.get("/api/interface", headers=self.headers).json()["current_url"], "")
        self.assertEqual(self.client.get("/work").status_code, 403)
        self.assertIn('app.js', self.client.get("/desktop", headers=self.headers).text)

    def test_human_add_child_keeps_role_and_enforces_capacity(self) -> None:
        parent = self.manager.create(tool="shell", profile="planner", name="human-parent")
        endpoint = "/api/sessions/human-parent/children"
        payload = {"tool": "shell", "profile": "coder", "task": "Explicit implementation task"}
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 403)
        response = self.client.post(endpoint, json={**payload, "name": "human-child"}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        child = response.json()
        self.assertEqual(child["parent_session_id"], parent["id"])
        self.assertEqual(child["profile"], "coder")
        self.assertEqual(self.manager.inspect("human-parent")["profile"], "planner")
        self.assertEqual(self.client.post(endpoint, json={**payload, "name": "human-child-2"}, headers=self.headers).status_code, 200)
        limited = self.client.post(endpoint, json={**payload, "name": "human-child-3"}, headers=self.headers)
        self.assertEqual(limited.status_code, 400)
        self.assertIn("child-session limit", limited.json()["detail"])
        # Automatic delegation still cannot escalate a planner to a coder.
        denied = self.client.post("/api/sessions/human-parent/delegations", json=payload, headers=self.headers)
        self.assertEqual(denied.status_code, 400)
        self.assertIn("not allowed", denied.json()["detail"])

    def test_lifespan_keeps_live_wal_sidecars_for_guarded_inspection(self) -> None:
        from agent_console.inspection import InspectionUnavailable, read_session_snapshot

        database = self.manager.settings.database_path
        # The manager's migrate() closes its last connection, so the guarded
        # read-only reader correctly refuses before the service holds a writer.
        with self.assertRaises(InspectionUnavailable):
            read_session_snapshot(database)
        with patch("agent_console.web.Settings.from_env", return_value=self.manager.settings):
            with TestClient(create_app(self.manager)):
                self.assertTrue(read_session_snapshot(database)["ok"])
        with self.assertRaises(InspectionUnavailable):
            read_session_snapshot(database)

    def test_health_and_identity_gate(self) -> None:
        self.assertEqual(self.client.get("/healthz").text, "ok\n")
        self.assertEqual(self.client.get("/api/sessions").status_code, 403)
        response = self.client.get("/api/sessions", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        me = self.client.get("/api/me", headers=self.headers).json()
        self.assertEqual(me["default_tool"], "codex")
        self.assertEqual(me["default_agent_modes"]["codex"], "auto")
        profiles = me["profiles"]
        self.assertIsInstance(profiles, list)
        self.assertGreater(len(profiles), 0)
        for summary in profiles:
            self.assertIn("read_write_capability", summary)
            self.assertIn("worktree_requirement", summary)
        general = next(p for p in profiles if p["name"] == "general")
        self.assertEqual(general["read_write_capability"], "write")
        self.assertEqual(me["default_agent_modes"]["opencode"], "plan")
        default_opencode = next(
            item for item in me["auth_contexts"]
            if item["tool"] == "opencode" and item["default"]
        )
        self.assertEqual(default_opencode["name"], "opencode-go-default")
        claude = next(item for item in me["tool_status"] if item["name"] == "claude")
        self.assertEqual(claude["status"], "disabled")

    def test_attention_endpoint_updates_state_without_exposing_note_in_audit(self) -> None:
        self.manager.create(
            tool="shell",
            profile="general",
            name="attention-web-test",
            repository=str(self.workspace),
        )
        response = self.client.patch(
            "/api/sessions/attention-web-test/attention",
            headers=self.headers,
            json={"state": "ready_for_review", "note": "Please review the UI"},
        )
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual(session["attention_state"], "ready_for_review")
        self.assertEqual(session["attention_note"], "Please review the UI")
        self.manager.reconcile()
        listed = self.client.get("/api/sessions", headers=self.headers).json()
        listed_session = next(row for row in listed if row["tmux_name"] == "attention-web-test")
        self.assertEqual(listed_session["attention_state"], "ready_for_review")
        invalid = self.client.patch(
            "/api/sessions/attention-web-test/attention",
            headers=self.headers,
            json={"state": "guessed_from_output"},
        )
        self.assertEqual(invalid.status_code, 422)
        with self.manager.database.connect() as conn:
            audit = conn.execute(
                "SELECT details_json FROM audit_events WHERE action='session.attention.updated' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertNotIn("Please review the UI", audit["details_json"])

    def test_model_and_brief_interfaces(self) -> None:
        self.manager.create(
            tool="shell", profile="general", name="brief-web-test",
            task="Do not send this automatically", repository=str(self.workspace),
        )
        brief = self.client.get("/api/sessions/brief-web-test/brief", headers=self.headers)
        self.assertEqual(brief.status_code, 200)
        self.assertEqual(brief.headers["cache-control"], "no-store")
        self.assertTrue(brief.json()["stored_only"])
        model = {
            "id": "free", "model": "openrouter/free", "provider": "openrouter",
            "name": "Free", "status": "active", "selectable": True,
            "cost": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "reasoning": None},
            "limits": {"context": 1000, "output": 100},
            "capabilities": {"reasoning": False, "attachment": False, "toolcall": True},
        }
        with patch.object(self.manager.models, "list", return_value={"provider": "openrouter", "models": [model], "stale": False}):
            listed = self.client.get("/api/models?provider=openrouter", headers=self.headers)
            self.assertEqual(listed.status_code, 200)
            estimated = self.client.post(
                "/api/models/estimate", headers=self.headers,
                json={"provider": "openrouter", "uncached_input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 1000},
            )
            self.assertEqual(estimated.status_code, 200)
            self.assertEqual(estimated.json()["models"][0]["estimated_usd"], 0.0)

    def test_trusted_lan_and_wrong_tailscale_header(self) -> None:
        lan_client = TestClient(
            create_app(self.manager),
            client=("10.0.0.40", 50000),
            base_url="http://10.0.0.1",
        )
        me = lan_client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["access_surface"], "local-lan")
        denied = lan_client.get(
            "/api/me", headers={"Tailscale-User-Login": "wrong@example.com"}
        )
        self.assertEqual(denied.status_code, 403)

    def test_profiles_endpoint_returns_full_schema(self) -> None:
        response = self.client.get("/api/profiles", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        entries = response.json()
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), len(PROFILE_SCHEMA))
        for entry in entries:
            self.assertIn("name", entry)
            self.assertIn("display_name", entry)
            self.assertIn("description", entry)
            self.assertIn("read_write_capability", entry)
            self.assertIn("worktree_requirement", entry)
            self.assertIn("installed", entry)
            self.assertIn("path", entry)
            self.assertIn("allowed_delegation_profiles", entry)
            self.assertIn("allowed_collaboration_profiles", entry)
            self.assertIn("delegation_permissions", entry)
            self.assertIn("requires_human_approval", entry)
            self.assertIn("status", entry)
        coder = next(p for p in entries if p["name"] == "coder")
        self.assertTrue(coder["installed"])
        self.assertEqual(coder["worktree_requirement"], "preferred")
        self.assertEqual(coder["read_write_capability"], "write")

    def test_kill_endpoint_verifies_exit_and_moves_to_history(self) -> None:
        self.manager.create(
            tool="shell",
            profile="general",
            name="kill-web-test",
            repository=str(self.workspace),
        )
        response = self.client.post(
            "/api/sessions/kill-web-test/kill",
            headers=self.headers,
            json={"confirmed": True, "allow_unmanaged": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["running"])
        self.assertFalse(self.manager.tmux.exists("kill-web-test"))
        active = self.client.get("/api/sessions?state=active", headers=self.headers).json()
        history = self.client.get("/api/sessions?state=history", headers=self.headers).json()
        self.assertNotIn("kill-web-test", {row["tmux_name"] for row in active})
        self.assertIn("kill-web-test", {row["tmux_name"] for row in history})

    def test_review_and_delegation_endpoints(self) -> None:
        parent = self.manager.create(
            tool="shell",
            profile="general",
            name="web-parent",
            repository=str(self.workspace),
        )
        self.manager.tmux.run("send-keys", "-t", "web-parent", "-l", "printf WEB_REVIEW_OK")
        self.manager.tmux.run("send-keys", "-t", "web-parent", "Enter")
        response = None
        for _ in range(20):
            response = self.client.get(
                "/api/sessions/web-parent/review?lines=50", headers=self.headers
            )
            if "WEB_REVIEW_OK" in response.json()["content"]:
                break
        assert response is not None
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("WEB_REVIEW_OK", response.json()["content"])
        self.assertTrue(self.manager.tmux.exists("web-parent"))

        delegated = self.client.post(
            "/api/sessions/web-parent/delegations",
            headers=self.headers,
            json={
                "profile": "planner",
                "tool": "shell",
                "auth_context": "default",
                "task": "Read-only child",
            },
        )
        self.assertEqual(delegated.status_code, 200)
        child = delegated.json()["session"]
        self.assertEqual(child["parent_session_id"], parent["id"])
        self.assertEqual(child["auth_context"], "default")
        tree = self.client.get("/api/delegations", headers=self.headers)
        self.assertEqual(tree.status_code, 200)
        root = next(node for node in tree.json()["roots"] if node["tmux_name"] == "web-parent")
        self.assertEqual(root["child_count"], 1)
        self.assertEqual(root["children"][0]["tmux_name"], child["tmux_name"])

        second = self.client.post(
            "/api/sessions/web-parent/delegations",
            headers=self.headers,
            json={"profile": "scout", "tool": "shell", "task": "Second child"},
        )
        self.assertEqual(second.status_code, 200)
        limited = self.client.post(
            "/api/sessions/web-parent/delegations",
            headers=self.headers,
            json={"profile": "reviewer", "tool": "shell", "task": "Third child"},
        )
        self.assertEqual(limited.status_code, 400)
        self.assertIn("child-session limit", limited.json()["detail"])

        rejected = self.client.post(
            "/api/sessions/web-parent/delegations",
            headers=self.headers,
            json={"profile": "planner", "tool": "opencode", "agent_mode": "build", "task": "No"},
        )
        self.assertEqual(rejected.status_code, 400)

    def test_plan_preview_confirmation_and_revision_change(self) -> None:
        repository = self.workspace / "plan-repository"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Agent Console Test"], check=True)
        (repository / "README.md").write_text("first\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "first"], check=True)
        revision = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        artifact = self.manager.settings.handoff_dir / "web-plan"
        artifact.mkdir(parents=True)
        (artifact / "plan.md").write_text("# Web plan\n", encoding="utf-8")
        (artifact / "metadata.json").write_text(
            '{"title":"Web plan","repository":"%s","repository_revision":"%s"}\n'
            % (repository, revision),
            encoding="utf-8",
        )
        preview = self.client.get("/api/plans/web-plan", headers=self.headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["revision_state"], "current")
        missing_confirmation = self.client.post(
            "/api/plans/web-plan/execute",
            headers=self.headers,
            json={"confirmed": False, "profile": "coder"},
        )
        self.assertEqual(missing_confirmation.status_code, 400)

        (repository / "README.md").write_text("second\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "second"], check=True)
        changed = self.client.get("/api/plans/web-plan", headers=self.headers)
        self.assertEqual(changed.json()["revision_state"], "changed")
        blocked = self.client.post(
            "/api/plans/web-plan/execute",
            headers=self.headers,
            json={"confirmed": True, "profile": "coder"},
        )
        self.assertEqual(blocked.status_code, 400)
        with patch.object(
            self.manager,
            "_launch_spec",
            return_value=LaunchSpec(["/usr/bin/zsh", "-l"], {}, []),
        ):
            executed = self.client.post(
                "/api/plans/web-plan/execute",
                headers=self.headers,
                json={
                    "confirmed": True,
                    "profile": "coder",
                    "name": "web-plan-run",
                    "allow_revision_change": True,
                },
            )
        self.assertEqual(executed.status_code, 200)
        session = executed.json()
        self.assertEqual(session["linked_plan_id"], "web-plan")
        self.assertTrue(Path(session["worktree"]).is_dir())
        self.manager.kill("web-plan-run")
        self.assertTrue(Path(session["worktree"]).is_dir())

    def test_unmanaged_kill_requires_explicit_acknowledgement(self) -> None:
        subprocess.run(
            self.manager.tmux.command(
                "new-session",
                "-d",
                "-s",
                "unmanaged-web-test",
                "sleep 60",
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        self.manager.reconcile()
        missing_ack = self.client.post(
            "/api/sessions/unmanaged-web-test/kill",
            headers=self.headers,
            json={"confirmed": True, "allow_unmanaged": True},
        )
        self.assertEqual(missing_ack.status_code, 400)
        self.assertTrue(self.manager.tmux.exists("unmanaged-web-test"))
        killed = self.client.post(
            "/api/sessions/unmanaged-web-test/kill",
            headers=self.headers,
            json={
                "confirmed": True,
                "allow_unmanaged": True,
                "understand_unmanaged": True,
            },
        )
        self.assertEqual(killed.status_code, 200)
        self.assertFalse(killed.json()["running"])
        self.assertFalse(self.manager.tmux.exists("unmanaged-web-test"))

    def test_group_create_and_list_api(self) -> None:
        group = self.client.post("/api/session-groups", headers=self.headers, json={"name": "web-group-test", "purpose": "API test"}).json()
        self.assertEqual(group["name"], "web-group-test")
        self.assertEqual(group["purpose"], "API test")
        groups = self.client.get("/api/session-groups", headers=self.headers).json()
        self.assertGreaterEqual(len(groups), 1)

    def test_group_membership_api(self) -> None:
        group = self.client.post("/api/session-groups", headers=self.headers, json={"name": "web-member-test"}).json()
        self.manager.create(
            tool="shell", profile="general", name="web-member-session",
            repository=str(self.workspace),
        )
        added = self.client.post(
            f"/api/session-groups/{group['id']}/members",
            headers=self.headers,
            json={"session_name": "web-member-session"},
        ).json()
        self.assertEqual(added["member_count"], 1)
        fetched = self.client.get(f"/api/session-groups/{group['id']}", headers=self.headers).json()
        self.assertEqual(fetched["member_count"], 1)
        removed = self.client.delete(
            f"/api/session-groups/{group['id']}/members/web-member-session",
            headers=self.headers,
        ).json()
        self.assertEqual(removed["member_count"], 0)

    def test_group_create_with_unknown_parent_returns_400(self) -> None:
        resp = self.client.post(
            "/api/session-groups",
            headers=self.headers,
            json={"name": "bad-parent-web", "parent_session": "no-such-session"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not found", resp.json()["detail"])

    def test_group_create_audit_shows_web_actor(self) -> None:
        self.client.post(
            "/api/session-groups",
            headers=self.headers,
            json={"name": "audit-web-test"},
        )
        with self.manager.database.connect() as conn:
            audit = conn.execute(
                "SELECT actor, surface FROM audit_events WHERE action='group.created' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(audit["actor"], "test@example.com")
        self.assertEqual(audit["surface"], "web")

    def test_group_add_duplicate_api_returns_400(self) -> None:
        group = self.client.post("/api/session-groups", headers=self.headers, json={"name": "web-dup-test"}).json()
        self.manager.create(
            tool="shell", profile="general", name="web-dup-session",
            repository=str(self.workspace),
        )
        self.client.post(
            f"/api/session-groups/{group['id']}/members",
            headers=self.headers,
            json={"session_name": "web-dup-session"},
        )
        dup = self.client.post(
            f"/api/session-groups/{group['id']}/members",
            headers=self.headers,
            json={"session_name": "web-dup-session"},
        )
        self.assertEqual(dup.status_code, 400)

    def test_group_open_api(self) -> None:
        group = self.client.post("/api/session-groups", headers=self.headers, json={"name": "web-open-test"}).json()
        self.manager.create(
            tool="shell", profile="general", name="web-open-s1",
            repository=str(self.workspace),
        )
        self.manager.create(
            tool="shell", profile="general", name="web-open-s2",
            repository=str(self.workspace),
        )
        self.client.post(
            f"/api/session-groups/{group['id']}/members",
            headers=self.headers,
            json={"session_name": "web-open-s1"},
        )
        self.client.post(
            f"/api/session-groups/{group['id']}/members",
            headers=self.headers,
            json={"session_name": "web-open-s2"},
        )
        result = self.client.post(f"/api/session-groups/{group['id']}/open", headers=self.headers).json()
        self.assertEqual(result["member_count"], 2)
        self.assertGreaterEqual(len(result["available"]), 2)

    def test_websocket_detach_preserves_tmux(self) -> None:
        self.manager.create(
            tool="shell",
            profile="general",
            name="web-test",
            repository=str(self.workspace),
        )
        output = bytearray()
        with self.client.websocket_connect(
            "/ws/sessions/web-test",
            headers=self.headers,
        ) as websocket:
            websocket.send_bytes(b"printf WEBPTY_OK\\n")
            for _ in range(20):
                output.extend(websocket.receive_bytes())
                if b"WEBPTY_OK" in output:
                    break
        self.assertIn(b"WEBPTY_OK", output)
        self.assertTrue(self.manager.tmux.exists("web-test"))

    def test_websocket_detach_control_preserves_tmux(self) -> None:
        self.manager.create(
            tool="shell",
            profile="general",
            name="detach-control-test",
            repository=str(self.workspace),
        )
        with self.client.websocket_connect(
            "/ws/sessions/detach-control-test",
            headers=self.headers,
        ) as websocket:
            websocket.send_text('{"type":"detach"}')
            with self.assertRaises(WebSocketDisconnect) as closed:
                for _ in range(20):
                    websocket.receive_bytes()
            self.assertEqual(closed.exception.code, 4000)
        self.assertTrue(self.manager.tmux.exists("detach-control-test"))

    def test_integration_session_websocket_rejects_terminal_input(self) -> None:
        self.manager.create(
            tool="shell", profile="general", name="view-only-integration",
            repository=str(self.workspace),
        )
        with self.manager.database.connect() as conn:
            conn.execute(
                "UPDATE sessions SET execution_kind='integration-plan' WHERE tmux_name=?",
                ("view-only-integration",),
            )
        with self.client.websocket_connect(
            "/ws/sessions/view-only-integration", headers=self.headers,
        ) as websocket:
            websocket.send_bytes(b"touch SHOULD_NOT_RUN\n")
            with self.assertRaises(WebSocketDisconnect) as closed:
                # The bridge may have already queued initial terminal output.
                # Drain only a bounded number of frames while waiting for the
                # policy close caused by the attempted input.
                for _ in range(20):
                    websocket.receive_bytes()
            self.assertEqual(closed.exception.code, 4403)
        self.assertFalse((self.workspace / "SHOULD_NOT_RUN").exists())

    def test_integration_result_requires_owner_identity_and_has_no_store(self) -> None:
        project = self.manager.create_project("Result project")
        request_id = "12345678901234567"
        session_id = "sess-result-test"
        request_uuid = "ireq-" + "a" * 32
        artifact_dir = self.manager.settings.state_dir / "integration-artifacts" / request_uuid
        artifact_dir.mkdir(parents=True, mode=0o700)
        artifact = {"outcome": "plan", "summary": "safe", "steps": [], "verification": [], "blockers": []}
        raw = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
        (artifact_dir / "plan.json").write_bytes(raw)
        (artifact_dir / "plan.json").chmod(0o600)
        now = utc_now()
        with self.manager.database.connect() as conn:
            conn.execute(
                "INSERT INTO sessions(id,tmux_name,created_at,status,managed,creator_surface,execution_kind) "
                "VALUES(?,?,?,'process-exited',1,'integration:n8n','integration-plan')",
                (session_id, f"n8n-plan-{request_id}", now),
            )
            conn.execute(
                """
                INSERT INTO integration_requests(
                    id,integration,request_key,requester_id,channel_id,canonical_hash,
                    canonical_payload_json,project_alias,project_id,frozen_context,context_hash,
                    frozen_prompt,prompt_hash,artifact_dir,session_id,state,accepted_at,
                    content_expires_at,updated_at,launch_nonce,provider_tool,auth_context,
                    final_artifact_hash,final_artifact_name,reason_code,admission_held
                ) VALUES(?,'n8n',?,?,?,'hash','{}','n100',?,'{}','context-hash','prompt',
                    'prompt-hash',?,?,'completed',?,'2099-01-01T00:00:00+00:00',?,
                    'nonce','codex','default',?,'plan.json','completed',0)
                """,
                (
                    request_uuid, request_id, "191524132624531458", "1493588468884836402",
                    project["id"], str(artifact_dir), session_id, now, now,
                    hashlib.sha256(raw).hexdigest(),
                ),
            )
        response = self.client.get(
            f"/api/integration/plan-requests/{request_id}/result", headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), artifact)
        lan_client = TestClient(
            create_app(self.manager), client=("10.0.0.40", 50000), base_url="http://10.0.0.1",
        )
        denied = lan_client.get(f"/api/integration/plan-requests/{request_id}/result")
        self.assertEqual(denied.status_code, 403)

    def test_kill_closes_websocket_instead_of_switching_sessions(self) -> None:
        self.manager.create(
            tool="shell",
            profile="general",
            name="other-live-session",
            repository=str(self.workspace),
        )
        self.manager.create(
            tool="shell",
            profile="general",
            name="kill-attached-test",
            repository=str(self.workspace),
        )
        with self.client.websocket_connect(
            "/ws/sessions/kill-attached-test",
            headers=self.headers,
        ) as websocket:
            killer = threading.Thread(
                target=self.manager.kill,
                args=("kill-attached-test",),
                daemon=True,
            )
            killer.start()
            with self.assertRaises(WebSocketDisconnect) as closed:
                for _ in range(20):
                    websocket.receive_bytes()
            killer.join(timeout=3)
            self.assertEqual(closed.exception.code, 4001)
        self.assertFalse(self.manager.tmux.exists("kill-attached-test"))
        self.assertTrue(self.manager.tmux.exists("other-live-session"))


    def test_skills_assign_success(self) -> None:
        response = self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "general", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["profile"], "general")
        self.assertEqual(data["skill_name"], "test-skill-for-web")
        self.assertIn("kind", data)

    def test_skills_assign_unknown_profile_returns_400(self) -> None:
        response = self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "nonexistent", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nonexistent", response.json()["detail"])

    def test_skills_assign_unknown_skill_returns_400(self) -> None:
        response = self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "general", "skill_name": "no-such-skill"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no-such-skill", response.json()["detail"])

    def test_skills_assign_duplicate_returns_400(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "coder", "skill_name": "test-skill-for-web"},
        )
        response = self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "coder", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already assigned", response.json()["detail"])

    def test_skills_assign_disallowed_profile_returns_400(self) -> None:
        restricted_root = Path(self.temp.name) / "restricted-skills"
        restricted_root.mkdir(parents=True, exist_ok=True)
        (restricted_root / "restricted-web").mkdir(exist_ok=True)
        (restricted_root / "restricted-web" / "SKILL.md").write_text(
            "---\nkind: superpower\ndescription: Only for coder\nallowed_profiles: coder\n---\n",
            encoding="utf-8",
        )
        old_root = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(restricted_root)
        try:
            response = self.client.post(
                "/api/skills/assign",
                headers=self.headers,
                json={"profile": "general", "skill_name": "restricted-web"},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("general", response.json()["detail"])
            self.assertIn("restricted-web", response.json()["detail"])
            self.assertIn("coder", response.json()["detail"])
        finally:
            if old_root is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_root
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_skills_assignments_list_after_assign(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "planner", "skill_name": "test-skill-for-web"},
        )
        response = self.client.get("/api/skills/assignments", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertTrue(any(
            a["profile"] == "planner" and a["skill_name"] == "test-skill-for-web"
            for a in data
        ))

    def test_skills_unassign_success(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "scout", "skill_name": "test-skill-for-web"},
        )
        response = self.client.post(
            "/api/skills/unassign",
            headers=self.headers,
            json={"profile": "scout", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["profile"], "scout")
        self.assertEqual(data["skill_name"], "test-skill-for-web")

    def test_skills_unassign_not_assigned_returns_400(self) -> None:
        response = self.client.post(
            "/api/skills/unassign",
            headers=self.headers,
            json={"profile": "general", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not assigned", response.json()["detail"])

    def test_skills_catalog_includes_assignments(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "reviewer", "skill_name": "test-skill-for-web"},
        )
        response = self.client.get("/api/skills", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("entries", data)
        skill = next((e for e in data["entries"] if e["name"] == "test-skill-for-web"), None)
        self.assertIsNotNone(skill, "test-skill-for-web should be in catalog entries")
        self.assertIn("assigned_to", skill)
        self.assertTrue(
            any(a["profile"] == "reviewer" for a in skill["assigned_to"]),
            f"expected reviewer assignment in {skill['assigned_to']}",
        )

    def test_skills_assignments_list_no_auth_returns_403(self) -> None:
        response = self.client.get("/api/skills/assignments")
        self.assertEqual(response.status_code, 403)

    def test_skills_assign_no_auth_returns_403(self) -> None:
        response = self.client.post(
            "/api/skills/assign",
            json={"profile": "general", "skill_name": "test-skill-for-web"},
        )
        self.assertEqual(response.status_code, 403)

    def test_skills_effective_returns_assigned(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "coder", "skill_name": "test-skill-for-web"},
        )
        response = self.client.post(
            "/api/skills/effective",
            headers=self.headers,
            json={"profile": "coder"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["profile"], "coder")
        self.assertIn("effective", data)
        self.assertIn("issues", data)
        names = [s["name"] for s in data["effective"]]
        self.assertIn("test-skill-for-web", names)

    def test_skills_validate_valid_returns_valid(self) -> None:
        self.client.post(
            "/api/skills/assign",
            headers=self.headers,
            json={"profile": "planner", "skill_name": "test-skill-for-web"},
        )
        response = self.client.post(
            "/api/skills/validate",
            headers=self.headers,
            json={"profile": "planner"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["valid"])

    def test_skills_validate_invalid_returns_invalid(self) -> None:
        stale_root = Path(self.temp.name) / "stale-validate"
        stale_root.mkdir(parents=True, exist_ok=True)
        skill_name = "stale-skill"
        (stale_root / skill_name).mkdir(exist_ok=True)
        (stale_root / skill_name / "SKILL.md").write_text(
            "---\nkind: standard\ndescription: Initially valid\n---\n",
            encoding="utf-8",
        )
        old_root = os.environ.get("AGCONSOLE_SKILLS_ROOT")
        os.environ["AGCONSOLE_SKILLS_ROOT"] = str(stale_root)
        try:
            self.client.post(
                "/api/skills/assign",
                headers=self.headers,
                json={"profile": "general", "skill_name": skill_name},
            )
            (stale_root / skill_name / "SKILL.md").unlink()
            response = self.client.post(
                "/api/skills/validate",
                headers=self.headers,
                json={"profile": "general"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertFalse(data["valid"])
            self.assertGreater(len(data["issues"]), 0)
            self.assertTrue(
                any("stale source" in i or "missing from catalog" in i for i in data["issues"])
            )
        finally:
            if old_root is not None:
                os.environ["AGCONSOLE_SKILLS_ROOT"] = old_root
            else:
                os.environ.pop("AGCONSOLE_SKILLS_ROOT", None)

    def test_skills_effective_no_auth_returns_403(self) -> None:
        response = self.client.post(
            "/api/skills/effective",
            json={"profile": "general"},
        )
        self.assertEqual(response.status_code, 403)

    def test_skills_validate_no_auth_returns_403(self) -> None:
        response = self.client.post(
            "/api/skills/validate",
            json={"profile": "general"},
        )
        self.assertEqual(response.status_code, 403)

    # --- Project API tests ---

    def test_project_create_and_list_api(self) -> None:
        repo = str(self.workspace / "web-repo")
        (self.workspace / "web-repo").mkdir(exist_ok=True)
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-proj", "repository": repo, "description": "Web test"},
        ).json()
        self.assertEqual(proj["name"], "web-proj")
        self.assertEqual(proj["repository"], repo)
        projects = self.client.get("/api/projects", headers=self.headers).json()
        self.assertGreaterEqual(len(projects), 1)
        found = next(p for p in projects if p["name"] == "web-proj")
        self.assertEqual(found["repository"], repo)

    def test_project_create_with_nonexistent_repo_api(self) -> None:
        nonexistent = str(self.workspace / "web-future-repo")
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-future-proj", "repository": nonexistent},
        ).json()
        self.assertEqual(proj["name"], "web-future-proj")
        self.assertEqual(proj["repository"], nonexistent)

    def test_project_update_repo_to_nonexistent_path_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-upd-nonexist"},
        ).json()
        nonexistent = str(self.workspace / "web-future-upd")
        updated = self.client.put(
            f"/api/projects/{proj['id']}", headers=self.headers,
            json={"repository": nonexistent},
        ).json()
        self.assertEqual(updated["repository"], nonexistent)

    def test_project_get_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-get-proj"},
        ).json()
        got = self.client.get(f"/api/projects/{proj['id']}", headers=self.headers).json()
        self.assertEqual(got["name"], "web-get-proj")
        self.assertIn("sessions", got)

    def test_project_update_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-upd-proj"},
        ).json()
        updated = self.client.put(
            f"/api/projects/{proj['id']}", headers=self.headers,
            json={"status": "paused"},
        ).json()
        self.assertEqual(updated["status"], "paused")

    def test_project_delete_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-del-proj"},
        ).json()
        resp = self.client.delete(
            f"/api/projects/{proj['id']}", headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")
        get_resp = self.client.get(f"/api/projects/{proj['id']}", headers=self.headers)
        self.assertEqual(get_resp.status_code, 400)

    def test_project_bad_id_returns_400(self) -> None:
        resp = self.client.get("/api/projects/proj-bad", headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_project_create_no_auth_returns_403(self) -> None:
        resp = self.client.post("/api/projects", json={"name": "no-auth-proj"})
        self.assertEqual(resp.status_code, 403)

    def test_project_update_no_auth_returns_403(self) -> None:
        resp = self.client.put("/api/projects/proj-x", json={"status": "paused"})
        self.assertEqual(resp.status_code, 403)

    def test_project_delete_no_auth_returns_403(self) -> None:
        resp = self.client.delete("/api/projects/proj-x")
        self.assertEqual(resp.status_code, 403)

    def test_project_assign_no_auth_returns_403(self) -> None:
        resp = self.client.post(
            "/api/projects/proj-x/assign",
            json={"session_name": "sess"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_project_unassign_no_auth_returns_403(self) -> None:
        resp = self.client.post(
            "/api/projects/proj-x/unassign",
            json={"session_name": "sess"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_project_create_audit_shows_web_actor(self) -> None:
        self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "audit-web-proj"},
        )
        with self.manager.database.connect() as conn:
            audit = conn.execute(
                "SELECT actor, surface FROM audit_events WHERE action='project.created' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(audit["actor"], "test@example.com")
        self.assertEqual(audit["surface"], "web")

    def test_project_assign_and_unassign_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-assign-proj", "repository": str(self.workspace)},
        ).json()
        self.manager.create(
            tool="shell", profile="general", name="web-api-assign-sess",
            repository=str(self.workspace),
        )
        assign = self.client.post(
            f"/api/projects/{proj['id']}/assign",
            headers=self.headers,
            json={"session_name": "web-api-assign-sess"},
        ).json()
        sess_names = [s["tmux_name"] for s in assign.get("sessions", [])]
        self.assertIn("web-api-assign-sess", sess_names)
        unassign = self.client.post(
            f"/api/projects/{proj['id']}/unassign",
            headers=self.headers,
            json={"session_name": "web-api-assign-sess"},
        ).json()
        unassign_names = [s["tmux_name"] for s in unassign.get("sessions", [])]
        self.assertNotIn("web-api-assign-sess", unassign_names)

    def test_project_assign_duplicate_returns_400(self) -> None:
        repo = str(self.workspace)
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-dup-assign", "repository": repo},
        ).json()
        self.manager.create(
            tool="shell", profile="general", name="web-dup-assign-sess",
            repository=repo, project_id=proj["id"],
        )
        dup = self.client.post(
            f"/api/projects/{proj['id']}/assign",
            headers=self.headers,
            json={"session_name": "web-dup-assign-sess"},
        )
        self.assertEqual(dup.status_code, 400)

    def test_create_session_rejects_invalid_reasoning_effort(self) -> None:
        resp = self.client.post(
            "/api/sessions",
            headers=self.headers,
            json={"tool": "codex", "profile": "general", "reasoning_effort": "turbo"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_session_with_project_id_api(self) -> None:
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-create-proj", "repository": str(self.workspace)},
        ).json()
        sess = self.client.post(
            "/api/sessions", headers=self.headers,
            json={
                "tool": "shell", "profile": "general",
                "name": "web-create-proj-sess",
                "repository": str(self.workspace),
                "project_id": proj["id"],
            },
        ).json()
        self.assertEqual(sess.get("project_id"), proj["id"])

    def test_project_unassign_wrong_project_id_returns_400(self) -> None:
        repo = str(self.workspace)
        proj_a = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-unassign-wrong-a", "repository": repo},
        ).json()
        proj_b = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-unassign-wrong-b", "repository": repo},
        ).json()
        self.manager.create(
            tool="shell", profile="general", name="web-unassign-wrong-sess",
            repository=repo, project_id=proj_a["id"],
        )
        resp = self.client.post(
            f"/api/projects/{proj_b['id']}/unassign",
            headers=self.headers,
            json={"session_name": "web-unassign-wrong-sess"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_session_with_incompatible_repo_api_returns_400(self) -> None:
        repo_a = str(self.workspace / "repo-a")
        repo_b = str(self.workspace / "repo-b")
        (self.workspace / "repo-a").mkdir(exist_ok=True)
        (self.workspace / "repo-b").mkdir(exist_ok=True)
        proj = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "web-repo-mismatch", "repository": repo_a},
        ).json()
        resp = self.client.post(
            "/api/sessions", headers=self.headers,
            json={
                "tool": "shell", "profile": "general",
                "name": "web-repo-mismatch-sess",
                "repository": repo_b,
                "project_id": proj["id"],
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_presence_routes_do_not_use_manager_or_scoped_writer_as_identity(self):
        from agent_console.device_presence import GET, POST
        token = 'FixtureOnlyPresenceWriter00000000000001'
        with patch.object(self.manager, 'list_sessions', side_effect=AssertionError('manager called')), \
                patch.object(self.manager.database, 'audit', side_effect=AssertionError('read audited')):
            self.assertEqual(self.client.get(GET, headers=self.headers).status_code, 503)
            self.assertEqual(self.client.get(GET, headers={'Tailscale-User-Login': 'wrong'}).status_code, 403)
            self.assertEqual(self.client.get(GET).status_code, 403)
            for path in (GET, '/api/sessions', '/api/projects', '/api/integrations/plan-status'):
                self.assertEqual(self.client.get(path, headers={**self.headers, 'X-AGC-Presence-Writer': token}).status_code, 403)
            self.assertEqual(self.client.post(POST, content='{}', headers={'Content-Type':'application/json','X-AGC-Presence-Writer':token}).status_code,503)


if __name__ == "__main__":
    unittest.main()
