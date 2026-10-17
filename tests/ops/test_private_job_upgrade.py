"""Guarded upgrade packaging/filesystem checks only; no systemd/host changes."""
import base64
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'ops/agc-worker'/file)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


updater = load('private_updater', 'private_job_upgrade.py')
builder = load('private_builder', 'prepare-private-job-upgrade.py')


def payload():
    return dict(version=1,source_revision='a'*40,files={name:dict(before_sha256=hashlib.sha256(b'old').hexdigest()
        if name=='worker.py' else None, sha256=hashlib.sha256(b'new').hexdigest(), mode=mode,
        data=base64.b64encode(b'new').decode()) for name,mode in updater.FILES.items()})


class UpgradeTests(unittest.TestCase):
    def run_failure(self, *, during_install, cleanup_timeout=False):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            paths = {name:root/name.lower() for name in ('ROOT','STATE','CONFIG','BACKUPS')}
            for path in paths.values(): path.mkdir(mode=0o700)
            (paths['STATE']/'jobs').mkdir(mode=0o700)
            (paths['CONFIG']/'hold').touch()
            (paths['ROOT']/'worker.py').write_bytes(b'old')
            for name,path in paths.items(): stack.enter_context(patch.object(updater,name,path))
            stack.enter_context(patch.object(updater,'LOCK',root/'lock'))
            stack.enter_context(patch.dict(os.environ,WSL_DISTRO_NAME='AGC-Worker'))
            stack.enter_context(patch.object(updater.os,'geteuid',return_value=0))
            stack.enter_context(patch.object(updater,'ancestry'))
            stack.enter_context(patch.object(updater,'protected',side_effect=lambda path,**unused:path.lstat()))
            stack.enter_context(patch.object(updater,'read_file',side_effect=lambda path:path.read_bytes()))
            # check_before's default reader was bound at function definition.
            original_check = updater.check_before
            stack.enter_context(patch.object(updater,'check_before',side_effect=lambda path,files:original_check(path,files,lambda file:file.read_bytes())))
            stack.enter_context(patch.object(updater,'preserved_snapshot',return_value={}))
            stack.enter_context(patch.object(updater,'jobs_empty'))
            stack.enter_context(patch.object(updater,'supervisor_stopped',return_value=True))
            status = stack.enter_context(patch.object(updater,'status'))
            status.side_effect = [{} ,{}] if during_install else [{},ValueError('became_active')]
            commands = stack.enter_context(patch.object(updater,'command'))
            events = []
            commands.side_effect = lambda args: events.append(('command',args))
            sync = updater.sync_directory
            def record_sync(path): events.append(('sync',path)); sync(path)
            stack.enter_context(patch.object(updater,'sync_directory',side_effect=record_sync))
            stops = stack.enter_context(patch.object(updater.subprocess,'run'))
            stops.return_value.returncode = 0
            if cleanup_timeout: stops.side_effect = subprocess.TimeoutExpired('systemctl stop',20)
            if during_install:
                atomic = updater.atomic_bytes
                def fail(path,data,mode):
                    if path == paths['ROOT']/'qualification_gate.py': raise OSError('simulated interrupted install')
                    return atomic(path,data,mode)
                stack.enter_context(patch.object(updater,'atomic_bytes',side_effect=fail))
            with self.assertRaises((ValueError,OSError)): updater.main(payload())
            receipt = json.loads(next(paths['BACKUPS'].glob('*/receipt.json')).read_text())
            self.assertTrue((paths['CONFIG']/'hold').exists())
            if during_install:
                first_stop = events.index(('command',['systemctl','stop','agc-worker.service']))
                self.assertLess(events.index(('sync',paths['BACKUPS'].parent)),first_stop)
                self.assertLess(events.index(('sync',paths['BACKUPS'])),first_stop)
                self.assertEqual(receipt['state'],'failed_stop_unconfirmed' if cleanup_timeout else 'failed_held')
                self.assertEqual(receipt['failure_type'],'OSError')
                self.assertEqual((paths['ROOT']/'worker.py').read_bytes(),b'new')
                self.assertEqual(commands.call_args.args[0],['systemctl','stop','agc-worker.service'])
                self.assertEqual(stops.call_args.args[0],['systemctl','stop','agc-worker.service'])
            else:
                self.assertEqual(receipt['state'],'failed_before_install')
                self.assertEqual((paths['ROOT']/'worker.py').read_bytes(),b'old')
                commands.assert_not_called(); stops.assert_not_called()

    def test_second_preflight_failure_does_not_stop_existing_service(self):
        self.run_failure(during_install=False)

    def test_partial_install_preserves_backup_and_hold_without_automatic_rollback(self):
        self.run_failure(during_install=True)

    def test_cleanup_timeout_is_not_reported_as_stopped_and_keeps_original_failure(self):
        self.run_failure(during_install=True,cleanup_timeout=True)

    def test_exact_three_files_and_payload_hashes_required(self):
        updater.validate_payload(payload())
        for change in ('extra','corrupt','mode','old'):
            value = copy.deepcopy(payload())
            if change=='extra': value['files']['adapters.json'] = value['files']['worker.py']
            if change=='corrupt': value['files']['worker.py']['data'] = base64.b64encode(b'wrong').decode()
            if change=='mode': value['files']['worker.py']['mode'] = 0o777
            if change=='old': value['files']['worker.py']['before_sha256'] = None
            with self.assertRaises(ValueError): updater.validate_payload(value)

    def test_old_hash_and_explicit_absence_are_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); worker = root/'worker.py'; worker.write_bytes(b'old')
            reader = lambda path:path.read_bytes()
            updater.check_before(root,payload()['files'],reader)
            worker.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'old_file_changed'): updater.check_before(root,payload()['files'],reader)
            worker.write_bytes(b'old')
            (root/'qualification_gate.py').symlink_to(root/'missing')
            with self.assertRaisesRegex(ValueError,'unexpected_existing'): updater.check_before(root,payload()['files'],reader)

    def test_installed_hash_and_modes_both_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name,mode in updater.FILES.items(): (root/name).write_bytes(b'new'); (root/name).chmod(mode)
            updater.check_after(root,payload()['files'],lambda path:path.read_bytes())
            (root/'worker.py').chmod(0o777)
            with self.assertRaisesRegex(ValueError,'new_file_mismatch'): updater.check_after(root,payload()['files'],lambda path:path.read_bytes())

    def test_config_snapshot_never_reads_credential_bytes_and_tolerates_hold_touch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root/'config'; config.mkdir()
            (config/'credentials').mkdir(); secret = config/'credentials/browser.json'; secret.write_text('fixture-only')
            hold = config/'hold'; hold.touch()
            unit = root/'worker.service'; unit.write_text('fixture unit')
            wsl = root/'wsl.conf'; wsl.write_text('fixture WSL config')
            adapters = root/'adapters'; adapters.mkdir()
            (adapters/'wrapper.py').write_text('fixture adapter')
            (adapters/'installed.json').write_text(json.dumps({'files':{'wrapper.py':'unused metadata pin'}}))
            accessed = []
            def reader(path): accessed.append(path); return path.read_bytes()
            with patch.object(updater,'CONFIG',config),patch.object(updater,'UNITS',[unit]),patch.object(updater,'WSL_CONFIG',wsl),patch.object(updater,'ADAPTERS',adapters),patch.object(updater,'ancestry'),patch.object(updater,'read_file',reader):
                before = updater.preserved_snapshot(); os.utime(hold,ns=(hold.stat().st_atime_ns,hold.stat().st_mtime_ns+1_000_000))
                self.assertEqual(updater.preserved_snapshot(),before)
                self.assertEqual(set(accessed),{unit,wsl,adapters/'installed.json',adapters/'wrapper.py'})
                self.assertNotIn('fixture-only',json.dumps(before))
                secret.chmod(0o644 if secret.stat().st_mode&0o777!=0o644 else 0o600)
                self.assertNotEqual(updater.preserved_snapshot(),before)

    def test_bundle_is_deterministic_exact_and_generated_python_compiles(self):
        revision = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        worker = subprocess.check_output(['git','-C',str(ROOT),'show',revision+':ops/agc-worker/worker.py'])
        expected = hashlib.sha256(worker).hexdigest()
        before = {name:('b'*64 if name=='worker.py' else None) for name in updater.FILES}
        first = builder.make_bundle(ROOT,revision,before,expected)
        self.assertEqual(first,builder.make_bundle(ROOT,revision,before,expected))
        bundle,manifest = first
        for name,data in bundle.items(): self.assertEqual(manifest['files'][name]['sha256'],hashlib.sha256(data).hexdigest())
        shell = bundle['upgrade-linux.sh'].decode()
        embedded = shell.split("<<'AGC_PRIVATE_JOB_UPGRADE'\n",1)[1].rsplit('\nAGC_PRIVATE_JOB_UPGRADE',1)[0]
        compile(embedded,'generated upgrade','exec')
        self.assertLess(len(bundle['upgrade-linux.sh']),1024**2)
        self.assertEqual(set(manifest['installed_files']),set(updater.FILES))
        for name in ('qualify-native.sh','qualify-toolchains.sh'): self.assertIn(expected.encode(),bundle[name])
        with self.assertRaisesRegex(ValueError,'reviewed_worker_hash'): builder.make_bundle(ROOT,revision,before,'c'*64)


if __name__ == '__main__': unittest.main()
