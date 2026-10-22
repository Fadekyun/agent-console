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
import queue
import re
import select
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

MAX_RPC = 512 * 1024
CHUNK = 256 * 1024
# Match the central queue object contract, including source/input blobs.
MAX_ARTIFACT = 2 * 1024**3
LEASE_STOP_SECONDS = 75
LEASE_TIMER_SECONDS = 80
# A host lifecycle stop is not a lost lease. systemd stops this supervisor with
# a bounded budget; result inventories are deferred for later reconciliation.
# Total worst case: 3s watchdog join + 1s state-lock wait + 8s systemctl stop
# + 2s unit query = 14s. The coordinator records an unconfirmed phase instead
# of extending that deadline.
SUPERVISOR_STOP_BUDGET_SECONDS = 10
LOG_DIR = Path("/var/log/agc-worker")
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 7
LOG_TOTAL_BYTES = 8 * 1024 * 1024
LOG_WRITE_LOCK = threading.RLock()
LOG_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
STATE_ROOT = Path("/var/lib/agc-worker")
CONFIG_ROOT = Path("/etc/agc-worker")
RUNTIME_ROOT = Path("/run/agc-worker")
JOB_SLICE = "agc-worker-jobs.slice"
CONTROL_SLICE = "system.slice"
JOB_USER = "agcjob"
JOB_VIEW = Path("/run/agc-job")
SLICE_CPU_QUOTA = "1200%"
PRESSURE_CPU_QUOTA = "100%"
CRITICAL_HOST_MEMORY_BYTES = 5_000_000_000
BINDING = ("job_id", "attempt_id", "lease_generation", "kind", "source_sha",
           "input_sha256", "manifest_sha256")
TERMINAL = {"succeeded", "failed", "oom", "timeout", "interrupted", "unknown"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class ResultDeferred(Exception):
    """Result inventory could not finish inside its bounded collection budget."""


def _log_line(event, fields):
    record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event}
    record.update({key: value for key, value in fields.items()
                   if isinstance(value, (str, int, float, bool)) or value is None})
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _rotate_log(path):
    for index in range(LOG_BACKUPS - 1, 0, -1):
        source = path.with_name(path.name + "." + str(index))
        if source.exists():
            os.replace(source, path.with_name(path.name + "." + str(index + 1)))
    if path.exists():
        os.replace(path, path.with_name(path.name + ".1"))


def _oldest_record_epoch(path):
    """Read one bounded first line; unknown/legacy content falls back to mtime."""
    try:
        with Path(path).open("rb") as stream:
            line = stream.readline(4096)
        record = json.loads(line.decode("utf-8", "strict"))
        stamp = str(record.get("ts", ""))
        if stamp:
            return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        pass
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def prune_logs(directory=LOG_DIR, now=None):
    """Age/size-prune only this worker's own log stream.

    Receipts, leases, objects, jobs and configured data are never candidates.
    The oldest bounded record controls eviction, so a fresh mtime cannot keep
    an eight-day-old first record alive. Whole-segment eviction is deliberate
    and may shorten retention; it never lengthens it.
    """
    now = time.time() if now is None else now
    directory = Path(directory)
    try:
        with LOG_WRITE_LOCK:
            paths = [path for path in directory.glob("worker.log*")
                     if path.is_file() and not path.is_symlink()]
            for path in paths:
                oldest = _oldest_record_epoch(path)
                if oldest is None or now - oldest > LOG_MAX_AGE_SECONDS:
                    path.unlink()
                    continue
            remaining = sorted((path for path in paths if path.exists()),
                               key=lambda path: path.stat().st_mtime)
            total = sum(path.stat().st_size for path in remaining)
            while total > LOG_TOTAL_BYTES and remaining:
                oldest = remaining.pop(0)
                total -= oldest.stat().st_size
                oldest.unlink()
    except OSError:
        pass


