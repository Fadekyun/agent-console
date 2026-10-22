"""Issue #206 lifecycle regressions: shutdown admission, deferred results, logs.

These tests are intentionally independent of systemd and Windows: they use the
real worker code with temporary state and patched control commands only.
"""
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import importlib.util
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "agc_worker_lifecycle", Path(__file__).parents[2] / "ops/agc-worker/worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def host(now=1000):
    return {"observed_at": now, "policy_verified": True, "on_ac": True,
            "memory_available_bytes": 24_000_000_000, "worker_memory_bytes": 1_000_000_000,
            "commit_available_bytes": 20_000_000_000, "cpu_percent": 20,
            "disk_free_bytes": 350_000_000_000, "disk_total_bytes": 510_000_000_000,
            "worker_disk_used_bytes": 1_000_000_000}


def binding():
    return worker.validate_binding({
        "job_id": "job-1", "attempt_id": "attempt-1", "lease_generation": 1,
        "kind": "agc.build", "source_sha": "a" * 40,
        "input_sha256": "b" * 64, "manifest_sha256": "c" * 64})


class InstanceTests(unittest.TestCase):
    def make_worker(self, tmp):
        config = Path(tmp) / "config"
        config.mkdir()
        (config / "adapters.json").write_text("{}")
        instance = worker.Worker(Path(tmp) / "state", config, Path(tmp) / "logs")
        instance.host = host(time.time())
        worker.flush_logs()  # startup record is drained before temp cleanup
        return instance

    def active(self, instance, production=False):
        receipt = {**binding(), "state": "running", "unit": "agc-job-test.service",
                   "production_writes": production,
                   "last_heartbeat_monotonic": time.monotonic()}
        instance.active = receipt
        instance.persist(receipt)
        return receipt


class ShutdownAdmissionTests(InstanceTests):
    def test_sigterm_shutdown_rejects_new_start_before_job_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            instance.request_shutdown("sigterm")
            self.assertTrue(instance.shutdown_requested)
            with patch.object(worker, "run") as control:
                with self.assertRaisesRegex(ValueError, "worker_stopping"):
                    instance.start({
                        "protocol_version": 1, "action": "start", "job_id": "job-1",
                        "attempt_id": "attempt-1", "lease_generation": 1,
                        "kind": "agc.build", "source_sha": "a" * 40,
                        "input_sha256": "b" * 64, "manifest_sha256": "c" * 64,
                        "manifest": {}})
            control.assert_not_called()

    def test_stopping_rejects_stage_and_start_rpc_before_any_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            instance.request_shutdown("host_stop")
            with patch.object(worker, "run") as control:
                with self.assertRaisesRegex(ValueError, "worker_stopping"):
                    instance.stage({"artifact_id": "a" * 64, "size": 1, "offset": 0,
                                    "data": "YQ=="})
                with self.assertRaisesRegex(ValueError, "worker_stopping"):
                    instance.dispatch({"protocol_version": 1, "action": "start", **binding(),
                                       "manifest": {}})
            control.assert_not_called()

    def test_shutdown_does_not_wait_lease_grace(self):
        # Lease-loss constants are not shutdown budgets. The supervisor's stop
        # path must be independently bounded and much shorter.
        self.assertEqual(worker.LEASE_STOP_SECONDS, 75)
        self.assertLessEqual(worker.SUPERVISOR_STOP_BUDGET_SECONDS, 10)
        self.assertLess(worker.SUPERVISOR_STOP_BUDGET_SECONDS, worker.LEASE_STOP_SECONDS)


