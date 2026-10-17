#!/usr/bin/env python3
"""Root-owned, standard-library-only, single-slot WSL worker (Console #203).

Only the fixed SSH dispatcher calls `rpc`. Job code runs as agcjob in a
bounded systemd cgroup and cannot mutate the daemon, adapters or receipts.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import tarfile
import threading
import time
from zoneinfo import ZoneInfo

MAX_RPC = 512 * 1024
CHUNK = 256 * 1024
MAX_ARTIFACT = 20_000_000_000
BINDING = ("job_id", "attempt_id", "lease_generation", "kind", "source_sha",
           "input_sha256", "manifest_sha256")
TERMINAL = {"succeeded", "failed", "oom", "timeout", "interrupted", "unknown"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fsync_directory(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_object(objects, digest, size, stream):
    """A named object is durable and hash-verified, or does not exist yet."""
    target = Path(objects) / digest
    if target.exists() and target.stat().st_size == size and digest_file(target) == digest:
        return target
    temporary = target.with_suffix(".tmp")
    hasher, written = hashlib.sha256(), 0
    try:
        with temporary.open("wb") as output:
            while chunk := stream.read(1024 * 1024):
                written += len(chunk)
                if written > size:
                    raise ValueError("object_size_mismatch")
                hasher.update(chunk)
                output.write(chunk)
            if written != size or hasher.hexdigest() != digest:
                raise ValueError("object_digest_mismatch")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        fsync_directory(target.parent)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    fsync_directory(path.parent)


def read_json(path):
    return json.loads(Path(path).read_text())


def run(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    if check and result.returncode:
        raise ValueError("control_command_failed:" + Path(args[0]).name)
    return result


def schedule_open(now=None):
    now = now or dt.datetime.now(ZoneInfo("Asia/Singapore"))
    return now.weekday() >= 2 and not (now.weekday() == 6 and (now.hour, now.minute) >= (23, 45))


def validate_binding(request):
    binding = {key: request[key] for key in BINDING}
    for key in ("job_id", "attempt_id", "kind"):
        if not isinstance(binding[key], str) or not ID.fullmatch(binding[key]):
            raise ValueError("invalid_binding:" + key)
    if type(binding["lease_generation"]) is not int or binding["lease_generation"] < 1:
        raise ValueError("invalid_generation")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", binding["source_sha"]):
        raise ValueError("invalid_source_sha")
    for key in ("input_sha256", "manifest_sha256"):
        if not HEX64.fullmatch(binding[key]):
            raise ValueError("invalid_digest")
    return binding


def validate_manifest(manifest, binding, adapters):
    if not isinstance(manifest, dict) or len(canonical(manifest)) > 65536:
        raise ValueError("invalid_manifest")
    if hashlib.sha256(canonical(manifest)).hexdigest() != binding["manifest_sha256"]:
        raise ValueError("manifest_digest_mismatch")
    if manifest.get("version") != 1:
        raise ValueError("unsupported_manifest_version")
    for key in ("kind", "source_sha", "input_sha256"):
        if manifest.get(key) != binding[key]:
            raise ValueError("manifest_binding_mismatch")
    adapter = adapters.get(binding["kind"])
    if not adapter or adapter.get("enabled") is not True:
        raise ValueError("adapter_not_enabled")
    # Registry must independently authorize external writes; request alone never can.
    if type(manifest.get("production_writes")) is not bool:
        raise ValueError("production_writes_required")
    if manifest["production_writes"] and adapter.get("production_writes") is not True:
        raise ValueError("external_writes_not_authorized")
    params = manifest.get("parameters", {})
    if not isinstance(params, dict) or set(params) - set(adapter.get("parameter_keys", [])):
        raise ValueError("unsupported_parameters")
    for key in ("source_artifact", "inputs_artifact"):
        artifact = manifest.get(key)
        if key == "inputs_artifact" and artifact is None:
            continue
        if (not isinstance(artifact, dict) or not HEX64.fullmatch(artifact.get("sha256", ""))
                or type(artifact.get("size")) is not int or not 0 < artifact["size"] <= MAX_ARTIFACT):
            raise ValueError("invalid_artifact_reference")
    return adapter


def toolchain_properties(adapter, job):
    """Only a locally installed root-owned toolchain, never caller root mounts."""
    value = adapter.get("root_directory")
    if value is None:
        return {}
    root = Path(value)
    prefix = Path("/var/lib/agc-worker/toolchains")
    if not root.is_absolute() or root.resolve() != root or root.parent != prefix:
        raise ValueError("invalid_toolchain_path")
    marker = root / ".agc-worker-toolchain.json"
    for path in (prefix, root, marker):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("untrusted_toolchain")
    if read_json(marker).get("version") != 1:
        raise ValueError("unqualified_toolchain")
    adapters = Path("/usr/local/lib/agc-worker-adapters")
    info = adapters.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("untrusted_adapter_directory")
    # Whole job is mounted read-only so atomic lease-file replacements remain
    # visible. Only source workspace/output get writable nested bind mounts.
    return {"RootDirectory": str(root), "MountAPIVFS": "yes", "PrivateDevices": "yes",
            "BindReadOnlyPaths": " ".join((str(job), str(adapters), "/etc/resolv.conf")),
            "BindPaths": str(job / "workspace") + " " + str(job / "output")}


def credential_properties(kind, adapter):
    ids = adapter.get("credential_ids", [])
    if not ids:
        return {}
    if kind != "bushi.run" or ids != ["agc-bushi-organizer-state.json"]:
        raise ValueError("credential_not_allowed_for_adapter")
    parent = Path("/etc/agc-worker/credentials")
    source = parent / ids[0]
    info, directory = source.lstat(), parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
            or not 0 < info.st_size <= 1024**2 or not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != 0 or directory.st_mode & 0o077):
        raise ValueError("credential_source_not_private")
    return {"LoadCredential": ids[0] + ":" + str(source)}


def extract_archive(archive, target, max_bytes=40_000_000_000):
    """No links, special files, path aliases, overwrite or compression bombs."""
    target = Path(target)
    target.mkdir(mode=0o750)
    count = total = 0
    with tarfile.open(archive, "r:*") as source:
        for member in source:
            count += 1
            name = PurePosixPath(member.name)
            if (count > 200_000 or name.is_absolute() or ".." in name.parts
                    or "\\" in member.name or not name.parts or not (member.isdir() or member.isfile())):
                raise ValueError("unsafe_archive_member")
            dest = target.joinpath(*name.parts)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True, mode=0o750)
                continue
            total += member.size
            if total > max_bytes:
                raise ValueError("archive_expansion_limit")
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            with source.extractfile(member) as src, dest.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.chmod(dest, 0o750 if member.mode & 0o111 else 0o640)
    return total


def admission_holds(host, now=None, required_memory=0):
    now = time.time() if now is None else now
    holds = []
    if not schedule_open():
        holds.append("outside_schedule")
    if not isinstance(host, dict) or not isinstance(host.get("observed_at"), (int, float)):
        return holds + ["host_telemetry_missing"]
    age = now - host["observed_at"]
    if not 0 <= age <= 30:
        holds.append("host_telemetry_stale")
    if host.get("policy_verified") is not True:
        holds.append("policy_unverified")
    if host.get("on_ac") is not True:
        holds.append("not_on_ac")
    staff_reserve = max(8 * 1024**3, int(host.get("memory_total_bytes", 0) * 0.25))
    if host.get("memory_available_bytes", 0) < staff_reserve + required_memory:
        holds.append("staff_memory_reserve")
    if host.get("worker_memory_bytes", 16_000_000_001) > 16_000_000_000:
        holds.append("worker_memory_ceiling")
    if host.get("commit_available_bytes", 0) < 4_000_000_000:
        holds.append("host_commit_pressure")
    if host.get("cpu_percent", 100) > 85:
        holds.append("staff_cpu_pressure")
    if host.get("disk_free_bytes", 0) < max(6_000_000_000, host.get("disk_total_bytes", 0) * 0.15):
        holds.append("staff_disk_reserve")
    if host.get("worker_disk_used_bytes", 300_000_000_001) >= 300_000_000_000:
        holds.append("worker_disk_ceiling")
    return holds


class Worker:
    def __init__(self, state=Path("/var/lib/agc-worker"), config=Path("/etc/agc-worker")):
        self.state, self.config = Path(state), Path(config)
        self.lock = threading.RLock()
        self.active = None
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.started = time.monotonic()
        self.healthy_since = None
        self.host = {}
        self.staff_pressure_since = None
        self.adapters = read_json(self.config / "adapters.json")
        self.fences = read_json(self.state / "fences.json") if (self.state / "fences.json").exists() else {}
        for name in ("objects", "incoming", "receipts", "jobs"):
            (self.state / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        # A reboot or daemon crash never licenses replay. Kill any old cgroup;
        # preserve unknown receipts for central reconciliation even if now empty.
        for path in (self.state / "receipts").glob("*.json"):
            receipt = read_json(path)
            self.fences[receipt["job_id"]] = max(self.fences.get(receipt["job_id"], 0), receipt["lease_generation"])
            if receipt["state"] not in TERMINAL or not receipt.get("termination_confirmed", False):
                run(["systemctl", "stop", receipt["unit"]], check=False)
                receipt.update(state="unknown", reason="supervisor_restarted",
                               termination_confirmed=self.unit_empty(receipt["unit"]))
                atomic_json(path, receipt)
                if not receipt["termination_confirmed"]:
                    self.active = receipt
        atomic_json(self.state / "fences.json", self.fences)

    def key(self, binding):
        return hashlib.sha256(canonical(binding)).hexdigest()

    def receipt_path(self, binding):
        return self.state / "receipts" / (self.key(binding) + ".json")

    def bound_receipt(self, binding):
        path = self.receipt_path(binding)
        if not path.exists():
            return {**binding, "state": "not_found", "termination_confirmed": False}
        return read_json(path)

    def persist(self, receipt):
        atomic_json(self.receipt_path({k: receipt[k] for k in BINDING}), receipt)

    def unit_info(self, unit):
        result = run(["systemctl", "show", unit, "--property=ActiveState,SubState,Result,ExecMainStatus,ControlGroup,MemoryPeak"], check=False)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def unit_empty(self, unit):
        info = self.unit_info(unit)
        if info.get("ActiveState") not in {"inactive", "failed"}:
            return False
        group = info.get("ControlGroup")
        if not group:
            return True
        event_file = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
        return not event_file.exists() or "populated 0" in event_file.read_text()

    def status(self):
        holds = admission_holds(self.host)
        if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
            holds.append("cgroup2_required")
        if self.healthy_since is None or time.monotonic() - self.healthy_since < 180:
            holds.append("healthy_warmup")
        if time.monotonic() - self.started < 120:
            holds.append("startup_warmup")
        if (self.config / "hold").exists():
            holds.append("operator_hold")
        stat = os.statvfs(self.state)
        mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        guest_memory = int(mem["MemAvailable"].split()[0]) * 1024
        guest_disk = stat.f_bavail * stat.f_frsize
        h = self.host
        telemetry = {"sampled_at": h.get("observed_at"), "host_cpu_count": h.get("cpu_count"),
                     "host_memory_total_bytes": h.get("memory_total_bytes"),
                     "host_available_bytes": h.get("memory_available_bytes"),
                     "guest_available_bytes": guest_memory, "guest_disk_free_bytes": guest_disk,
                     "host_disk_total_bytes": h.get("disk_total_bytes"), "host_disk_free_bytes": h.get("disk_free_bytes"),
                     "worker_memory_bytes": h.get("worker_memory_bytes"), "worker_disk_used_bytes": h.get("worker_disk_used_bytes"),
                     "host_cpu_percent": h.get("cpu_percent"), "host_commit_percent": 100 * (1 - h.get("commit_available_bytes", 0) / max(1, h.get("commit_total_bytes", 1))),
                     "on_ac": h.get("on_ac"), "policy_verified": h.get("policy_verified")}
        return {"worker_id": "agc-laptop", "boot_id": self.boot_id,
                "capabilities": sorted(k for k, v in self.adapters.items() if v.get("enabled")),
                "gpu_qualified": False, "host": self.host, "telemetry": telemetry,
                "guest": {"memory_available_bytes": guest_memory, "disk_free_bytes": guest_disk,
                          "memory_total_bytes": int(mem["MemTotal"].split()[0]) * 1024,
                          "swap_total_bytes": int(mem["SwapTotal"].split()[0]) * 1024,
                          "cpu_count": os.cpu_count(), "boot_id": self.boot_id},
                "admission": {"ready": not holds and not self.active, "holds": holds},
                "active": self.active}

    def start(self, request):
        preparation_started = time.monotonic()
        binding = validate_binding(request)
        old = self.bound_receipt(binding)
        if old["state"] != "not_found":
            return old  # No implicit re-execution, including an interrupted attempt.
        if self.active:
            raise ValueError("worker_busy")
        fence = self.fences.get(binding["job_id"], 0)
        if binding["lease_generation"] <= fence:
            raise ValueError("generation_fenced")
        manifest = request["manifest"]
        adapter = validate_manifest(manifest, binding, self.adapters)
        memory = int(adapter.get("memory_bytes", 2 * 1024**3))
        cpus = int(adapter.get("cpus", 1))
        timeout = int(adapter.get("timeout_seconds", 3600))
        if not (128 * 1024**2 <= memory <= 8 * 1024**3 and 1 <= cpus <= 12 and 1 <= timeout <= 21600):
            raise ValueError("invalid_adapter_limits")
        status = self.status()
        holds = status["admission"]["holds"] + admission_holds(self.host, required_memory=memory)
        if holds:
            raise ValueError("admission_held:" + ",".join(sorted(set(holds))))
        argv = adapter["argv"]
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv) or not argv[0].startswith("/"):
            raise ValueError("invalid_adapter_command")
        job = self.state / "jobs" / self.key(binding)
        job.mkdir(mode=0o755)
        work = job / "workspace"
        work.mkdir(mode=0o755)
        disk_budget = max(0, int(self.host["disk_free_bytes"] - max(6_000_000_000, self.host["disk_total_bytes"] * 0.15) - 512 * 1024**2))
        disk_budget = min(disk_budget, status["guest"]["disk_free_bytes"] - 1024**3,
                          300_000_000_000 - self.host["worker_disk_used_bytes"])
        for key, dirname in (("source_artifact", "source"), ("inputs_artifact", "inputs")):
            artifact = manifest.get(key)
            if not artifact:
                continue
            obj = self.state / "objects" / artifact["sha256"]
            if not obj.is_file() or obj.stat().st_size != artifact["size"] or digest_file(obj) != artifact["sha256"]:
                raise ValueError("artifact_missing_or_corrupt")
            disk_budget -= extract_archive(obj, work / dirname, max_bytes=min(40_000_000_000, disk_budget))
        output = job / "output"
        output.mkdir(mode=0o750)
        for name in (".home", ".tmp", ".cache"):
            (work / name).mkdir(mode=0o750)
        account = pwd.getpwnam("agcjob")
        for root in (work, output):
            shutil.chown(root, user=account.pw_uid, group=account.pw_gid)
            for path in root.rglob("*"):
                shutil.chown(path, user=account.pw_uid, group=account.pw_gid)
        # Parent folders must be traversable but not writable by job code.
        os.chmod(self.state, 0o711)
        os.chmod(self.state / "jobs", 0o711)
        lease = job / "lease.json"
        atomic_json(lease, {"boot_id": self.boot_id, "lease_generation": binding["lease_generation"],
                            "last_heartbeat_monotonic": time.monotonic()})
        os.chmod(lease, 0o644)
        manifest_path = job / "manifest.json"
        atomic_json(manifest_path, {**manifest, "worker_binding": binding, "worker_lease_file": str(lease)})
        os.chmod(manifest_path, 0o644)
        unit = "agc-job-" + self.key(binding)[:32] + ".service"
        receipt = {**binding, "state": "starting", "unit": unit, "boot_id": self.boot_id,
                   "started_at": time.time(), "last_heartbeat_monotonic": time.monotonic(),
                   "termination_confirmed": False, "production_writes": manifest["production_writes"]}
        self.persist(receipt)  # Persist intent BEFORE the executable can run.
        self.fences[binding["job_id"]] = binding["lease_generation"]
        atomic_json(self.state / "fences.json", self.fences)
        self.active = receipt
        try:
            self.host = read_json("/run/agc-worker/host.json")
        except (OSError, ValueError):
            self.host = {}
        if time.monotonic() - preparation_started > 45 or admission_holds(self.host, required_memory=memory):
            receipt.update(state="interrupted", reason="preparation_timeout_or_pressure", termination_confirmed=True)
            self.persist(receipt)
            self.active = None
            return receipt
        properties = {"Slice": "agc-worker-jobs.slice", "MemoryHigh": str(memory * 9 // 10),
                      "MemoryMax": str(memory), "MemorySwapMax": "0", "CPUQuota": str(cpus * 100) + "%",
                      "CPUWeight": "10", "IOWeight": "10", "Nice": "10", "TasksMax": "512",
                      "RuntimeMaxSec": str(timeout), "TimeoutStopSec": "10", "KillMode": "control-group",
                      "OOMPolicy": "kill", "MemoryOOMGroup": "yes", "NoNewPrivileges": "yes",
                      "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
                      "ReadWritePaths": str(work) + " " + str(output), "WorkingDirectory": str(work),
                      "StandardOutput": "null", "StandardError": "null", "UMask": "0077",
                      "Environment": " ".join(("HOME=" + str(work / ".home"), "TMPDIR=" + str(work / ".tmp"),
                                                  "XDG_CACHE_HOME=" + str(work / ".cache"),
                                                  "npm_config_cache=" + str(work / ".cache/npm"),
                                                  "PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/agc-worker-adapters/browsers")),
                      "UnsetEnvironment": "DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK"}
        properties.update(toolchain_properties(adapter, job))
        properties.update(credential_properties(binding["kind"], adapter))
        command = ["systemd-run", "--quiet", "--unit=" + unit, "--service-type=exec", "--uid=agcjob"]
        command += ["--property=" + key + "=" + value for key, value in properties.items()]
        command += ["--", *argv, "--manifest", str(manifest_path), "--workspace", str(work), "--output", str(output)]
        try:
            run(command)
            receipt["state"] = "running"
        except (ValueError, subprocess.TimeoutExpired):
            # A lost start acknowledgement may mean the unit did launch.
            receipt["state"] = "unknown"
            receipt["reason"] = "start_acknowledgement_uncertain"
            run(["systemctl", "stop", unit], check=False)
            receipt["termination_confirmed"] = self.unit_empty(unit)
        self.persist(receipt)
        if receipt["state"] in TERMINAL and receipt["termination_confirmed"]:
            self.active = None
        return receipt

    def terminate(self, reason):
        receipt = self.active
        if receipt is None:
            return
        run(["systemctl", "stop", receipt["unit"]], check=False)
        receipt.update(state="unknown" if receipt["production_writes"] else "interrupted",
                       reason=reason, termination_confirmed=self.unit_empty(receipt["unit"]))
        if receipt["termination_confirmed"]:
            try:
                receipt["result"] = self.collect_result(receipt)
            except (OSError, ValueError):
                receipt["checkpoint_unavailable"] = True
        self.persist(receipt)
        if receipt["termination_confirmed"]:
            self.active = None

    def checkpoint(self, receipt):
        if receipt.get("checkpoint_requested"):
            return
        if self.adapters[receipt["kind"]].get("supports_checkpoint"):
            run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGUSR1", receipt["unit"]], check=False)
        receipt["checkpoint_requested"] = True
        self.persist(receipt)

    def tick(self):
        with self.lock:
            try:
                self.host = read_json(Path("/run/agc-worker/host.json"))
            except (OSError, ValueError):
                self.host = {}
            holds = admission_holds(self.host)
            if holds:
                self.healthy_since = None
            elif self.healthy_since is None:
                self.healthy_since = time.monotonic()
            if not self.active:
                return
            receipt = self.active
            idle = time.monotonic() - receipt["last_heartbeat_monotonic"]
            if idle >= 90:
                self.terminate("lease_lost")
                return
            if idle >= 60:
                self.checkpoint(receipt)
            if "staff_memory_reserve" in holds:
                if self.staff_pressure_since is None:
                    self.staff_pressure_since = time.monotonic()
                    self.checkpoint(receipt)
                elif time.monotonic() - self.staff_pressure_since >= 60:
                    self.terminate("staff_memory_pressure")
                    return
            else:
                self.staff_pressure_since = None
            critical = set(holds) - {"staff_cpu_pressure", "staff_memory_reserve"}
            if self.host.get("memory_available_bytes", 0) < 5_000_000_000:
                critical.add("staff_memory_critical")
            if critical:
                self.terminate(",".join(sorted(critical)))
                return
            quota = "100%" if holds else "1200%"
            run(["systemctl", "set-property", "--runtime", "agc-worker-jobs.slice", "CPUQuota=" + quota])
            info = self.unit_info(receipt["unit"])
            if info.get("ActiveState") in {"inactive", "failed"} and self.unit_empty(receipt["unit"]):
                result = info.get("Result", "unknown")
                state = {"oom-kill": "oom", "timeout": "timeout"}.get(result, "failed")
                if result == "success" and info.get("ExecMainStatus") == "0":
                    state = "succeeded"
                if state != "succeeded" and receipt["production_writes"]:
                    state = "unknown"
                receipt.update(state=state, termination_confirmed=True,
                               finished_at=time.time(), systemd_result=result,
                               memory_peak_bytes=info.get("MemoryPeak"))
                try:
                    receipt["result"] = self.collect_result(receipt)
                except (OSError, ValueError):
                    receipt.update(state="unknown" if receipt["production_writes"] else "failed",
                                   reason="invalid_result_artifacts")
                self.persist(receipt)
                self.active = None

    def collect_result(self, receipt):
        output = self.state / "jobs" / self.key({k: receipt[k] for k in BINDING}) / "output"
        inventory = []
        # Outputs are immutable only AFTER the cgroup is empty.
        for path in sorted(output.rglob("*")):
            if path.is_symlink():
                raise ValueError("result_symlink")
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_ARTIFACT or len(inventory) >= 4096:
                raise ValueError("result_limit")
            digest = digest_file(path)
            with path.open("rb") as stream:
                install_object(self.state / "objects", digest, path.stat().st_size, stream)
            inventory.append({"path": path.relative_to(output).as_posix(), "sha256": digest, "size": path.stat().st_size})
        result = {"version": 1, "binding": {k: receipt[k] for k in BINDING}, "files": inventory}
        data = canonical(result)
        if len(data) > 2 * 1024**2:
            raise ValueError("inventory_limit")
        digest = hashlib.sha256(data).hexdigest()
        import io
        install_object(self.state / "objects", digest, len(data), io.BytesIO(data))
        checkpoints = [entry for entry in inventory if entry["path"] == "checkpoint.json"]
        if checkpoints:
            receipt["checkpoint_sha256"] = checkpoints[0]["sha256"]
        domain_response = output / "response.json"
        if receipt["kind"] == "bushi.run" and receipt["production_writes"] and domain_response.is_file():
            if domain_response.stat().st_size > 1024**2:
                raise ValueError("domain_receipt_limit")
            response = read_json(domain_response)
            if response.get("ok") is False and response.get("errorType") == "write_unknown":
                receipt.update(state="unknown", reason="external_write_unknown")
        return {"manifest_sha256": digest, "manifest_size": len(data), "artifact_count": len(inventory),
                "artifacts": [{"sha256": digest, "size": len(data)}],
                "inventory_artifact": {"sha256": digest, "size": len(data)}}

    def stage(self, request):
        if self.active:
            raise ValueError("stage_while_active")
        digest = request.get("artifact_id", "")
        offset, size = request.get("offset"), request.get("size")
        if not HEX64.fullmatch(digest) or type(offset) is not int or offset < 0 or type(size) is not int or not 0 < size <= MAX_ARTIFACT:
            raise ValueError("invalid_stage_request")
        if admission_holds(self.host):
            raise ValueError("stage_admission_held")
        target = self.state / "objects" / digest
        if target.exists():
            if target.stat().st_size == size and digest_file(target) == digest:
                return {"artifact_id": digest, "offset": target.stat().st_size, "complete": True}
            target.unlink()  # Corrupt cache is never advertised as completed.
        chunk = base64.b64decode(request.get("data", ""), validate=True)
        if len(chunk) > CHUNK or offset + len(chunk) > size:
            raise ValueError("invalid_chunk")
        if self.host["disk_free_bytes"] - len(chunk) < self.host["disk_total_bytes"] * 0.15:
            raise ValueError("disk_reserve")
        partial = self.state / "incoming" / digest
        current = partial.stat().st_size if partial.exists() else 0
        if current != offset:
            return {"artifact_id": digest, "offset": current, "complete": False}
        with partial.open("ab") as stream:
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        complete = partial.stat().st_size == size
        if complete:
            if digest_file(partial) != digest:
                partial.unlink()
                raise ValueError("artifact_digest_mismatch")
            os.replace(partial, target)
            fsync_directory(target.parent)
        return {"artifact_id": digest, "offset": offset + len(chunk), "complete": complete}

    def fetch(self, request):
        digest, offset = request.get("artifact_id", ""), request.get("offset", 0)
        if not HEX64.fullmatch(digest) or type(offset) is not int or offset < 0:
            raise ValueError("invalid_fetch_request")
        target = self.state / "objects" / digest
        length = request.get("length", CHUNK)
        if type(length) is not int or not 1 <= length <= CHUNK:
            raise ValueError("invalid_fetch_length")
        with target.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        return {"artifact_id": digest, "offset": offset, "size": target.stat().st_size,
                "data": base64.b64encode(data).decode(), "eof": offset + len(data) >= target.stat().st_size}

    def acknowledge(self, request):
        binding = validate_binding(request)
        receipt = self.bound_receipt(binding)
        inventory = receipt.get("result", {}).get("inventory_artifact", {})
        if (receipt["state"] not in TERMINAL or receipt["state"] == "unknown"
                or not receipt.get("termination_confirmed")
                or not inventory.get("sha256")
                or request.get("inventory_sha256") != inventory["sha256"]):
            raise ValueError("archive_ack_rejected")
        receipt.update(centrally_archived=True, archived_at=time.time())
        self.persist(receipt)  # Archive proof is durable before scratch deletion.
        job = self.state / "jobs" / self.key(binding)
        if job.exists():
            shutil.rmtree(job)
        return {**receipt, "acknowledged": True, "workspace_pruned": True}

    def dispatch(self, request):
        with self.lock:
            if request.get("protocol_version") != 1:
                raise ValueError("unsupported_protocol")
            action = request.get("action")
            if action == "status":
                result = self.status()
            elif action == "start":
                result = self.start(request)
            elif action in {"heartbeat", "inspect", "stop"}:
                binding = validate_binding(request)
                if self.active and all(self.active[k] == binding[k] for k in BINDING):
                    if action == "heartbeat":
                        self.active["last_heartbeat_monotonic"] = time.monotonic()
                        self.persist(self.active)
                        lease = self.state / "jobs" / self.key(binding) / "lease.json"
                        atomic_json(lease, {"boot_id": self.boot_id, "lease_generation": binding["lease_generation"],
                                            "last_heartbeat_monotonic": self.active["last_heartbeat_monotonic"]})
                        os.chmod(lease, 0o644)
                    elif action == "stop":
                        self.terminate("central_stop")
                result = self.bound_receipt(binding)
            elif action == "stage":
                result = self.stage(request)
            elif action == "fetch":
                result = self.fetch(request)
            elif action == "ack":
                result = self.acknowledge(request)
            else:
                raise ValueError("unsupported_action")
            if all(key in result for key in BINDING):
                result = {**result, "binding": {key: result[key] for key in BINDING}}
            return {"protocol_version": 1, "ok": True, **result}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(20)
        try:
            line = self.rfile.readline(MAX_RPC + 1)
            if len(line) > MAX_RPC or not line.endswith(b"\n"):
                raise ValueError("request_size")
            response = self.server.worker.dispatch(json.loads(line))
        except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
            # Avoid reflecting file paths, command output or secrets in errors.
            response = {"protocol_version": 1, "ok": False, "error": "request_rejected"}
        self.wfile.write(canonical(response) + b"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("serve", "rpc", "host-sample"))
    args = parser.parse_args()
    sock = Path("/run/agc-worker/rpc.sock")
    if args.mode == "host-sample":
        # Only root can replace this file; not an exposed network RPC action.
        raw = sys.stdin.buffer.readline(16385)
        if len(raw) > 16384:
            raise SystemExit(2)
        atomic_json("/run/agc-worker/host.json", json.loads(raw))
    elif args.mode == "rpc":
        data = sys.stdin.buffer.readline(MAX_RPC + 1)
        if len(data) > MAX_RPC:
            raise SystemExit(2)
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(30)
            client.connect(str(sock))
            client.sendall(data.rstrip(b"\r\n") + b"\n")
            with client.makefile("rb") as stream:
                sys.stdout.buffer.write(stream.readline(MAX_RPC + 1))
    else:
        sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sock.exists():
            sock.unlink()
        worker = Worker()
        with socketserver.UnixStreamServer(str(sock), Handler) as server:
            os.chmod(sock, 0o600)
            server.worker = worker
            server.timeout = 1
            while True:
                server.handle_request()
                worker.tick()


if __name__ == "__main__":
    main()