LOG_QUEUE = queue.Queue(maxsize=256)
LOG_THREAD = None
LOG_THREAD_LOCK = threading.Lock()


def _write_log(directory, data, now=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "worker.log"
    now = time.time() if now is None else now
    with LOG_WRITE_LOCK:
        if path.exists():
            current_day = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).date()
            today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
            if current_day != today or path.stat().st_size + len(data) > LOG_MAX_BYTES:
                _rotate_log(path)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)
        prune_logs(directory, now=now)


def _log_writer():
    while True:
        directory, data = LOG_QUEUE.get()
        try:
            _write_log(directory, data)
        except (OSError, ValueError):
            pass
        finally:
            LOG_QUEUE.task_done()


def log_event(directory, event, **fields):
    """Queue a low-volume JSONL transition; a stalled disk cannot block work."""
    global LOG_THREAD
    try:
        data = _log_line(event, fields)
        if LOG_THREAD is None or not LOG_THREAD.is_alive():
            with LOG_THREAD_LOCK:
                if LOG_THREAD is None or not LOG_THREAD.is_alive():
                    LOG_THREAD = threading.Thread(target=_log_writer, name="agc-worker-log",
                                                  daemon=True)
                    LOG_THREAD.start()
        try:
            LOG_QUEUE.put_nowait((str(directory), data))
        except queue.Full:
            pass  # Dropping a diagnostic record never changes lifecycle state.
    except (OSError, ValueError, RuntimeError, TypeError):
        pass


