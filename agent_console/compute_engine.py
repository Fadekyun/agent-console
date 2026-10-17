"""Durable single-lane dispatch per physical host, with fenced remote receipts."""
from dataclasses import asdict
import json
import base64
from pathlib import PurePosixPath
import logging
import threading
import time

from .compute_artifacts import ComputeArtifacts, CHUNK_BYTES
from .compute_manifest import artifact
from .compute_policy import WORKERS, laptop_availability, laptop_schedule_reason
from .compute_queue import ComputeQueue
from .compute_sampler import ComputeSampler, LaptopSampler, maintenance_report
from .compute_transport import SSHWorker, WorkerUnavailable, binding, validate_binding
from .workflow_store import canonical

log = logging.getLogger(__name__)


class ComputeEngine:
    def __init__(self, state_dir, *, enabled=False, sampler=None, clock=time.time, worker=None):
        self.queue = ComputeQueue(state_dir, clock=clock)
        self.enabled = enabled is True
        self.clock = clock
        self.sampler = sampler or ComputeSampler(filesystem_path=state_dir)
        self.worker = worker if worker is not None else SSHWorker.from_env()
        self.laptop_sampler = LaptopSampler(self.worker) if self.worker else None
        self.artifacts = ComputeArtifacts(state_dir)
        self._lock = threading.Lock()
        self.telemetry = None
        self.laptop_telemetry = None

    def tick(self):
        if not self._lock.acquire(blocking=False):
            return None
        try:
            self.queue.expire()
            held = self.queue.held()
            self.telemetry = self.sampler.sample(enabled=self.enabled and not held,
                                                 operator_hold=held, now=self.clock())
            self.telemetry['enabled'] = self.enabled
            # Existing leases must be inspected/drained even under an owner hold;
            # holds prevent new starts, never silently abandon a running process.
            remote_result = self._remote_tick(held) if self.worker else None
            attempt = self.queue.reserve(self.telemetry)
            if attempt is None:
                return remote_result
            if json.loads(attempt['profile_json'])['name'] != 'maintenance.report':
                raise ValueError('no verified local adapter for this profile')
            self.queue.heartbeat(attempt['id'], attempt['generation'])
            self.queue.finish(attempt['id'], attempt['generation'], report=maintenance_report(self.telemetry))
            return self.queue.inspect(attempt['job_id'])
        finally:
            self._lock.release()

    def _remote_tick(self, held):
        if laptop_schedule_reason(self.clock()) == 'schedule_closed':
            # Even a status RPC could start an otherwise stopped WSL guest.
            # Do not contact or wake the worker on Monday/Tuesday.
            self.laptop_telemetry = {'physical_host': 'agc-laptop', 'guest_id': 'AGC-Worker',
                'enabled': self.enabled, 'operator_hold': held, 'online': False, 'readiness': 'schedule_closed'}
            self.queue.reserve(self.laptop_telemetry)
            return None
        self.laptop_telemetry = self.laptop_sampler.sample(
            enabled=self.enabled, operator_hold=held, now=self.clock())
        for attempt in self.queue.active_attempts('agc-laptop'):
            try:
                self._advance(attempt, held=held)
            except WorkerUnavailable:
                # The worker watchdog ends unattended work. Until a fresh bound
                # terminal receipt proves that, central keeps its reservation.
                pass
            except (ValueError, KeyError, TypeError):
                self.queue.mark_unknown(attempt['id'], reason='remote_protocol_invalid')
        self._acknowledge_archived()
        if not self.enabled or held:
            return None
        attempt = self.queue.reserve(self.laptop_telemetry)
        if attempt is not None:
            try:
                self._advance(attempt, held=False)
            except WorkerUnavailable:
                pass
            except (ValueError, KeyError, TypeError):
                self.queue.mark_unknown(attempt['id'], reason='remote_protocol_invalid')
            return self.queue.inspect(attempt['job_id'])
        return None

    def _upload(self, description, attempt, *, budget):
        artifact(description)
        if not self.artifacts.has(description):
            raise WorkerUnavailable('source_artifact_not_ready')
        offset = 0
        while time.monotonic() < budget:
            chunk = self.artifacts.get(description['sha256'], offset=offset)
            response = self.worker.call('stage', artifact_id=description['sha256'],
                offset=offset, data=chunk['data'], size=description['size'], final=chunk['eof'])
            next_offset = response.get('offset')
            if type(next_offset) is not int or not 0 <= next_offset <= description['size']:
                raise ValueError('invalid staged artifact offset')
            if response.get('complete') is True:
                if next_offset != description['size']:
                    raise ValueError('incomplete artifact marked complete')
                return True
            if next_offset == offset:
                raise WorkerUnavailable('artifact_staging_stalled')
            offset = next_offset
            self.queue.heartbeat(attempt['id'], attempt['generation'])
        return False

    def _fetch(self, description, *, budget, attempt):
        from .compute_retention import ComputeRetention
        artifact(description)
        ComputeRetention(self).pin_upload(description['sha256'], 'attempt:' + attempt['id'])
        if self.artifacts.has(description):
            return True
        offset = self.artifacts.offset(description)
        while time.monotonic() < budget:
            response = self.worker.call('fetch', artifact_id=description['sha256'], offset=offset, length=CHUNK_BYTES)
            if (response.get('artifact_id') != description['sha256'] or response.get('offset') != offset
                    or response.get('size') != description['size']):
                raise ValueError('artifact response mismatch')
            result = self.artifacts.put(description['sha256'], size=description['size'], offset=offset,
                                        data=response['data'], final=response.get('eof', False))
            if result['complete']:
                return True
            if result['offset'] <= offset:
                raise ValueError('artifact fetch made no progress')
            offset = result['offset']
        return False

    def _advance(self, attempt, *, held):
        job = self.queue.inspect(attempt['job_id'])
        expected = binding(job, attempt)
        manifest = json.loads(job['manifest_json'])
        # Only the process that durably claims start can send it. A lost start
        # acknowledgement is inspected, never resent (including after restart).
        if attempt['remote_start_sent_at'] is None and attempt['state'] != 'unknown':
            if held or not self.enabled:
                return
            self.queue.heartbeat(attempt['id'], attempt['generation'])
            budget = time.monotonic() + 5
            for description in [manifest['source_artifact']] + ([manifest['inputs_artifact']] if 'inputs_artifact' in manifest else []):
                if not self._upload(description, attempt, budget=budget):
                    return
            if not self.queue.claim_remote_start(attempt['id'], attempt['generation']):
                return
            response = self.worker.call('start', **expected, manifest=manifest)
        else:
            response = self.worker.call('inspect', **expected)
        validate_binding(response, expected)
        state = response.get('state')
        terminated = response.get('termination_confirmed') is True
        terminal_states = {'completed', 'succeeded', 'failed', 'stopped', 'oom', 'timeout', 'interrupted'}
        external_uncertain = (attempt.get('external_write_unknown')
            or response.get('reason') == 'external_write_unknown'
            or (manifest['production_writes'] and response.get('write_outcome') != 'not_started'
                and (state == 'unknown' or (terminated and state not in {'completed', 'succeeded'})
                     or attempt['state'] == 'unknown')))
        if external_uncertain:
            # Persist the business fence before fetching anything: disk/transport
            # failures must not allow the other host to repeat the external action.
            self.queue.mark_external_unknown(attempt['id'], attempt['generation'], terminated=terminated)
            report = response.get('result')
            if terminated and isinstance(report, dict) and report.get('inventory_artifact'):
                if self._archive_result(report, expected, attempt):
                    self.queue.mark_external_unknown(attempt['id'], attempt['generation'], terminated=True,
                        report={**report, 'binding': expected, 'external_write_unknown': True})
            return
        if attempt['state'] == 'unknown':
            if terminated and state in terminal_states:
                self.queue.reconcile_terminated(attempt['id'],
                    evidence='Authenticated worker terminal receipt matches immutable attempt binding; result requires review',
                    actor='compute-dispatcher', failure='oom' if state == 'oom' or response.get('failure') == 'oom' else None,
                    write_not_started=response.get('write_outcome') == 'not_started')
            return
        if terminated and state in terminal_states:
            if state in {'completed', 'succeeded'}:
                report = response.get('result')
                self.queue.heartbeat(attempt['id'], attempt['generation'])
                if not self._archive_result(report, expected, attempt):
                    return
                self.queue.finish(attempt['id'], attempt['generation'], report={**report, 'binding': expected})
            else:
                self.queue.finish(attempt['id'], attempt['generation'],
                                  failure='oom' if state == 'oom' or response.get('failure') == 'oom' else 'adapter_failed')
            return
        if state not in {'starting', 'running', 'checkpointing', 'stopping'} or terminated:
            self.queue.mark_unknown(attempt['id'], reason='remote_state_uncertain')
            return
        if held or not self.enabled or laptop_schedule_reason(self.clock()) or self.clock() >= attempt['started_at'] + json.loads(attempt['profile_json'])['timeout_seconds'] - 30:
            self.worker.call('stop', **expected)
            return
        heartbeat = self.worker.call('heartbeat', **expected)
        validate_binding(heartbeat, expected)
        self.queue.heartbeat(attempt['id'], attempt['generation'],
                             checkpoint_digest=heartbeat.get('checkpoint_sha256'))

    def _archive_result(self, report, expected, attempt):
        if not isinstance(report, dict) or len(canonical(report).encode()) > 8192:
            raise ValueError('invalid result receipt')
        artifacts = report.get('artifacts')
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 10:
            raise ValueError('result must name bounded immutable artifacts')
        budget = time.monotonic() + 5
        for description in artifacts:
            if not self._fetch(description, budget=budget, attempt=attempt):
                return False
        for description in self._result_descriptions(report, expected):
            if not self._fetch(description, budget=budget, attempt=attempt):
                return False
        return True

    def _acknowledge_archived(self):
        for attempt in self.queue.pending_remote_acks()[:1]:
            try:
                job = self.queue.inspect(attempt['job_id'])
                expected = binding(job, attempt)
                response = self.worker.call('ack', **expected, inventory_sha256=attempt['inventory_digest'])
                validate_binding(response, expected)
                if response.get('acknowledged') is not True:
                    raise ValueError('worker archive not acknowledged')
                self.queue.confirm_remote_ack(attempt['id'])
            except (WorkerUnavailable, ValueError, KeyError, TypeError):
                # Archival acknowledgement is idempotent and has its own durable
                # outbox. Failure never undoes the accepted central result.
                pass

    def _result_descriptions(self, result, expected):
        inventory = result.get('inventory_artifact')
        if inventory is None:
            return result['artifacts']
        artifact(inventory)
        if inventory['size'] > 2 * 1024 ** 2 or inventory not in result['artifacts']:
            raise ValueError('invalid result inventory')
        raw, offset = bytearray(), 0
        while offset < inventory['size']:
            chunk = self.artifacts.get(inventory['sha256'], offset=offset)
            data = base64.b64decode(chunk['data'], validate=True)
            if not data:
                raise ValueError('incomplete inventory')
            raw.extend(data)
            offset += len(data)
        value = json.loads(raw)
        if value.get('version') != 1 or value.get('binding') != expected:
            raise ValueError('result inventory binding mismatch')
        files = value.get('files')
        if not isinstance(files, list) or len(files) > 4096:
            raise ValueError('result inventory exceeds bounds')
        result_descriptions, seen = [inventory], set()
        for item in files:
            name = item.get('path')
            if (not isinstance(name, str) or not name or len(name) > 500 or name in seen
                    or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                    or '\\' in name or '\x00' in name):
                raise ValueError('invalid inventory path')
            seen.add(name)
            result_descriptions.append(artifact({'sha256': item['sha256'], 'size': item['size']}))
        return result_descriptions

    def result_artifact_ids(self, result):
        return {item['sha256'] for item in self._result_descriptions(result, result['binding'])}

    def status(self):
        self.queue.expire()
        held = self.queue.held()
        workers = []
        for worker in WORKERS.values():
            value = asdict(worker)
            if worker.worker_id == 'ct115-local':
                value['enabled'] = self.enabled
                value['availability'] = ('worker_disabled' if not self.enabled else
                                         'operator_hold' if held else 'requires_fresh_admission')
            else:
                value['enabled'] = self.enabled and self.worker is not None
                value['availability'] = ((self.laptop_telemetry or {}).get('readiness') or
                                         laptop_availability(self.clock()))
            workers.append(value)
        return {**self.queue.status(), 'enabled': self.enabled, 'held': held,
                'workers': workers, 'telemetry': self.telemetry, 'laptop_telemetry': self.laptop_telemetry}
