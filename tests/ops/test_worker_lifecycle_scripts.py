"""Source-level lifecycle checks for issue #206.

Native Windows parsing, scheduled tasks and WSL behavior remain a root-owned
acceptance step. These checks only prove the reviewed source keeps the required
gate, bound, identity and managed-mode structure.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "ops/agc-worker"


def source(name):
    return (WORKER / name).read_text(encoding="utf-8")


class WorkerScriptContractTests(unittest.TestCase):
    def test_start_checks_gate_before_every_launch_and_never_unbounded(self):
        script = source("Start-Worker.ps1")
        self.assertLess(script.index("Get-AgcWorkerGate"), script.index("Start-AgcWorkerKeepAlive"))
        self.assertLess(script.index("Get-AgcWorkerGate"), script.index("Invoke-AgcWorkerAdmittedWsl"))
        self.assertNotRegex(script, r"(?m)^\s*&\s*wsl\.exe")
        self.assertNotIn("WaitForExit()", script)
        self.assertNotIn("wsl.exe --shutdown", script)
        self.assertIn("worker_coordinator_stop", script)
        self.assertNotIn("Invoke-AgcWorkerGracefulDrain", script)
        branch = script[script.index("if ($validatedStop)"):script.index("} elseif ($stopRequest")]
        self.assertNotIn("Invoke-AgcWorker", branch)
        self.assertNotIn("-d", branch)
        self.assertNotIn("--terminate", branch)

    def test_stop_task_reads_gate_and_has_no_unbounded_wait(self):
        script = source("Stop-Worker.ps1")
        self.assertIn("Get-AgcWorkerGate", script)
        self.assertIn("Invoke-AgcWorkerScopedTerminate", script)
        self.assertIn("Phase 'terminate'", script)
        self.assertNotRegex(script, r"WaitForExit\(\s*\)")
        self.assertNotRegex(script, r"wsl\.exe\s+-d")
        self.assertNotIn("wsl --shutdown", script)
        self.assertIn("exit 1", script)

    def test_persistent_owner_stop_task_is_fixed_and_never_disables_account(self):
        helper = source("worker-stop.ps1")
        installer = source("Install-LifecycleStopTask.ps1")
        self.assertIn("$FromTask", helper)
        self.assertIn("owner_token_required", helper)
        self.assertIn("agc-worker$", helper)
        self.assertIn("S-1-5-18", helper)
        self.assertIn("requests\\worker\\current.json", helper)
        for phase in ("stop", "terminate", "verify"):
            self.assertIn(phase, helper)
        self.assertIn("AGC-Worker-LifecycleStop", installer)
        self.assertIn("-FromTask", installer)
        self.assertIn("Read-TaskCredential", installer)
        self.assertNotIn("Disable-LocalUser", installer)
        self.assertNotIn("Set-LocalUser", installer)
        self.assertNotIn("New-TaskPassword", installer)

    def test_dispatch_bounds_input_and_gate(self):
        script = source("Dispatch-Worker.ps1")
        self.assertIn("Read-BoundedRequest", script)
        self.assertIn("Get-AgcWorkerGate", script)
        self.assertIn("TimeoutMilliseconds 60000", script)
        self.assertIn("MaxOutputBytes 1048576", script)
        self.assertIn("rpc_output_truncated", script)
        self.assertIn("rpc_reply_too_large", script)
        self.assertNotIn("WaitForExit()", script)

    def test_all_maintenance_wsl_hooks_are_gated(self):
        checks = {
            "Invoke-WorkerSetup.ps1": ["Initialize-AgcWorkerLifecycle", "Assert-AgcWorkerGateOpen", "Invoke-AgcWorkerAdmittedWsl"],
            "Initialize-Distro.ps1": ["Initialize-AgcWorkerLifecycle", "Assert-Gate", "Invoke-AgcWorkerAdmittedWsl"],
            "Invoke-WatchdogHandoverGuard.ps1": ["Initialize-AgcWorkerLifecycle", "Get-AgcWorkerGate", "Start-AgcWorkerAdmittedProcessInfo"],
            "Install-OrganizerCredential.ps1": ["Initialize-AgcWorkerLifecycle", "Assert-AgcWorkerGateOpen", "Invoke-AgcWorkerAdmittedWsl"],
            "Verify-WorkerPolicy.ps1": ["Test-AgcStartAllowed -Owner 'display'", "-RequireAdmission -Owner 'display'"],
            "Setup-Worker.ps1": ["Assert-AgcWorkerGateOpen"],
            "Provision-Worker.ps1": ["Assert-LifecycleAdmission"],
            "Handover-WorkerWatchdog.ps1": ["Assert-AgcWorkerGateOpen"],
        }
        for name, needles in checks.items():
            script = source(name)
            for needle in needles:
                self.assertIn(needle, script, f"{name} missing {needle}")
            self.assertNotRegex(script, r"(?m)^\s*&\s*wsl\.exe")
        self.assertIn("Assert-AgcWorkerGateOpen", source("Invoke-WorkerSetup.ps1"))
        self.assertIn("Get-AgcWorkerGate", source("Invoke-WatchdogHandoverGuard.ps1"))
        self.assertIn("Start-AgcWorkerAdmittedProcessInfo", source("WorkerLifecycle.ps1"))

    def test_stop_task_queues_while_background_stays_single_instance(self):
        installer = source("Install-LifecycleStopTask.ps1")
        provision = source("Provision-Worker.ps1")
        self.assertIn("-MultipleInstances Queue", installer)
        self.assertNotIn("-MultipleInstances IgnoreNew", installer)
        background = [line for line in provision.splitlines() if "-Days 6" in line]
        self.assertEqual(len(background), 1)
        self.assertIn("-MultipleInstances IgnoreNew", background[0])
        # Queue is the only intended change: the existing password-task
        # principal/credential path and limited run level stay intact.
        self.assertIn("Read-TaskCredential", installer)
        self.assertIn("$credential.GetNetworkCredential().Password", installer)
        self.assertIn("-User $credential.UserName", installer)
        self.assertIn("-RunLevel Limited", installer)
        self.assertNotIn("Disable-LocalUser", installer)
        self.assertNotIn("Set-LocalUser", installer)
        self.assertNotIn("New-TaskPassword", installer)

    def test_background_runtime_task_survives_ac_loss_to_drain(self):
        script = source("Provision-Worker.ps1")
        runtime = [line for line in script.splitlines() if "-Days 6" in line]
        self.assertEqual(len(runtime), 1)
        self.assertIn("-AllowStartIfOnBatteries", runtime[0])
        self.assertIn("-DontStopIfGoingOnBatteries", runtime[0])
        start = source("Start-Worker.ps1")
        self.assertIn("OnAC", start)
        self.assertIn("worker_start_suppressed", start)

    def test_guest_install_is_managed_and_bounds_journald(self):
        script = source("install-linux.sh")
        self.assertIn("managed-mode", script)
        self.assertIn("SystemMaxUse=128M", script)
        self.assertIn("MaxRetentionSec=7day", script)
        self.assertIn("systemctl disable agc-worker.service", script)
        self.assertNotIn("systemctl enable agc-worker.service", script)
        self.assertNotIn("systemctl restart agc-worker.service", script)
        self.assertNotIn("systemctl start agc-worker.service", script)
        self.assertIn("Restart=no", script)
        self.assertIn("ForwardToSyslog=no", script)
        self.assertNotIn("*.* stop", script)
        self.assertIn("find /var/log", script)
        self.assertNotIn("WantedBy=multi-user.target", script)

    def test_worker_shutdown_constants_are_not_lease_grace(self):
        worker = (WORKER / "worker.py").read_text(encoding="utf-8")
        self.assertIn("LEASE_STOP_SECONDS = 75", worker)
        self.assertIn("SUPERVISOR_STOP_BUDGET_SECONDS = 11", worker)
        self.assertIn("CHECKPOINT_TIMEOUT_SECONDS = 2", worker)
        self.assertIn("defer_result", worker)
        self.assertIn("shutdown_event", worker)


REAL_LIBRARY = os.environ.get("AGC_LIFECYCLE_REAL_LIBRARY", "")


@unittest.skipUnless(shutil.which("pwsh") and REAL_LIBRARY and Path(REAL_LIBRARY).is_file(),
                     "pwsh/real Lifecycle.ps1 not available")
class RealLibraryTests(unittest.TestCase):
    def test_real_library_cross_integration(self):
        env = {**os.environ, "AGC_LIFECYCLE_REAL_LIBRARY": REAL_LIBRARY,
               "POWERSHELL_TELEMETRY_OPTOUT": "1"}
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File",
             str(WORKER / "tests/RealLifecycle.Tests.ps1")],
            cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("real lifecycle tests: PASS", result.stdout)


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
class PowerShellAdapterTests(unittest.TestCase):
    def test_local_fake_cli_adapter_tests_pass(self):
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File",
             str(WORKER / "tests/WorkerLifecycle.Tests.ps1")],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("worker lifecycle tests: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