def flush_logs():
    """Test/operator helper; never called inside the shutdown deadline."""
    LOG_QUEUE.join()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest_file_bounded(path, deadline=None, cancel=None):
    """Hash one result file with optional deadline/cancellation checks."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise ResultDeferred("result_collection_cancelled")
            if deadline is not None and time.monotonic() > deadline:
                raise ResultDeferred("result_collection_deadline")
            hasher.update(chunk)
    return hasher.hexdigest()


def fsync_directory(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_object(objects, digest, size, stream, deadline=None, cancel=None):
    """A named object is durable and hash-verified, or does not exist yet."""
    target = Path(objects) / digest
    if target.exists() and target.stat().st_size == size:
        existing_digest = (digest_file_bounded(target, deadline, cancel)
                           if deadline is not None or cancel is not None else digest_file(target))
        if existing_digest == digest:
            return target
    descriptor, temporary_name = tempfile.mkstemp(prefix=digest + ".", suffix=".tmp", dir=objects)
    os.close(descriptor)
    temporary = Path(temporary_name)
    hasher, written = hashlib.sha256(), 0
    try:
        with temporary.open("wb") as output:
            while chunk := stream.read(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise ResultDeferred("result_collection_cancelled")
                if deadline is not None and time.monotonic() > deadline:
                    raise ResultDeferred("result_collection_deadline")
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


def atomic_json(path, value, *, mode=0o600, group=None):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        # Set access before replacement: a heartbeat must never briefly publish
        # an unreadable lease inode to the unprivileged adapter. Fsync metadata
        # together with content before publishing the new inode.
        if group is not None:
            os.fchown(stream.fileno(), -1, group)
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    fsync_directory(path.parent)


def read_json(path):
    return json.loads(Path(path).read_text())


def read_line(fd, limit, timeout):
    """One bounded frame with a total deadline, including slow partial input."""
    deadline, data = time.monotonic() + timeout, bytearray()
    while len(data) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError("request_deadline")
        chunk = os.read(fd, min(65536, limit + 1 - len(data)))
        if not chunk:
            raise ValueError("request_incomplete")
        data.extend(chunk)
        if b"\n" in data:
            if len(data) > limit or not data.endswith(b"\n") or data.count(b"\n") != 1:
                raise ValueError("request_size")
            return bytes(data)
    raise ValueError("request_size")


def run(args, check=True):
    timeout = 20
    if args and args[0] == "systemctl":
        timeout = 8 if len(args) > 1 and args[1] == "stop" else 2
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
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


def job_namespace_properties(job):
    """Expose only this job, without granting traversal of host worker state."""
    job = Path(job)
    if not job.is_absolute() or any(char.isspace() or char in ':%' for char in str(job)):
        raise ValueError("invalid_private_job_path")
    work, output = JOB_VIEW / "workspace", JOB_VIEW / "output"
    return {"BindReadOnlyPaths": str(job) + ":" + str(JOB_VIEW),
            "BindPaths": str(job / "workspace") + ":" + str(work) + " " + str(job / "output") + ":" + str(output),
            "ReadWritePaths": str(work) + " " + str(output), "WorkingDirectory": str(work),
            "Environment": " ".join(("HOME=" + str(work / ".home"), "TMPDIR=" + str(work / ".tmp"),
                "XDG_CACHE_HOME=" + str(work / ".cache"), "npm_config_cache=" + str(work / ".cache/npm"),
                "PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/agc-worker-adapters/browsers"))}


def toolchain_properties(adapter, job):
    """Only a locally installed root-owned toolchain, never caller root mounts."""
    properties = job_namespace_properties(job)
    value = adapter.get("root_directory")
    if value is None:
        return properties
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
    properties.update({"RootDirectory": str(root), "MountAPIVFS": "yes", "PrivateDevices": "yes",
                       "BindReadOnlyPaths": properties["BindReadOnlyPaths"] + " " + str(adapters) + " /etc/resolv.conf"})
    return properties


def credential_properties(kind, adapter):
    ids = adapter.get("credential_ids", [])
    if not ids:
        return {}
    if kind not in {"bushi.run", "bushi.emergency"} or ids != ["agc-bushi-organizer-state.json"]:
        raise ValueError("credential_not_allowed_for_adapter")
    parent = CONFIG_ROOT / "credentials"
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
    # Windows and the WSL/controller clocks may differ slightly. This applies
    # only to host telemetry; attempt lease expiry still uses monotonic time.
    if not -5 <= age <= 30:
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
    def __init__(self, state=None, config=None, log_dir=None):
        self.state = Path(state) if state is not None else STATE_ROOT
        self.config = Path(config) if config is not None else CONFIG_ROOT
        self.log_dir = Path(log_dir) if log_dir is not None else LOG_DIR
        self.lock = threading.RLock()
        self.active = None
        self.shutdown_requested = False
        self.shutdown_reason = None
        self.shutdown_event = threading.Event()
        self.last_log_prune = 0.0
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.started = time.monotonic()
        self.healthy_since = None
        self.host = {}
        self.staff_pressure_since = None
        self.adapters = read_json(self.config / "adapters.json")
        self.fences = read_json(self.state / "fences.json") if (self.state / "fences.json").exists() else {}
        for name in ("objects", "incoming", "receipts", "jobs"):
            (self.state / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        # Root opens sources for the unit's private bind mounts. Job code never
        # needs to traverse state or sibling jobs in the host namespace.
        os.chmod(self.state, 0o700)
        os.chmod(self.state / "jobs", 0o700)
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
                else:
                    self.stop_lease_timer(receipt)
            elif receipt.get("unit_cleanup_pending"):
                # A crash after persisting completion must not discard its
                # result merely because the retained transient unit remains.
                try:
                    run(["systemctl", "stop", receipt["unit"]])
                    receipt["unit_cleanup_pending"] = False
                    atomic_json(path, receipt)
                except (ValueError, subprocess.TimeoutExpired):
                    pass
        atomic_json(self.state / "fences.json", self.fences)
        prune_logs(self.log_dir)
        self.last_log_prune = time.monotonic()
        self.log("supervisor_start")

    def log(self, event, **fields):
        payload = {"boot_id": self.boot_id}
        payload.update(fields)
        log_event(self.log_dir, event, **payload)

    def request_shutdown(self, reason):
        """Close admission without waiting for a watchdog-owned state lock."""
        self.shutdown_requested = True
        if self.shutdown_reason is None:
            self.shutdown_reason = reason
        self.shutdown_event.set()
        # No lock and no logging here: a watchdog-owned state lock or a stalled
        # log sink must never delay admission closure.

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
        retained_exit = info.get("ActiveState") == "active" and info.get("SubState") == "exited"
        if info.get("ActiveState") not in {"inactive", "failed"} and not retained_exit:
            return False
        group = info.get("ControlGroup")
        if not group:
            return True
        event_file = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
        return not event_file.exists() or "populated 0" in event_file.read_text()

    def stop_lease_timer(self, receipt):
        timer = receipt.get("lease_timer")
        if timer:
            try:
                run(["systemctl", "stop", timer], check=False)
            except (ValueError, subprocess.TimeoutExpired):
                pass  # Target cgroup is already empty; no replay is permitted.

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
        if self.shutdown_event.is_set():
            raise ValueError("worker_stopping")
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
        cpu_quota = int(adapter.get("cpu_quota_percent", cpus * 100))
        timeout = int(adapter.get("timeout_seconds", 3600))
        if not (128 * 1024**2 <= memory <= 8 * 1024**3 and 1 <= cpus <= 12 and 1 <= cpu_quota <= cpus * 100 and 1 <= timeout <= 21600):
            raise ValueError("invalid_adapter_limits")
        status = self.status()
        holds = status["admission"]["holds"] + admission_holds(self.host, required_memory=memory)
        if holds:
            raise ValueError("admission_held:" + ",".join(sorted(set(holds))))
        argv = adapter["argv"]
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv) or not argv[0].startswith("/"):
            raise ValueError("invalid_adapter_command")
        job = self.state / "jobs" / self.key(binding)
        job.mkdir(mode=0o700)
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
        account = pwd.getpwnam(JOB_USER)
        os.chown(job, 0, account.pw_gid)
        os.chmod(job, 0o710)
        for root in (work, output):
            shutil.chown(root, user=account.pw_uid, group=account.pw_gid)
            for path in root.rglob("*"):
                shutil.chown(path, user=account.pw_uid, group=account.pw_gid)
        lease = job / "lease.json"
        atomic_json(lease, {"boot_id": self.boot_id, "lease_generation": binding["lease_generation"],
                            "last_heartbeat_monotonic": time.monotonic()}, mode=0o640, group=account.pw_gid)
        manifest_path = job / "manifest.json"
        atomic_json(manifest_path, {**manifest, "worker_binding": binding,
                    "worker_lease_file": str(JOB_VIEW / "lease.json")}, mode=0o640, group=account.pw_gid)
        unit = "agc-job-" + self.key(binding)[:32] + ".service"
        receipt = {**binding, "state": "starting", "unit": unit, "boot_id": self.boot_id,
                   "started_at": time.time(), "last_heartbeat_monotonic": time.monotonic(),
                   "termination_confirmed": False, "production_writes": manifest["production_writes"]}
        receipt["lease_timer"] = unit.replace("agc-job-", "agc-lease-").replace(".service", ".timer")
        self.persist(receipt)  # Persist intent BEFORE the executable can run.
        self.fences[binding["job_id"]] = binding["lease_generation"]
        atomic_json(self.state / "fences.json", self.fences)
        self.active = receipt
        try:
            self.host = read_json(RUNTIME_ROOT / "host.json")
        except (OSError, ValueError):
            self.host = {}
        if time.monotonic() - preparation_started > 45 or admission_holds(self.host, required_memory=memory):
            receipt.update(state="interrupted", reason="preparation_timeout_or_pressure", termination_confirmed=True)
            self.persist(receipt)
            self.active = None
            return receipt
        if self.shutdown_event.is_set():
            receipt.update(state="interrupted", reason="shutdown_during_preparation", termination_confirmed=True)
            self.persist(receipt)
            self.active = None
            self.log("job_start_suppressed", job_id=binding["job_id"], reason="shutdown_during_preparation")
            return receipt
        properties = {"Slice": JOB_SLICE, "MemoryHigh": str(memory * 9 // 10),
                      "RemainAfterExit": "yes",
                      "MemoryMax": str(memory), "MemorySwapMax": "0", "CPUQuota": str(cpu_quota) + "%",
                      "CPUWeight": "10", "IOWeight": "10", "Nice": "10", "TasksMax": "512",
                      "RuntimeMaxSec": str(timeout), "TimeoutStopSec": "5", "KillMode": "control-group",
                      "OOMPolicy": "kill", "NoNewPrivileges": "yes",
                      "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
                      "StandardOutput": "null", "StandardError": "null", "UMask": "0077",
                      "UnsetEnvironment": "DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK"}
        properties.update(toolchain_properties(adapter, job))
        properties.update(credential_properties(binding["kind"], adapter))
        command = ["systemd-run", "--quiet", "--unit=" + unit, "--service-type=exec", "--uid=" + JOB_USER,
                   "--gid=" + str(account.pw_gid)]
        command += ["--property=" + key + "=" + value for key, value in properties.items()]
        command += ["--", *argv, "--manifest", str(JOB_VIEW / "manifest.json"),
                    "--workspace", str(JOB_VIEW / "workspace"), "--output", str(JOB_VIEW / "output")]
        try:
            # A root-owned timer survives a stalled/crashed supervisor and
            # accepts no notifications from job code. Its only fixed action
            # kills this exact job cgroup; valid heartbeats restart the timer.
            lease_unit = receipt["lease_timer"].removesuffix(".timer")
            run(["systemd-run", "--quiet", "--unit=" + lease_unit,
                 "--on-active=" + str(LEASE_TIMER_SECONDS) + "s",
                 "--timer-property=AccuracySec=1s", "--timer-property=RandomizedDelaySec=0",
                 "--property=Slice=" + CONTROL_SLICE, "--property=Type=oneshot",
                 "--property=TimeoutStartSec=5", "--property=MemoryMax=32M", "--property=CPUQuota=10%",
                 "--", "/usr/bin/systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit])
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
            self.stop_lease_timer(receipt)
            self.active = None
        return receipt

    def terminate(self, reason, defer_result=False):
        receipt = self.active
        if receipt is None:
            return
        defer_result = defer_result or self.shutdown_event.is_set()
        started = time.monotonic()
        run(["systemctl", "stop", receipt["unit"]], check=False)
        receipt.update(state="unknown" if receipt["production_writes"] else "interrupted",
                       reason=reason, termination_confirmed=self.unit_empty(receipt["unit"]),
                       shutdown_requested=bool(self.shutdown_requested))
        if receipt["termination_confirmed"]:
            self.stop_lease_timer(receipt)
            if defer_result:
                # The host lifecycle budget is shorter than an artifact scan.
                # Preserve the receipt and output; central reconciliation owns
                # the deferred inventory. Never claim a collected result.
                receipt.update(result_deferred=True, result_deferred_reason=reason)
                self.log("job_result_deferred", job_id=receipt["job_id"], reason=reason)
            else:
                try:
                    receipt["result"] = self.collect_result(receipt, cancel=self.shutdown_event)
                except ResultDeferred as error:
                    reason = str(error)
                    receipt.update(result_deferred=True, result_deferred_reason=reason)
                    self.log("job_result_deferred", job_id=receipt["job_id"], reason=reason)
                except (OSError, ValueError):
                    receipt["checkpoint_unavailable"] = True
        receipt["shutdown_duration_ms"] = int((time.monotonic() - started) * 1000)
        self.persist(receipt)
        if receipt["termination_confirmed"]:
            self.active = None
            self.log("job_terminated", job_id=receipt["job_id"], reason=reason,
                     state=receipt["state"], result_deferred=bool(receipt.get("result_deferred")))

    def checkpoint(self, receipt):
        if receipt.get("checkpoint_requested"):
            return
        if self.adapters[receipt["kind"]].get("supports_checkpoint"):
            run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGUSR1", receipt["unit"]], check=False)
        receipt["checkpoint_requested"] = True
        self.persist(receipt)

    def tick(self):
        with self.lock:
            if time.monotonic() - self.last_log_prune >= 3600:
                prune_logs(self.log_dir)
                self.last_log_prune = time.monotonic()
            try:
                self.host = read_json(RUNTIME_ROOT / "host.json")
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
            # Start graceful stop early; the five-second cgroup kill grace must
            # fit inside the 90-second termination budget, not follow it.
            if idle >= LEASE_STOP_SECONDS:
                self.terminate("lease_lost", defer_result=self.shutdown_requested)
                return
            if idle >= 60:
                self.checkpoint(receipt)
            if "staff_memory_reserve" in holds:
                if self.staff_pressure_since is None:
                    self.staff_pressure_since = time.monotonic()
                    self.checkpoint(receipt)
                elif time.monotonic() - self.staff_pressure_since >= 60:
                    self.terminate("staff_memory_pressure", defer_result=self.shutdown_requested)
                    return
            else:
                self.staff_pressure_since = None
            critical = set(holds) - {"staff_cpu_pressure", "staff_memory_reserve"}
            if self.host.get("memory_available_bytes", 0) < CRITICAL_HOST_MEMORY_BYTES:
                critical.add("staff_memory_critical")
            if critical:
                self.terminate(",".join(sorted(critical)), defer_result=self.shutdown_requested)
                return
            quota = PRESSURE_CPU_QUOTA if holds else SLICE_CPU_QUOTA
            run(["systemctl", "set-property", "--runtime", JOB_SLICE, "CPUQuota=" + quota])
            info = self.unit_info(receipt["unit"])
            retained_exit = info.get("ActiveState") == "active" and info.get("SubState") == "exited"
            if (info.get("ActiveState") in {"inactive", "failed"} or retained_exit) and self.unit_empty(receipt["unit"]):
                result = info.get("Result", "unknown")
                state = {"oom-kill": "oom", "timeout": "timeout"}.get(result, "failed")
                if result == "success" and info.get("ExecMainStatus") == "0":
                    state = "succeeded"
                if state != "succeeded" and receipt["production_writes"]:
                    state = "unknown"
                receipt.update(state=state, termination_confirmed=True,
                               finished_at=time.time(), systemd_result=result,
                               memory_peak_bytes=info.get("MemoryPeak"), unit_cleanup_pending=True)
                try:
                    receipt["result"] = self.collect_result(receipt, cancel=self.shutdown_event)
                except ResultDeferred as error:
                    reason = str(error)
                    receipt.update(result_deferred=True, result_deferred_reason=reason,
                                   state="unknown" if receipt["production_writes"] else "failed")
                    self.log("job_result_deferred", job_id=receipt["job_id"], reason=reason)
                except (OSError, ValueError):
                    receipt.update(state="unknown" if receipt["production_writes"] else "failed",
                                   reason="invalid_result_artifacts")
                self.persist(receipt)
                self.active = None
                self.stop_lease_timer(receipt)
                # Keep successful transient units until their Result and
                # immutable artifacts are durable. Only then permit unit GC.
                try:
                    run(["systemctl", "stop", receipt["unit"]])
                    receipt["unit_cleanup_pending"] = False
                    self.persist(receipt)
                except (ValueError, subprocess.TimeoutExpired):
                    pass  # Safe to retry on daemon boot; completion is durable.

    def collect_result(self, receipt, deadline=None, cancel=None):
        # Ordinary completion keeps the existing complete-inventory contract.
        # A lifecycle shutdown defers collection entirely before reaching here;
        # an in-progress scan still observes cancellation between chunks.
        output = self.state / "jobs" / self.key({k: receipt[k] for k in BINDING}) / "output"
        # os.walk does not follow directory symlinks, but it would silently skip
        # them. The previous rglob inventory rejected every link, so do the same
        # before collecting deterministic sorted relative paths.
        paths = []
        for root, directories, files in os.walk(output):
            if cancel is not None and cancel.is_set():
                raise ResultDeferred("result_collection_cancelled")
            for name in directories:
                if (Path(root) / name).is_symlink():
                    raise ValueError("result_symlink")
            directories.sort()
            for name in sorted(files):
                path = Path(root) / name
                if path.is_symlink():
                    raise ValueError("result_symlink")
                if path.is_file():
                    paths.append(path)
        paths.sort(key=lambda path: path.relative_to(output).as_posix())
        inventory = []
        for path in paths:
            if cancel is not None and cancel.is_set():
                raise ResultDeferred("result_collection_cancelled")
            if deadline is not None and time.monotonic() > deadline:
                raise ResultDeferred("result_collection_deadline")
            if path.stat().st_size > MAX_ARTIFACT or len(inventory) >= 4096:
                raise ValueError("result_limit")
            digest = digest_file_bounded(path, deadline, cancel)
            with path.open("rb") as stream:
                install_object(self.state / "objects", digest, path.stat().st_size, stream,
                               deadline, cancel)
            inventory.append({"path": path.relative_to(output).as_posix(), "sha256": digest,
                              "size": path.stat().st_size})
        if cancel is not None and cancel.is_set():
            raise ResultDeferred("result_collection_cancelled")
        result = {"version": 1, "binding": {k: receipt[k] for k in BINDING}, "files": inventory}
        data = canonical(result)
        if len(data) > 2 * 1024**2:
            raise ValueError("inventory_limit")
        digest = hashlib.sha256(data).hexdigest()
        import io
        install_object(self.state / "objects", digest, len(data), io.BytesIO(data), deadline, cancel)
        checkpoints = [entry for entry in inventory if entry["path"] == "checkpoint.json"]
        if checkpoints:
            receipt["checkpoint_sha256"] = checkpoints[0]["sha256"]
        domain_response = output / "response.json"
        if receipt["kind"] in {"bushi.run", "bushi.emergency"} and receipt["production_writes"] and domain_response.is_file():
            if domain_response.stat().st_size > 1024**2:
                raise ValueError("domain_receipt_limit")
            response = read_json(domain_response)
            if not isinstance(response, dict):
                raise ValueError("invalid_domain_receipt")
            if response.get("ok") is False and response.get("errorType") == "write_unknown":
                receipt.update(state="unknown", reason="external_write_unknown")
        return {"manifest_sha256": digest, "manifest_size": len(data), "artifact_count": len(inventory),
                "artifacts": [{"sha256": digest, "size": len(data)}],
                "inventory_artifact": {"sha256": digest, "size": len(data)}}

    def stage(self, request):
        if self.shutdown_event.is_set():
            raise ValueError("worker_stopping")
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
        with self.lock:
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
        # Pruning a large old build must not block the active job's watchdog.
        if job.exists():
            shutil.rmtree(job)
        return {**receipt, "acknowledged": True, "workspace_pruned": True}

    def dispatch(self, request):
        if not isinstance(request, dict) or request.get("protocol_version") != 1:
            raise ValueError("unsupported_protocol")
        action = request.get("action")
        # The RPC server handles requests serially. Bulk transfer and old-job
        # pruning need not hold the active state lock against the watchdog.
        if action in {"fetch", "stage", "ack"}:
            result = {"fetch": self.fetch, "stage": self.stage, "ack": self.acknowledge}[action](request)
            if all(key in result for key in BINDING):
                result = {**result, "binding": {key: result[key] for key in BINDING}}
            return {"protocol_version": 1, "ok": True, **result}
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
                        now = time.monotonic()
                        if now - self.active["last_heartbeat_monotonic"] >= LEASE_STOP_SECONDS:
                            self.terminate("lease_lost")
                            result = self.bound_receipt(binding)
                            return {"protocol_version": 1, "ok": True, **result, "binding": binding}
                        if self.active["state"] == "running":
                            # Renew the independent root timer BEFORE recording
                            # the accepted heartbeat. Failed renewal cannot
                            # extend the supervisor's local lease.
                            run(["systemctl", "restart", self.active["lease_timer"]])
                        self.active["last_heartbeat_monotonic"] = now
                        self.persist(self.active)
                        lease = self.state / "jobs" / self.key(binding) / "lease.json"
                        atomic_json(lease, {"boot_id": self.boot_id, "lease_generation": binding["lease_generation"],
                                            "last_heartbeat_monotonic": self.active["last_heartbeat_monotonic"]},
                                    mode=0o640, group=lease.parent.stat().st_gid)
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
        self.connection.settimeout(2)
        try:
            line = read_line(self.connection.fileno(), MAX_RPC, 2)
            response = self.server.worker.dispatch(json.loads(line))
        except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
            # Avoid reflecting file paths, command output or secrets in errors.
            response = {"protocol_version": 1, "ok": False, "error": "request_rejected"}
        self.wfile.write(canonical(response) + b"\n")


def watchdog_loop(instance, stopping):
    """Lease enforcement does not depend on an RPC finishing its input/output."""
    while not stopping.is_set():
        try:
            instance.tick()
        except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            # Do not silently lose the watchdog after a control/telemetry
            # failure. The separately renewed root-owned systemd timer still
            # kills the group if control calls or this process stop progressing.
            sys.stderr.write("worker_watchdog_error:" + type(error).__name__ + "\n")
            try:
                with instance.lock:
                    instance.terminate("watchdog_control_error")
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
                pass
        stopping.wait(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("serve", "rpc", "host-sample"))
    args = parser.parse_args()
    sock = RUNTIME_ROOT / "rpc.sock"
    if args.mode == "host-sample":
        # Only root can replace this file; not an exposed network RPC action.
        raw = read_line(sys.stdin.fileno(), 16384, 20)
        atomic_json(RUNTIME_ROOT / "host.json", json.loads(raw))
    elif args.mode == "rpc":
        data = read_line(sys.stdin.fileno(), MAX_RPC, 20)
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
        instance = Worker()
        stopping = threading.Event()
        def _stop(signum, _frame):
            # Publish admission closure immediately; the main loop performs the
            # bounded, lock-held termination after the current request returns.
            instance.shutdown_requested = True
            instance.shutdown_reason = "signal_" + str(signum)
            instance.shutdown_event.set()
            stopping.set()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, _stop)
        with socketserver.UnixStreamServer(str(sock), Handler) as server:
            os.chmod(sock, 0o600)
            server.worker = instance
            server.timeout = 1
            watchdog = threading.Thread(target=watchdog_loop, args=(instance, stopping),
                                        name="agc-worker-watchdog", daemon=True)
            watchdog.start()
            try:
                while not stopping.is_set():
                    server.handle_request()
            finally:
                stopping.set()
                instance.request_shutdown("supervisor_shutdown")
                watchdog.join(timeout=3)
                if instance.lock.acquire(timeout=1):
                    try:
                        instance.terminate("supervisor_shutdown", defer_result=True)
                    finally:
                        instance.lock.release()
                else:
                    # systemd's bounded stop remains the last resort. The next
                    # boot classifies any receipt that this shutdown could not
                    # update; never invent a collected result.
                    instance.log("supervisor_stop_lock_unavailable", reason=instance.shutdown_reason)
                instance.log("supervisor_stop", reason=instance.shutdown_reason)


if __name__ == "__main__":
    main()