class DeferredResultTests(InstanceTests):
    def test_shutdown_event_closes_admission_without_state_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            acquired = threading.Event()
            release = threading.Event()

            def watchdog_owns_lock():
                with instance.lock:
                    acquired.set()
                    release.wait(2)

            holder = threading.Thread(target=watchdog_owns_lock)
            holder.start()
            try:
                self.assertTrue(acquired.wait(1))
                started = time.monotonic()
                instance.request_shutdown("host_stop")
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(instance.shutdown_event.is_set())
                with self.assertRaisesRegex(ValueError, "worker_stopping"):
                    instance.stage({"artifact_id": "a" * 64, "size": 1, "offset": 0, "data": "YQ=="})
            finally:
                release.set()
                holder.join(2)

    def test_stalled_logger_never_delays_gate_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)

            def stalled_log(*_args, **_kwargs):
                time.sleep(5)

            with patch.object(worker, "log_event", side_effect=stalled_log):
                started = time.monotonic()
                instance.request_shutdown("host_stop")
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(instance.shutdown_event.is_set())

    def test_shutdown_defers_inventory_and_keeps_truthful_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            receipt = self.active(instance)
            output = instance.state / "jobs" / instance.key(binding()) / "output"
            output.mkdir(parents=True)
            (output / "artifact").write_bytes(b"x" * 1024)
            with patch.object(worker, "run"), \
                 patch.object(instance, "unit_empty", return_value=True), \
                 patch.object(instance, "collect_result") as collect, \
                 patch.object(instance, "stop_lease_timer"):
                instance.terminate("host_shutdown", defer_result=True)
            collect.assert_not_called()
            saved = instance.bound_receipt(binding())
            self.assertTrue(saved["termination_confirmed"])
            self.assertTrue(saved["result_deferred"])
            self.assertEqual(saved["reason"], "host_shutdown")
            self.assertEqual(saved["state"], "interrupted")
            self.assertIsNone(instance.active)
            # Deferred collection must not delete the retained output.
            self.assertTrue((output / "artifact").exists())
            worker.flush_logs()

    def test_deferred_result_for_production_write_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            self.active(instance, production=True)
            with patch.object(worker, "run"), \
                 patch.object(instance, "unit_empty", return_value=True), \
                 patch.object(instance, "stop_lease_timer"):
                instance.terminate("supervisor_shutdown", defer_result=True)
            saved = instance.bound_receipt(binding())
            self.assertEqual(saved["state"], "unknown")
            self.assertTrue(saved["result_deferred"])
            self.assertTrue(saved["termination_confirmed"])
            worker.flush_logs()

    def test_collection_deadline_returns_deferred_not_unbounded_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            receipt = {**binding(), "production_writes": False}
            output = instance.state / "jobs" / instance.key(binding()) / "output"
            output.mkdir(parents=True)
            (output / "artifact").write_bytes(b"data")
            with self.assertRaises(worker.ResultDeferred):
                instance.collect_result(receipt, deadline=time.monotonic() - 1)
            self.assertFalse(any((instance.state / "objects").iterdir()))

    def test_cancelled_collection_returns_deferred_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            receipt = {**binding(), "production_writes": False}
            output = instance.state / "jobs" / instance.key(binding()) / "output"
            output.mkdir(parents=True)
            artifact = output / "artifact"
            artifact.write_bytes(b"data")
            instance.shutdown_event.set()
            with self.assertRaises(worker.ResultDeferred):
                instance.collect_result(receipt, cancel=instance.shutdown_event)
            self.assertTrue(artifact.exists())
            self.assertFalse(any((instance.state / "objects").iterdir()))

    def test_ordinary_collection_has_no_global_scan_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            receipt = {**binding(), "production_writes": False}
            output = instance.state / "jobs" / instance.key(binding()) / "output"
            output.mkdir(parents=True)
            for index in range(50):
                (output / ("artifact-%03d" % index)).write_bytes(b"data")
            result = instance.collect_result(receipt)
            self.assertEqual(result["artifact_count"], 50)

    def test_fences_survive_shutdown_and_never_authorize_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            self.active(instance)
            with patch.object(worker, "run"), \
                 patch.object(instance, "unit_empty", return_value=True), \
                 patch.object(instance, "stop_lease_timer"):
                instance.terminate("host_shutdown", defer_result=True)
            restarted = worker.Worker(instance.state, instance.config, Path(tmp) / "logs")
            self.assertEqual(restarted.fences["job-1"], 1)
            status = restarted.bound_receipt(binding())
            self.assertTrue(status["termination_confirmed"])
            self.assertTrue(status["result_deferred"])
            worker.flush_logs()


