from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_console.entrypoints import (NAMES, EntrypointError, SelectedRelease,
    bootstrap_entrypoints, entrypoint_lock, install_entrypoints)
from agent_console.deployer import Deployer, RUNTIME_ASSETS
from agent_console.database import Database

REPO = Path(__file__).resolve().parents[1]


def manifest(root):
    files = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    (root/'manifest.json').write_text(json.dumps({'files':files,'file_count':len(files)}))


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'home';self.home.mkdir()
        self.state=self.home/'.local/share/agent-console';self.releases=self.state/'releases';self.releases.mkdir(parents=True)
        self.config=self.home/'config';self.config.mkdir();self.db=self.state/'db.sqlite3'
        self.one=self.make_release('release-one');self.current=self.releases/'current';self.current.symlink_to(self.one.name)

    def make_release(self,name):
        root=self.releases/name;root.mkdir();(root/'scripts').mkdir();(root/'agent_console').mkdir()
        for command in NAMES:
            p=root/'scripts'/command;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
        for relative in ('scripts/install-entrypoints.py','agent_console/entrypoints.py'):
            shutil.copy2(REPO/relative,root/relative)
        (root/'agent_console/__init__.py').write_text('')
        (root/'agent_console/database.py').write_text('SCHEMA_VERSION = 11\n')
        for relative in RUNTIME_ASSETS:
            p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
        manifest(root);return root

    def install(self):return install_entrypoints(self.home,self.state,self.releases)
    def aliases(self):return [b/n for b in (self.home/'bin',self.home/'.local/bin') for n in NAMES]
    def assert_aliases(self):
        for p in self.aliases():
            self.assertEqual(os.readlink(p),str(self.current/'scripts'/p.name))
            self.assertEqual(p.resolve(),self.current.resolve()/'scripts'/p.name)

    def test_exact_eight_aliases_follow_selection_and_preserve_unrelated(self):
        (self.home/'bin').mkdir();unrelated=self.home/'bin/unrelated';unrelated.write_text('manual')
        self.assertEqual(len(self.install()['aliases']),8);self.assert_aliases()
        two=self.make_release('release-two');Deployer(self.releases,object(),state_dir=self.state).select_release(two.name)
        self.assert_aliases();self.assertEqual(unrelated.read_text(),'manual')
        self.install();self.assert_aliases()

    def test_stale_paths_repaired_without_executing_old_writer(self):
        old=self.home/'old-writer';old.write_text('#!/bin/sh\ntouch '+str(self.home/'EXECUTED')+'\n');old.chmod(0o755)
        for p in self.aliases():p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to(old)
        self.install();self.install();self.assert_aliases();self.assertFalse((self.home/'EXECUTED').exists())

    def test_manual_file_refuses_whole_set_before_alias_changes(self):
        last=self.aliases()[-1];last.parent.mkdir(parents=True);last.write_text('user edit')
        with self.assertRaises(EntrypointError):self.install()
        self.assertEqual(last.read_text(),'user edit');self.assertFalse(self.aliases()[0].exists())

    def test_missing_current_and_missing_installer_do_not_fallback(self):
        self.current.unlink()
        with self.assertRaises(OSError):self.install()
        self.assertFalse((self.home/'bin').exists())
        result=subprocess.run([sys.executable,'-I','-B',str(REPO/'ops/lxc115-entrypoint-delegate.py'),str(self.home)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.current.symlink_to(self.one.name);(self.one/'scripts/install-entrypoints.py').unlink();manifest(self.one)
        result=subprocess.run([sys.executable,'-I','-B',str(REPO/'ops/lxc115-entrypoint-delegate.py'),str(self.home)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,2);self.assertFalse((self.home/'bin').exists())

    def test_release_manifest_tamper_and_duplicate_keys_rejected(self):
        (self.one/'scripts/agentctl').write_text('tampered')
        with self.assertRaises(EntrypointError):self.install()
        (self.one/'manifest.json').write_text('{"files":{},"files":{}}')
        with self.assertRaises(EntrypointError):self.install()
        self.assertFalse((self.home/'bin').exists())

    def test_fifo_manifest_refused_without_blocking(self):
        (self.one/'manifest.json').unlink();os.mkfifo(self.one/'manifest.json')
        with self.assertRaises(EntrypointError):self.install()
        self.assertFalse((self.home/'bin').exists())

    def test_release_file_symlink_and_hardlink_rejected(self):
        p=self.one/'scripts/agentctl';data=p.read_bytes();p.unlink();external=self.home/'external';external.write_bytes(data);p.symlink_to(external)
        with self.assertRaises(OSError):self.install()
        p.unlink();os.link(external,p)
        with self.assertRaises(EntrypointError):self.install()

    def test_directory_escape_and_alias_parent_symlinks_refused(self):
        self.current.unlink();self.current.symlink_to(self.home)
        with self.assertRaises(EntrypointError):self.install()
        self.current.unlink();self.current.symlink_to(self.one.name)
        outside=self.home/'outside';outside.mkdir();(self.home/'bin').symlink_to(outside)
        with self.assertRaises(OSError):self.install()
        self.assertEqual(list(outside.iterdir()),[])

    def test_same_bytes_new_inode_detected_before_first_mutation(self):
        real=SelectedRelease.check;count=0
        def changed(selected):
            nonlocal count
            count+=1
            if count==2:
                p=self.one/'scripts/agentctl';q=p.with_suffix('.new');q.write_bytes(p.read_bytes());q.chmod(0o755);q.replace(p)
            return real(selected)
        with patch.object(SelectedRelease,'check',changed):
            with self.assertRaises(EntrypointError):self.install()
        self.assertFalse((self.home/'bin').exists())

    def test_release_selection_waits_for_alias_writer_lock(self):
        two=self.make_release('release-two');done=threading.Event();errors=[]
        def select():
            try:Deployer(self.releases,object(),state_dir=self.state).select_release(two.name)
            except BaseException as e:errors.append(e)
            finally:done.set()
        with entrypoint_lock(self.state):
            worker=threading.Thread(target=select);worker.start();self.assertFalse(done.wait(.08));self.assertEqual(self.current.resolve(),self.one)
        worker.join(2);self.assertTrue(done.is_set());self.assertEqual(errors,[]);self.assertEqual(self.current.resolve(),two)

    def test_concurrent_installers_serialize_and_remain_idempotent(self):
        errors=[]
        def run():
            try:self.install()
            except BaseException as e:errors.append(e)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertEqual(errors,[]);self.assertTrue(all(not t.is_alive() for t in threads));self.assert_aliases()

    def test_lock_symlink_refused(self):
        p=self.state/'entrypoints.lock';p.symlink_to(self.home/'outside')
        with self.assertRaises(OSError):self.install()
        self.assertFalse((self.home/'outside').exists())

    def test_explicit_first_bootstrap_and_upgrade(self):
        self.current.unlink();source=self.one
        result=bootstrap_entrypoints(source,self.home,self.state,self.releases,self.db,self.config)
        self.assertTrue(result['ok']);self.assert_aliases()
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("select value from schema_meta where key='schema_version'").fetchone()[0],'11')
        selected=self.current.resolve()
        bootstrap_entrypoints(source,self.home,self.state,self.releases,self.db,self.config)
        self.assertEqual(self.current.resolve(),selected);self.assert_aliases()

    def test_bootstrap_existing_database_never_selects_fallback(self):
        self.current.unlink();Database(self.db).migrate()
        with self.assertRaisesRegex(EntrypointError,'existing database'):
            bootstrap_entrypoints(self.one,self.home,self.state,self.releases,self.db,self.config)
        self.assertFalse(self.current.exists());self.assertFalse((self.home/'bin').exists())

    def test_guarded_rollback_keeps_aliases_on_current_and_refuses_noninteractive(self):
        Database(self.db).migrate();two=self.make_release('release-old');(two/'agent_console/database.py').write_text('SCHEMA_VERSION = 10\n');manifest(two)
        self.install();deployer=Deployer(self.releases,object(),database_path=self.db,config_dir=self.config,state_dir=self.state)
        deployer.select_release(two.name);self.install();self.assert_aliases()
        Database(self.db).migrate();deployer.select_release(self.one.name)
        with sqlite3.connect(self.db) as c:c.execute("insert into sessions(id,tmux_name,created_at,status,execution_kind) values('private','private','now','detached','integration-plan')")
        with self.assertRaises(ValueError):deployer.select_release(two.name)
        self.assertEqual(self.current.resolve(),self.one);self.assert_aliases()

    def test_authoritative_delegation_executes_only_valid_selected_installer(self):
        for p in self.aliases():p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to('/opt/agent-console/releases/2fb140e/scripts/'+p.name)
        command=[sys.executable,'-I','-B',str(REPO/'ops/lxc115-entrypoint-delegate.py'),str(self.home)]
        for _ in range(2):
            result=subprocess.run(command,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr);self.assert_aliases()
        (self.one/'agent_console/entrypoints.py').write_text("raise RuntimeError('do not execute')")
        result=subprocess.run(command,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2);self.assertNotIn('RuntimeError',result.stderr)

    def test_patch_wrong_source_hash_refuses_without_output(self):
        source=self.home/'wrong.sh';source.write_text('unrelated');output=self.home/'out.sh'
        result=subprocess.run([sys.executable,'-B',str(REPO/'ops/prepare-lxc115-sync-patch.py'),str(source),str(output)],capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists());self.assertEqual(source.read_text(),'unrelated')


class InstallerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'home';self.home.mkdir()
        self.source=self.home/'source'
        shutil.copytree(REPO,self.source,ignore=shutil.ignore_patterns('.git','node_modules','__pycache__','.pytest_cache'))
        for relative in RUNTIME_ASSETS:
            p=self.source/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture asset')
        self.bin=self.home/'test-bin';self.bin.mkdir()
        self.state=self.home/'.local/share/agent-console';self.config=self.home/'.config/agent-console'
        self.stub('python3', '''#!/bin/bash
set -eu
if [ "${1:-}" = "-B" ]; then shift; fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
 mkdir -p "$3/bin"
 printf '#!/bin/sh\nexit 0\n' > "$3/bin/pip"
 chmod +x "$3/bin/pip"
 ln -s /usr/bin/python3 "$3/bin/python"
 exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "agent_console.cli" ]; then
 printf '%s\n' "$PYTHONPATH" > "$HOME/doctor-source"
 printf '{"ok":true}\n'
 exit 0
fi
exec /usr/bin/python3 -B "$@"
''')
        for name in ['npm','codex','tmux']:self.stub(name,'#!/bin/sh\nexit 0\n')
        self.stub('systemctl','#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/systemctl-calls"\n')
        self.env={'HOME':str(self.home),'PATH':str(self.bin)+':/usr/bin:/bin','TERM':'dumb','USER':'fixture','XDG_RUNTIME_DIR':str(self.home/'run')}

    def stub(self,name,body):
        p=self.bin/name;p.write_text(body);p.chmod(0o755)

    def run_install(self):
        return subprocess.run(['bash',str(self.source/'scripts/install.sh')],env=self.env,cwd=self.source,capture_output=True,text=True,timeout=15)

    def assert_aliases(self):
        for base in [self.home/'bin',self.home/'.local/bin']:
            for name in NAMES:self.assertEqual(os.readlink(base/name),str(self.state/'releases/current/scripts'/name))

    def test_real_installer_bootstrap_then_upgrade_uses_selected_source(self):
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assert_aliases();selected=(self.state/'releases/current').resolve()
        self.assertEqual((self.home/'doctor-source').read_text().strip(),str(self.state/'releases/current'))
        for base in [self.home/'bin',self.home/'.local/bin']:
            for name in NAMES:(base/name).unlink();(base/name).symlink_to('/legacy/schema10/'+name)
        # The venv stub must behave idempotently like the real venv creator.
        (self.state/'venv/bin/python').unlink()
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assert_aliases();self.assertEqual((self.state/'releases/current').resolve(),selected)

    def test_custom_state_and_database_are_shared_by_bootstrap_aliases_and_runtime(self):
        self.state=self.home/'custom-state'
        database=self.home/'database-area/custom.sqlite3'
        self.env.update(AGENT_CONSOLE_STATE_DIR=str(self.state),AGENT_CONSOLE_DB=str(database))
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        self.assert_aliases();self.assertTrue(database.is_file())
        self.assertFalse((self.state/'agent-console.sqlite3').exists())
        self.assertIn('AGENT_CONSOLE_DB='+str(database),(self.config/'runtime.env').read_text())
        self.assertIn('runner_state="'+str(self.state)+'"',(self.state/'runner.sh').read_text())

    def test_real_installer_existing_database_without_current_stops_before_service(self):
        Database(self.state/'agent-console.sqlite3').migrate()
        result=self.run_install();self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.home/'systemctl-calls').exists())
        self.assertFalse((self.home/'bin/agentctl').exists())

    def rollback_fixture(self,blocked=False,custom=False):
        if custom:
            self.state=self.home/"rollback-custom-state"
            self.env.update(AGENT_CONSOLE_STATE_DIR=str(self.state),AGENT_CONSOLE_DB=str(self.home/"custom-db/rollback.sqlite3"))
        result=self.run_install();self.assertEqual(result.returncode,0,result.stderr)
        current=self.state/'releases/current';new=current.resolve();old=self.state/'releases/release-old'
        shutil.copytree(new,old)
        p=old/'agent_console/database.py';p.write_text(p.read_text().replace('SCHEMA_VERSION = 11','SCHEMA_VERSION = 10'));manifest(old)
        backup=self.home/'backup';(backup/'releases').mkdir(parents=True);(backup/'releases/current').symlink_to(old.name)
        shutil.copy2(self.config/'runtime.env',backup/'runtime.env');shutil.copy2(self.state/'runner.sh',backup/'runner.sh')
        for name in ['agent-console-web.service','agent-console-tailscale-tunnel.service']:
            shutil.copy2(self.home/'.config/systemd/user'/name,backup/name)
        for directory in ['bin','local-bin']:
            (backup/directory).mkdir()
            for name in NAMES:(backup/directory/name).symlink_to('/legacy/schema10/'+name)
        db=Path(self.env.get('AGENT_CONSOLE_DB',str(self.state/'agent-console.sqlite3')))
        if blocked == 'requests':
            with sqlite3.connect(db) as c:
                required=[row[1] for row in c.execute('PRAGMA table_info(integration_requests)') if row[3] and row[4] is None]
                c.execute('INSERT INTO integration_requests ('+','.join(required)+') VALUES ('+','.join('?' for _ in required)+')',['retained']*len(required))
        elif blocked:
            with sqlite3.connect(db) as c:c.execute("insert into sessions(id,tmux_name,created_at,status,execution_kind) values('private','private','now','detached','integration-plan')")
        before={str(p):p.read_bytes() for p in (self.config/'runtime.env',self.state/'runner.sh')}
        (self.home/'systemctl-calls').unlink(missing_ok=True)
        updater=(self.source/'scripts/update.sh').read_text();function=updater[updater.index('rollback() {'):updater.index('\nexport AGENT_CONSOLE_SOURCE_ROOT')]
        env=dict(self.env,checkout=str(self.source),state=str(self.state),backup=str(backup),runtime=str(self.config/'runtime.env'),config_dir=str(self.config),database_path=str(db),current_target=str(old),current_link=str(current),root=str(self.source),runner=str(self.state/'runner.sh'),unit=str(self.home/'.config/systemd/user/agent-console-web.service'),tunnel_unit=str(self.home/'.config/systemd/user/agent-console-tailscale-tunnel.service'))
        result=subprocess.run(['bash','-euc','wait_for_health() { return 0; };\n'+function+'\nrollback'],env=env,capture_output=True,text=True,timeout=15)
        self.assert_aliases()
        with sqlite3.connect(db) as c:schema=c.execute("select value from schema_meta where key='schema_version'").fetchone()[0]
        if blocked:
            self.assertNotEqual(result.returncode,0);self.assertEqual(current.resolve(),new);self.assertEqual(schema,'11')
            self.assertFalse((self.home/'systemctl-calls').exists())
            self.assertEqual(before,{p:Path(p).read_bytes() for p in before})
            if blocked == 'requests':
                with sqlite3.connect(db) as c:self.assertEqual(c.execute('SELECT count(*) FROM integration_requests').fetchone()[0],1)
        else:
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(current.resolve(),old);self.assertEqual(schema,'10')

    def test_real_updater_guarded_rollback_never_restores_stale_aliases(self):self.rollback_fixture()
    def test_real_updater_refuses_incompatible_rollback_before_links(self):self.rollback_fixture(blocked=True)

    def test_real_updater_retained_request_blocks_before_restore(self):self.rollback_fixture(blocked="requests",custom=True)
    def test_real_updater_custom_database_guarded_selection(self):self.rollback_fixture(custom=True)
