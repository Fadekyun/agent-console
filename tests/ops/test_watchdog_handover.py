import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('handover_guard', ROOT / 'ops/agc-worker/watchdog_handover_guard.py')
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class HandoverGuardTests(unittest.TestCase):
    def test_monitor_refuses_changed_boot_and_missing_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot, hold, worker = root/'boot', root/'hold', root/'worker'
            boot.write_text('same'); hold.touch(); worker.write_bytes(b'fixture')
            digest = GUARD.hashlib.sha256(b'fixture').hexdigest()
            with patch.multiple(GUARD, BOOT=boot, HOLD=hold, WORKER=worker, CGROUP=root,
                                ALLOWED_WORKERS={digest}), patch.object(GUARD, 'status', return_value=None):
                self.assertEqual(GUARD.sample('same')['populated_job_cgroups'], 0)
                with self.assertRaisesRegex(ValueError, 'boot_or_hold'):
                    GUARD.sample('other')
                hold.unlink()
                with self.assertRaisesRegex(ValueError, 'boot_or_hold'):
                    GUARD.sample('same')

    def test_populated_job_is_not_hidden_by_setup_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for unit in ('agc-setup-example.service', 'agc-job-example.service'):
                (root/unit).mkdir(); (root/unit/'cgroup.events').write_text('populated 1\nfrozen 0\n')
            (root/'boot').write_text('same'); (root/'hold').touch(); (root/'worker').write_bytes(b'x')
            with patch.multiple(GUARD, BOOT=root/'boot', HOLD=root/'hold', WORKER=root/'worker', CGROUP=root,
                                ALLOWED_WORKERS={GUARD.hashlib.sha256(b'x').hexdigest()}), patch.object(GUARD, 'status', return_value=None):
                sample = GUARD.sample('same')
            self.assertEqual(sample['populated_job_cgroups'], 1)
            self.assertIsNone(sample['status'])  # restart is never invented success

    def test_unknown_worker_refuses_to_supply_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'boot').write_text('same'); (root/'hold').touch(); (root/'worker').write_bytes(b'unknown')
            with patch.multiple(GUARD, BOOT=root/'boot', HOLD=root/'hold', WORKER=root/'worker'):
                with self.assertRaisesRegex(ValueError, 'unexpected_worker'):
                    GUARD.sample('same')

    def test_monotonic_deadline_survives_backward_clock(self):
        monotonic=[0]
        outputs=[]
        def sleep(seconds): monotonic[0] += seconds
        with patch.object(GUARD, 'sample', return_value={'worker_boot_id':'same'}):
            GUARD.run('same', 110, clock=lambda:100-monotonic[0], monotonic=lambda:monotonic[0],
                      sleep=sleep, emit=lambda value, **kwargs:outputs.append(json.loads(value)))
        self.assertEqual(monotonic[0], 10)
        self.assertEqual(outputs[-1]['state'], 'expired')
        self.assertEqual(len(outputs), 6)

    def test_no_automatic_reconnect_or_mutation_in_guest(self):
        code=(ROOT/'ops/agc-worker/watchdog_handover_guard.py').read_text()
        self.assertNotIn('subprocess', code)
        self.assertNotIn('write_text(', code)
        self.assertNotIn('unlink(', code)
        with self.assertRaisesRegex(ValueError, 'invalid_deadline'):
            GUARD.run('same', 701, clock=lambda:100)

    def test_source_orders_authority_before_exact_handle_termination(self):
        source=(ROOT/'ops/agc-worker/Handover-WorkerWatchdog.ps1').read_text()
        prepare=source[source.index("$state.state='disable_intent'"):source.index("$state.state='awaiting_linux_upgrade'")]
        positions=[prepare.index(token) for token in (
            'Save-Journal; $mutationStarted=$true', 'Disable-ScheduledTask',
            "state='replace_intent'", 'Write-Durable $StartFile',
            "state='terminate_old_intent'", '$oldHandle.TerminateExact()',
            "if(-not $oldHandle.Exited)", '$instances[0].Stop()')]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('GetRunningTasks(1)', source)
        self.assertNotIn('Stop-ScheduledTask', source)
        self.assertNotIn('Stop-Process', source)
        self.assertIn('$guard.guest.status.new_watchdog_sample -eq $true', source)
        self.assertIn('Fresh operator-observed staff baseline required', source)

    def test_retained_clients_are_fenced_and_proven_dead_before_upgrade(self):
        source=(ROOT/'ops/agc-worker/Handover-WorkerWatchdog.ps1').read_text()
        prepare=source[source.index("$state.state='disable_intent'"):source.index("$state.state='awaiting_linux_upgrade'")]
        self.assertLess(prepare.index('Pin-OldClients'), prepare.index('$oldHandle.TerminateExact()'))
        self.assertLess(prepare.index('$oldHandle.TerminateExact()'), prepare.index('Stop-PinnedOldClients'))
        cleanup=source[source.index('function Stop-PinnedOldClients'):source.index('function Read-Guard')]
        self.assertLess(cleanup.index('if(-not $oldHandle.Exited)'), cleanup.index('$client.handle.TerminateExact()'))
        self.assertIn('Sort-Object -Property depth -Descending', cleanup)
        self.assertIn('if($remaining.Count)', cleanup)
        self.assertIn("$state.old_clients_confirmed_dead=$true", cleanup)
        self.assertNotIn('[AgcPinnedProcess]::new', cleanup)
        self.assertIn("$ShimWsl='C:\\Program Files\\WSL\\wsl.exe'".replace('\\\\', '\\'), source)
        self.assertIn('$state.old_clients_confirmed_dead -ne $true', source)

    def test_guard_has_one_attach_and_no_shutdown_cleanup(self):
        source=(ROOT/'ops/agc-worker/Invoke-WatchdogHandoverGuard.ps1').read_text()
        self.assertEqual(source.count('[Diagnostics.Process]::Start('), 1)
        self.assertNotIn('--terminate', source)
        self.assertNotIn('--shutdown', source)
        self.assertNotIn('systemctl', source)
        self.assertNotIn('Ubuntu', source)
        self.assertIn('$process.Dispose()', source)


if __name__ == '__main__':
    unittest.main()