class InventoryTests(InstanceTests):
    def test_result_inventory_rejects_directory_symlinks_and_sorts_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            receipt = {**binding(), "production_writes": False}
            output = instance.state / "jobs" / instance.key(binding()) / "output"
            output.mkdir(parents=True)
            (output / "b").write_text("b")
            (output / "a").mkdir()
            (output / "a" / "z").write_text("z")
            (output / "a.txt").write_text("a")
            result = instance.collect_result(receipt)
            inventory = worker.read_json(instance.state / "objects" / result["inventory_artifact"]["sha256"])
            self.assertEqual([entry["path"] for entry in inventory["files"]], ["a.txt", "a/z", "b"])
            (output / "link").symlink_to(output / "a", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "result_symlink"):
                instance.collect_result(receipt)


class LogRetentionTests(unittest.TestCase):
    def test_rotation_caps_stream_at_eight_files_and_eight_mebibytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(worker, "LOG_MAX_BYTES", 256), \
                 patch.object(worker, "LOG_BACKUPS", 7), \
                 patch.object(worker, "LOG_TOTAL_BYTES", 8 * 256):
                for index in range(40):
                    worker.log_event(tmp, "test_event", index=index)
                worker.flush_logs()
            files = sorted(Path(tmp).glob("worker.log*"))
            self.assertLessEqual(len(files), 8)
            self.assertLessEqual(sum(path.stat().st_size for path in files), 8 * 256)
            self.assertTrue((Path(tmp) / "worker.log").exists())

    def test_daily_rotation_and_quiet_current_pruning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "worker.log"
            active.write_text("old\n")
            old = time.time() - 2 * 24 * 60 * 60
            os.utime(active, (old, old))
            worker.log_event(root, "new_event")
            worker.flush_logs()
            self.assertTrue((root / "worker.log.1").exists())
            self.assertIn("old", (root / "worker.log.1").read_text())
            self.assertNotIn("old", active.read_text())
            # A quiet active file older than the retention window is removed,
            # not kept alive by a fresh append.
            quiet = root / "worker.log"
            quiet.write_text("stale\n")
            stale = time.time() - worker.LOG_MAX_AGE_SECONDS - 30
            os.utime(quiet, (stale, stale))
            worker.prune_logs(root)
            self.assertFalse(quiet.exists())
            # Fresh mtime must not preserve an eight-day-old first record.
            misleading = root / "worker.log.2"
            old_ts = (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(seconds=worker.LOG_MAX_AGE_SECONDS + 3600)).isoformat()
            misleading.write_text(json.dumps({"ts": old_ts, "event": "old"}) + "\n")
            worker.prune_logs(root)
            self.assertFalse(misleading.exists())

    def test_install_object_existing_target_observes_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = b"already stored"
            digest = hashlib.sha256(value).hexdigest()
            (Path(tmp) / digest).write_bytes(value)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(worker.ResultDeferred):
                worker.install_object(tmp, digest, len(value), io.BytesIO(value),
                                      cancel=cancel)

    def test_retention_only_removes_own_old_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "worker.log.3"
            old.write_text("old")
            stale = time.time() - worker.LOG_MAX_AGE_SECONDS - 60
            os.utime(old, (stale, stale))
            receipt = root / "receipts" / "job.json"
            receipt.parent.mkdir()
            receipt.write_text("{}")
            os.utime(receipt, (stale, stale))
            data = root / "jobs" / "job" / "output" / "artifact"
            data.parent.mkdir(parents=True)
            data.write_text("x")
            os.utime(data, (stale, stale))
            worker.prune_logs(tmp)
            self.assertFalse(old.exists())
            self.assertTrue(receipt.exists())
            self.assertTrue(data.exists())

    def test_logging_is_nonthrowing_when_sink_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-a-directory"
            path.write_text("file")
            worker.log_event(path, "test_event")
            worker.log_event(path / "child", "test_event")
            worker.flush_logs()


if __name__ == "__main__":
    unittest.main()
