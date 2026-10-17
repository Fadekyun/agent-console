"""Fault and containment tests; no daemon/systemd/remote job execution."""
import base64
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

SPEC = importlib.util.spec_from_file_location("agc_worker", Path(__file__).parents[2] / "ops/agc-worker/worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def host(now=1000):
    return {"observed_at": now, "policy_verified": True, "on_ac": True,
            "memory_available_bytes": 24_000_000_000, "worker_memory_bytes": 1_000_000_000,
            "commit_available_bytes": 20_000_000_000, "cpu_percent": 20,
            "disk_free_bytes": 350_000_000_000, "disk_total_bytes": 510_000_000_000,
            "worker_disk_used_bytes": 1_000_000_000}


def request():
    manifest = {"version": 1, "kind": "agc.build", "source_sha": "a" * 40,
                "input_sha256": "b" * 64, "source_artifact": {"sha256": "c" * 64, "size": 100},
                "parameters": {}, "production_writes": False}
    return {"protocol_version": 1, "action": "start", "job_id": "job-1", "attempt_id": "attempt-1",
            "lease_generation": 1, "kind": "agc.build", "source_sha": "a" * 40,
            "input_sha256": "b" * 64, "manifest_sha256": hashlib.sha256(worker.canonical(manifest)).hexdigest(),
            "manifest": manifest}


class ValidationTests(unittest.TestCase):
    def test_rpc_input_has_total_deadline_and_one_frame(self):
        source, sink = os.pipe()
        try:
            os.write(sink, b'{"ok":true}\n')
            self.assertEqual(worker.read_line(source, 32, .1), b'{"ok":true}\n')
            os.write(sink, b'{')
            with self.assertRaisesRegex(ValueError, 'request_deadline'):
                worker.read_line(source, 32, .02)
            os.write(sink, b'{}\n{}\n')
            with self.assertRaisesRegex(ValueError, 'request_size'):
                worker.read_line(source, 32, .1)
        finally:
            os.close(source); os.close(sink)

    def test_schedule_never_monday_tuesday_and_drains_sunday(self):
        tz = ZoneInfo("Asia/Singapore")
        self.assertFalse(worker.schedule_open(dt.datetime(2026, 10, 19, 12, tzinfo=tz)))
        self.assertFalse(worker.schedule_open(dt.datetime(2026, 10, 20, 12, tzinfo=tz)))
        self.assertTrue(worker.schedule_open(dt.datetime(2026, 10, 21, 12, tzinfo=tz)))
        self.assertTrue(worker.schedule_open(dt.datetime(2026, 10, 18, 23, 44, tzinfo=tz)))
        self.assertFalse(worker.schedule_open(dt.datetime(2026, 10, 18, 23, 45, tzinfo=tz)))

    @patch.object(worker, "schedule_open", return_value=True)
    def test_admission_fails_on_missing_stale_or_unverified_signals(self, _):
        self.assertEqual(worker.admission_holds(host(), now=1001), [])
        self.assertIn("host_telemetry_missing", worker.admission_holds({}, now=1001))
        self.assertIn("host_telemetry_stale", worker.admission_holds(host(), now=1031))
        self.assertEqual(worker.admission_holds(host(), now=995), [])
        self.assertIn("host_telemetry_stale", worker.admission_holds(host(), now=994.9))
        for field, value, reason in [
            ("policy_verified", False, "policy_unverified"), ("on_ac", False, "not_on_ac"),
            ("worker_memory_bytes", 16_000_000_001, "worker_memory_ceiling"),
            ("disk_free_bytes", 70_000_000_000, "staff_disk_reserve"),
            ("worker_disk_used_bytes", 300_000_000_000, "worker_disk_ceiling"),
            ("commit_available_bytes", 1_000_000_000, "host_commit_pressure")]:
            sample = {**host(), field: value}
            self.assertIn(reason, worker.admission_holds(sample, now=1001))
        self.assertIn("staff_memory_reserve", worker.admission_holds(host(), now=1001, required_memory=20_000_000_000))

    def test_binding_cannot_smuggle_shell_or_paths(self):
        for field, value in [("job_id", "../../x"), ("attempt_id", "x; touch x"),
                             ("lease_generation", True), ("source_sha", "master")]:
            with self.assertRaises(ValueError):
                worker.validate_binding({**request(), field: value})

    def test_manifest_hash_and_local_write_authorization(self):
        req = request()
        registry = {"agc.build": {"enabled": True, "parameter_keys": []}}
        worker.validate_manifest(req["manifest"], worker.validate_binding(req), registry)
        req["manifest"]["production_writes"] = True
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            worker.validate_manifest(req["manifest"], worker.validate_binding(req), registry)
        req["manifest_sha256"] = hashlib.sha256(worker.canonical(req["manifest"])).hexdigest()
        with self.assertRaisesRegex(ValueError, "writes_not_authorized"):
            worker.validate_manifest(req["manifest"], worker.validate_binding(req), registry)

    def test_archive_rejects_traversal_links_duplicates_and_bombs(self):
        for name, kind in [("../../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE),
                           ("fifo", tarfile.FIFOTYPE)]:
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "a.tar"
                with tarfile.open(archive, "w") as stream:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    stream.addfile(member)
                with self.assertRaises(ValueError):
                    worker.extract_archive(archive, Path(tmp) / "out")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.tar"
            with tarfile.open(archive, "w") as stream:
                member = tarfile.TarInfo("normal")
                member.size = 3
                stream.addfile(member, io.BytesIO(b"abc"))
            with self.assertRaisesRegex(ValueError, "expansion"):
                worker.extract_archive(archive, Path(tmp) / "out", max_bytes=2)

    def test_toolchain_cannot_select_host_or_relative_root(self):
        for root in ("/", "/etc", "/tmp/toolchain", "../toolchains/debian12"):
            with self.assertRaises(ValueError):
                worker.toolchain_properties({"root_directory": root}, Path("/tmp/job"))

    def test_credential_identity_cannot_be_requested_by_builds(self):
        self.assertEqual(worker.credential_properties("agc.build", {}), {})
        with self.assertRaises(ValueError):
            worker.credential_properties("agc.build", {"credential_ids": ["agc-bushi-organizer-state.json"]})
        with self.assertRaises(ValueError):
            worker.credential_properties("bushi.run", {"credential_ids": ["../../staff-browser"]})


class ReceiptTests(unittest.TestCase):
    def make_worker(self, tmp):
        config = Path(tmp) / "config"
        config.mkdir()
        (config / "adapters.json").write_text("{}")
        instance = worker.Worker(Path(tmp) / "state", config)
        instance.host = host(worker.time.time())
        return instance

    def test_duplicate_start_returns_receipt_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            req = request()
            binding = worker.validate_binding(req)
            receipt = {**binding, "state": "unknown", "termination_confirmed": True}
            instance.persist(receipt)
            with patch.object(worker, "run") as run:
                response = instance.dispatch(req)
            run.assert_not_called()
            self.assertEqual(response["state"], "unknown")
            self.assertEqual(response["binding"], binding)

    def test_old_generation_cannot_reexecute_with_changed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            req = request()
            instance.fences[req["job_id"]] = 2
            with self.assertRaisesRegex(ValueError, "fenced"):
                instance.start(req)

    def test_mismatched_heartbeat_cannot_extend_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            instance.active = {**binding, "state": "running", "last_heartbeat_monotonic": 42}
            result = instance.dispatch({**request(), "action": "heartbeat", "lease_generation": 2})
            self.assertEqual(result["state"], "not_found")
            self.assertEqual(instance.active["last_heartbeat_monotonic"], 42)

    @patch.object(worker, "schedule_open", return_value=True)
    def test_stage_resume_validates_offset_and_final_digest(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            value = b"a small reviewed archive"
            digest = hashlib.sha256(value).hexdigest()
            req = {"artifact_id": digest, "size": len(value), "offset": 0,
                   "data": base64.b64encode(value[:4]).decode()}
            self.assertFalse(instance.stage(req)["complete"])
            self.assertEqual(instance.stage(req)["offset"], 4)
            req.update(offset=4, data=base64.b64encode(value[4:]).decode())
            self.assertTrue(instance.stage(req)["complete"])
            fetched = instance.fetch({"artifact_id": digest, "offset": 0, "length": 262144})
            self.assertEqual(base64.b64decode(fetched["data"]), value)
            self.assertTrue(fetched["eof"])
            req.update(artifact_id="f" * 64, offset=0, data=base64.b64encode(value).decode())
            with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                instance.stage(req)

    def test_lease_loss_keeps_uncertain_writes_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            instance.active = {**binding, "state": "running", "unit": "agc-job-test.service", "production_writes": True}
            with patch.object(worker, "run"), patch.object(instance, "unit_empty", return_value=True):
                instance.terminate("lease_lost")
            receipt = instance.bound_receipt(binding)
            self.assertEqual(receipt["state"], "unknown")
            self.assertTrue(receipt["termination_confirmed"])
            self.assertIsNone(instance.active)

    def test_object_crash_or_corruption_never_becomes_completed_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = b"verified artifact"
            digest = hashlib.sha256(value).hexdigest()
            target = Path(tmp) / digest
            target.write_bytes(b"partial prior copy")
            worker.install_object(tmp, digest, len(value), io.BytesIO(value))
            self.assertEqual(target.read_bytes(), value)
            missing = hashlib.sha256(b"different").hexdigest()
            with self.assertRaises(ValueError):
                worker.install_object(tmp, missing, len(value), io.BytesIO(value))
            self.assertFalse((Path(tmp) / missing).exists())
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_ack_prunes_only_exact_archived_attempt_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            receipt = {**binding, "state": "succeeded", "termination_confirmed": True,
                       "result": {"inventory_artifact": {"sha256": "e" * 64, "size": 42}}}
            instance.persist(receipt)
            job = instance.state / "jobs" / instance.key(binding)
            job.mkdir()
            (job / "scratch").write_text("disposable")
            with self.assertRaises(ValueError):
                instance.acknowledge({**binding, "inventory_sha256": "f" * 64})
            self.assertTrue(job.exists())
            result = instance.acknowledge({**binding, "inventory_sha256": "e" * 64})
            self.assertTrue(result["acknowledged"])
            self.assertFalse(job.exists())
            self.assertTrue(instance.bound_receipt(binding)["centrally_archived"])
            receipt["state"] = "unknown"
            instance.persist(receipt)
            with self.assertRaises(ValueError):
                instance.acknowledge({**binding, "inventory_sha256": "e" * 64})

    def test_external_write_ambiguity_is_not_a_completed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = {**worker.validate_binding(request()), "kind": "bushi.run"}
            receipt = {**binding, "state": "succeeded", "termination_confirmed": True, "production_writes": True}
            output = instance.state / "jobs" / instance.key(binding) / "output"
            output.mkdir(parents=True)
            (output / "response.json").write_text(json.dumps({"ok": False, "errorType": "write_unknown"}))
            result = instance.collect_result(receipt)
            self.assertEqual(receipt["state"], "unknown")
            self.assertEqual(receipt["reason"], "external_write_unknown")
            self.assertTrue((instance.state / "objects" / result["inventory_artifact"]["sha256"]).exists())
            (output / "response.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "invalid_domain_receipt"):
                instance.collect_result(receipt)

    @patch.object(worker, "schedule_open", return_value=True)
    def test_retained_success_receipt_is_durable_before_systemd_unit_release(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            instance.active = {**binding, "state": "running", "unit": "agc-job-test.service",
                               "production_writes": False, "last_heartbeat_monotonic": worker.time.monotonic()}
            info = {"ActiveState": "active", "SubState": "exited", "Result": "success",
                    "ExecMainStatus": "0", "ControlGroup": ""}
            def command(args, **kwargs):
                if args[:2] == ["systemctl", "stop"]:
                    receipt = instance.bound_receipt(binding)
                    self.assertEqual(receipt["state"], "succeeded")
                    self.assertTrue(receipt["unit_cleanup_pending"])
                    self.assertTrue(receipt["result"]["inventory_artifact"]["sha256"])
            with patch.object(worker, "read_json", side_effect=lambda p: host(worker.time.time()) if str(p).endswith("host.json") else json.loads(Path(p).read_text())), patch.object(instance, "unit_info", return_value=info), patch.object(worker, "run", side_effect=command):
                instance.tick()
            receipt = instance.bound_receipt(binding)
            self.assertEqual(receipt["state"], "succeeded")
            self.assertFalse(receipt["unit_cleanup_pending"])
            self.assertIsNone(instance.active)

    def test_running_unit_and_remaining_children_are_not_termination_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            with patch.object(instance, "unit_info", return_value={"ActiveState": "active", "SubState": "running", "ControlGroup": ""}):
                self.assertFalse(instance.unit_empty("agc-job-test.service"))
            with patch.object(instance, "unit_info", return_value={"ActiveState": "active", "SubState": "exited", "ControlGroup": "/test"}), patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", return_value="populated 1\nfrozen 0\n"):
                self.assertFalse(instance.unit_empty("agc-job-test.service"))

    def test_output_objects_match_central_size_limit_and_allow_empty_files(self):
        self.assertEqual(worker.MAX_ARTIFACT, 2 * 1024**3)
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            output = instance.state / "jobs" / instance.key(binding) / "output"
            output.mkdir(parents=True)
            (output / "empty").touch()
            result = instance.collect_result({**binding, "production_writes": False})
            inventory = worker.read_json(instance.state / "objects" / result["inventory_artifact"]["sha256"])
            self.assertEqual(inventory["files"][0]["size"], 0)
            (output / "large").write_bytes(b"12345")
            with patch.object(worker, "MAX_ARTIFACT", 4), self.assertRaisesRegex(ValueError, "result_limit"):
                instance.collect_result({**binding, "production_writes": False})

    def test_fenced_heartbeat_renews_root_timer_only_after_successful_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            instance.active = {**binding, "state": "running", "unit": "agc-job-test.service",
                               "last_heartbeat_monotonic": 90, "lease_timer": "agc-lease-test.timer"}
            (instance.state / "jobs" / instance.key(binding)).mkdir()
            with patch.object(worker.time, "monotonic", return_value=100), patch.object(worker, "run") as command:
                instance.dispatch({**request(), "action": "heartbeat"})
            command.assert_called_once_with(["systemctl", "restart", "agc-lease-test.timer"])
            self.assertEqual(instance.active["last_heartbeat_monotonic"], 100)
            with patch.object(worker.time, "monotonic", return_value=110), patch.object(worker, "run", side_effect=ValueError("control_failed")):
                with self.assertRaises(ValueError): instance.dispatch({**request(), "action": "heartbeat"})
            self.assertEqual(instance.active["last_heartbeat_monotonic"], 100)

    def test_blocked_fetch_does_not_own_watchdog_state_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            entered, release = threading.Event(), threading.Event()
            def fetch(_):
                entered.set(); release.wait(2)
                return {"eof": True}
            with patch.object(instance, "fetch", side_effect=fetch):
                thread = threading.Thread(target=instance.dispatch, args=({"protocol_version": 1, "action": "fetch"},))
                thread.start()
                try:
                    self.assertTrue(entered.wait(1))
                    acquired = instance.lock.acquire(timeout=.2)
                    self.assertTrue(acquired)
                    if acquired: instance.lock.release()
                finally:
                    release.set(); thread.join(1)

    def test_late_heartbeat_cannot_renew_expired_local_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.make_worker(tmp)
            binding = worker.validate_binding(request())
            instance.active = {**binding, "state": "running", "last_heartbeat_monotonic": 0}
            with patch.object(worker.time, "monotonic", return_value=76), patch.object(instance, "terminate") as stop, patch.object(worker, "run") as command:
                instance.dispatch({**request(), "action": "heartbeat"})
            stop.assert_called_once_with("lease_lost")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
