"""A stopped session's durable identity cannot be reused below itself."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent_console.config import Settings
from agent_console.manager import SessionManager


class SessionParentCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profiles = self.root/'profiles'
        profiles.mkdir()
        (profiles/'general.md').write_text('# General')
        self.manager = SessionManager(Settings(workspace_root=self.root, state_dir=self.root/'state',
            database_path=self.root/'state/db.sqlite', profile_dir=profiles,
            handoff_dir=self.root/'handoff', worktree_root=self.root/'trees',
            tmux_socket='unused-cycle-'+str(os.getpid())+'-'+str(id(self)), legacy_tmux_socket_path=None))
        with self.manager.database.connect() as db:
            for identity, parent in [('ancestor',None), ('parent','ancestor'), ('leaf','parent')]:
                db.execute("INSERT INTO sessions(id,tmux_name,tool,profile,parent_session_id,repository,status,managed,created_at) VALUES(?,?,'shell','general',?,?,'process-exited',1,'now')",
                           (identity, identity, parent, str(self.root)))
        self.assets = []
        for name in ('ancestor','parent','leaf'):
            for relative in ('tool-overlays/'+name+'/retained', 'skills-isolated/'+name+'/retained',
                             'contexts/'+name+'.md', 'launchers/'+name+'.sh', 'environment-launches/'+name+'.json'):
                path = self.manager.settings.state_dir/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{"preserved":true}' if path.suffix == '.json' else b'preserved fixture bytes')
                path.chmod(0o600)
                self.assets.append(path)

    def snapshot(self):
        with self.manager.database.connect() as db:
            database = list(db.iterdump())
        return database, {str(path):path.read_bytes() for path in self.assets}

    def test_manager_rejects_stopped_self_and_ancestor_reuse_before_mutation(self):
        before = self.snapshot()
        for name, parent in [('parent','parent'), ('ancestor','parent'), ('ancestor','leaf')]:
            with self.subTest(name=name, parent=parent), patch.object(self.manager.tmux,'create') as create:
                with self.assertRaisesRegex(ValueError,'itself or its descendants'):
                    self.manager.create(tool='shell',profile='general',name=name,
                                        parent_session_id=parent,repository=str(self.root))
                create.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_owner_api_cannot_reparent_stopped_identity_into_its_descendants(self):
        # web creates its default app on import; bind that instance to this
        # fixture rather than opening the operator's runtime database.
        with patch('agent_console.manager.SessionManager', return_value=self.manager):
            import agent_console.web as web
        web.SessionManager = SessionManager
        with patch.multiple(web,EXPECTED_LOGIN='fixture@example.com',TRUSTED_HOSTS=['testserver']):
            client = TestClient(web.create_app(self.manager), client=('127.0.0.1',50000))
            self.addCleanup(client.close)
            before = self.snapshot()
            with patch.object(self.manager.tmux,'create') as create:
                for parent in ('ancestor','parent','leaf'):
                    with self.subTest(parent=parent):
                        response = client.post('/api/sessions/'+parent+'/children',
                            json={'tool':'shell','profile':'general','name':'ancestor'},
                            headers={'Tailscale-User-Login':'fixture@example.com'})
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertIn('itself or its descendants', response.json()['detail'])
                        self.assertEqual(self.snapshot(), before)
                create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
