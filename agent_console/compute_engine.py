"""Single-lane dispatcher for the bounded, read-only first compute adapter."""
from dataclasses import asdict
import json
import logging
import threading
import time

from .compute_policy import WORKERS, laptop_availability
from .compute_queue import ComputeQueue
from .compute_sampler import ComputeSampler, maintenance_report

log = logging.getLogger(__name__)


class ComputeEngine:
    def __init__(self, state_dir, *, enabled=False, sampler=None, clock=time.time):
        self.queue = ComputeQueue(state_dir, clock=clock)
        self.enabled = enabled is True
        self.clock = clock
        self.sampler = sampler or ComputeSampler(filesystem_path=state_dir)
        self._lock = threading.Lock()
        self.telemetry = None

    def tick(self):
        # SQLite also protects different service processes; this lock avoids
        # duplicate probes within a process. Neither lock spans a remote job.
        if not self._lock.acquire(blocking=False):
            return None
        try:
            self.queue.expire()
            held = self.queue.held()
            if self.enabled and not held:
                self.telemetry = self.sampler.sample(enabled=True, operator_hold=False, now=self.clock())
            else:
                self.telemetry = self.sampler.sample(enabled=False, operator_hold=held, now=self.clock())
                self.telemetry['enabled'] = self.enabled
            attempt = self.queue.reserve(self.telemetry)
            if attempt is None:
                return None
            if json.loads(attempt['profile_json'])['name'] != 'maintenance.report':
                raise ValueError('no verified adapter for this profile')
            # The only admitted profile is a fixed in-process report. There is
            # no shell, subprocess, browser, deployment, or publication hook.
            self.queue.heartbeat(attempt['id'], attempt['generation'])
            report = maintenance_report(self.telemetry)
            self.queue.finish(attempt['id'], attempt['generation'], report=report)
            return self.queue.inspect(attempt['job_id'])
        finally:
            # On any unexpected exception retain the reservation. Expiry will
            # expose an unknown receipt, never silently replay the attempt.
            self._lock.release()

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
                value['availability'] = laptop_availability(self.clock())
            workers.append(value)
        return {**self.queue.status(), 'enabled': self.enabled, 'held': held,
                'workers': workers, 'telemetry': self.telemetry}
