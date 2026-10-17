"""Durable compute receipts in the existing connected-work store.

Compute attempts deliberately do not masquerade as interactive tmux sessions.
Lease expiry preserves the physical-host reservation until reconciliation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

from .compute_policy import PROFILES, admission
from .compute_manifest import validate_manifest
from .workflow_store import WorkflowStore, bounded_text, canonical, identifier

LEASE_SECONDS = 120
ACTIVE = ('starting', 'running', 'unknown')


class ComputeQueue:
    def __init__(self, state_dir: Path, *, clock=time.time):
        self.store = WorkflowStore(state_dir)
        self.clock = clock
        self.store.migrate()
        with self.store.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS compute_schema(version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM compute_schema').fetchone()
            if row and row[0] not in (1, 2, 3):
                raise ValueError('unsupported compute schema; preserve state')
            if not row:
                db.execute('INSERT INTO compute_schema VALUES(1)')
            for sql in (
                '''CREATE TABLE IF NOT EXISTS compute_control(
                    id INTEGER PRIMARY KEY CHECK(id=1), held INTEGER NOT NULL CHECK(held IN (0,1)))''',
                '''CREATE TABLE IF NOT EXISTS compute_jobs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, execution_target TEXT NOT NULL,
                    source_digest TEXT NOT NULL, input_digest TEXT NOT NULL,
                    request_key TEXT UNIQUE NOT NULL, spec_hash TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT, generation INTEGER NOT NULL DEFAULT 0,
                    actor TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS compute_attempts(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES compute_jobs(id),
                    generation INTEGER NOT NULL, physical_host TEXT NOT NULL, guest_id TEXT NOT NULL,
                    state TEXT NOT NULL, lease_until REAL NOT NULL, heartbeat_at REAL NOT NULL,
                    started_at REAL NOT NULL, ended_at REAL, profile_json TEXT NOT NULL,
                    checkpoint_digest TEXT, result_json TEXT, result_digest TEXT, reason TEXT,
                    UNIQUE(job_id,generation))''',
                '''CREATE TABLE IF NOT EXISTS compute_reservations(
                    physical_host TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL
                    REFERENCES compute_attempts(id), guest_id TEXT NOT NULL,
                    memory_max_bytes INTEGER NOT NULL, scratch_bytes INTEGER NOT NULL,
                    cpu_cores REAL NOT NULL)''',
                'CREATE INDEX IF NOT EXISTS compute_pending ON compute_jobs(state,created_at)',
            ):
                db.execute(sql)
            # Additive migration, preserving v1 receipts and immutable specs.
            columns = {r['name'] for r in db.execute('PRAGMA table_info(compute_jobs)')}
            for name, definition in (
                ('manifest_json', 'TEXT'), ('manifest_digest', 'TEXT'),
                ('dependencies_json', "TEXT NOT NULL DEFAULT '[]'"),
                ('priority', 'INTEGER NOT NULL DEFAULT 0'),
                ('not_before', 'REAL'), ('deadline', 'REAL'),
            ):
                if name not in columns:
                    db.execute(f'ALTER TABLE compute_jobs ADD COLUMN {name} {definition}')
            attempt_columns = {r['name'] for r in db.execute('PRAGMA table_info(compute_attempts)')}
            if 'remote_start_sent_at' not in attempt_columns:
                db.execute('ALTER TABLE compute_attempts ADD COLUMN remote_start_sent_at REAL')
            for name, definition in (('external_write_unknown', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('termination_confirmed_at', 'REAL')):
                if name not in attempt_columns:
                    db.execute(f'ALTER TABLE compute_attempts ADD COLUMN {name} {definition}')
            db.execute('CREATE TABLE IF NOT EXISTS compute_resource_reservations(resource TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES compute_attempts(id))')
            db.execute('CREATE TABLE IF NOT EXISTS compute_remote_acks(attempt_id TEXT PRIMARY KEY REFERENCES compute_attempts(id), inventory_digest TEXT NOT NULL, acknowledged_at REAL)')
            db.execute('UPDATE compute_schema SET version=3')
            db.execute('INSERT OR IGNORE INTO compute_control VALUES(1,1)')

    def held(self):
        with self.store.connect() as db:
            return bool(db.execute('SELECT held FROM compute_control WHERE id=1').fetchone()[0])

    def set_hold(self, *, held, actor):
        if not isinstance(held, bool):
            raise ValueError('held must be a boolean')
        with self.store.connect(write=True) as db:
            db.execute('UPDATE compute_control SET held=? WHERE id=1', (int(held),))
            self._event(db, 'hold', 'n100', {'held': held}, actor)
        return {'held': held}

    def _event(self, db, kind, target, detail, actor='compute-dispatcher'):
        self.store.event(db, 'compute.' + kind, target, detail, actor)

    @staticmethod
    def _digest(value):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('expected a lowercase SHA-256 digest')
        return value

    def enqueue(self, *, kind, execution_target, source_digest, input_digest, request_key, actor,
                manifest=None, dependencies=None, priority=0, not_before=None, deadline=None):
        if kind not in PROFILES:
            raise ValueError('unsupported compute profile')
        profile = PROFILES[kind]
        if execution_target != profile.physical_host:
            raise ValueError('execution target does not match the fixed profile')
        self._digest(source_digest)
        self._digest(input_digest)
        bounded_text(request_key, 'request key', 100, True)
        bounded_text(actor, 'actor', 200, True)
        spec = dict(kind=kind, execution_target=execution_target,
                    source_digest=source_digest, input_digest=input_digest)
        encoded = manifest_digest = None
        if manifest is not None:
            if kind == 'maintenance.report':
                raise ValueError('local maintenance does not accept execution manifests')
            encoded, manifest_digest = validate_manifest(manifest, kind=kind,
                source_digest=source_digest, input_digest=input_digest)
        dependencies = [] if dependencies is None else dependencies
        if (not isinstance(dependencies, list) or len(dependencies) > 20
                or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError('invalid dependency list')
        if type(priority) is not int or not 0 <= priority <= 100:
            raise ValueError('priority must be an integer from 0 to 100')
        import math
        for value in (not_before, deadline):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError('invalid scheduling timestamp')
        if not_before is not None and deadline is not None and not_before >= deadline:
            raise ValueError('admission window is empty')
        # Preserve v1 idempotency hashes for jobs without new scheduling fields.
        if manifest is not None or dependencies or priority or not_before is not None or deadline is not None:
            spec.update(manifest_digest=manifest_digest, dependencies=dependencies,
                        priority=priority, not_before=not_before, deadline=deadline)
        spec_hash = hashlib.sha256(canonical(spec).encode()).hexdigest()
        with self.store.connect(write=True) as db:
            prior = db.execute('SELECT * FROM compute_jobs WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                if actor.startswith('producer:') and prior['actor'] != actor:
                    raise PermissionError('request key belongs to a different actor')
                if prior['spec_hash'] != spec_hash:
                    raise ValueError('request key already identifies a different immutable job')
                return dict(prior)
            for dependency in dependencies:
                prior_dependency = db.execute('SELECT actor FROM compute_jobs WHERE id=?', (dependency,)).fetchone()
                if prior_dependency is None:
                    raise ValueError('dependency must already exist')
                if actor.startswith('producer:') and prior_dependency['actor'] != actor:
                    raise PermissionError('producer dependencies must belong to the same producer')
            job_id, now = identifier('compute'), self.clock()
            db.execute('''INSERT INTO compute_jobs(id,kind,execution_target,source_digest,input_digest,
                request_key,spec_hash,state,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?,?)''',
                (job_id, kind, execution_target, source_digest, input_digest, request_key, spec_hash, actor, now, now))
            db.execute('UPDATE compute_jobs SET manifest_json=?,manifest_digest=?,dependencies_json=?,priority=?,not_before=?,deadline=? WHERE id=?',
                       (encoded, manifest_digest, canonical(dependencies), priority, not_before, deadline, job_id))
            self._event(db, 'queued', job_id, spec, actor)
            return dict(db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone())

    def inspect(self, job_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('unknown compute job')
            result = dict(row)
            result['attempts'] = [dict(a) for a in db.execute(
                'SELECT * FROM compute_attempts WHERE job_id=? ORDER BY generation', (job_id,))]
            return result

    def status(self):
        with self.store.connect() as db:
            return {'jobs': [dict(r) for r in db.execute(
                'SELECT * FROM compute_jobs ORDER BY created_at DESC,id DESC LIMIT 100')],
                'reservations': [dict(r) for r in db.execute('SELECT * FROM compute_reservations')],
                'resource_reservations': [dict(r) for r in db.execute('SELECT * FROM compute_resource_reservations')],
                'counts': {r['state']: r['n'] for r in db.execute(
                    'SELECT state,count(*) AS n FROM compute_jobs GROUP BY state')}}

    def _expire(self, db, now):
        rows = db.execute("SELECT * FROM compute_attempts WHERE state IN ('starting','running') AND lease_until<=?", (now,)).fetchall()
        for row in rows:
            db.execute("UPDATE compute_attempts SET state='unknown',reason='lease_expired' WHERE id=?", (row['id'],))
            db.execute("UPDATE compute_jobs SET state='unknown',reason='reconciliation_required',updated_at=? WHERE id=?", (now, row['job_id']))
            self._event(db, 'unknown', row['id'], {'reason': 'lease_expired'})

    def expire(self):
        with self.store.connect(write=True) as db:
            self._expire(db, self.clock())

    def reserve(self, telemetry):
        """Admission and host reservation share one SQLite write transaction."""
        from dataclasses import asdict
        now = self.clock()
        with self.store.connect(write=True) as db:
            self._expire(db, now)
            # Recheck the durable hold inside the reservation transaction: a
            # control request after sampling must still prevent a new launch.
            telemetry = {**(telemetry or {}), 'operator_hold': bool(db.execute(
                'SELECT held FROM compute_control WHERE id=1').fetchone()[0]) or (telemetry or {}).get('operator_hold', True)}
            reservations = [dict(r) for r in db.execute('SELECT * FROM compute_reservations')]
            # A disconnected laptop queue cannot starve the N100 report lane.
            target = telemetry.get('physical_host', 'n100')
            for job in db.execute("""SELECT * FROM compute_jobs WHERE state IN ('queued','blocked')
                    AND execution_target=? ORDER BY priority DESC, deadline IS NULL, deadline,
                    created_at,id LIMIT 100""", (target,)).fetchall():
                profile = PROFILES[job['kind']]
                reason = None
                if job['deadline'] is not None and now >= job['deadline']:
                    reason = 'deadline_passed_requires_review'
                elif job['not_before'] is not None and now < job['not_before']:
                    reason = 'not_before'
                elif profile.physical_host == 'agc-laptop' and not job['manifest_json']:
                    reason = 'manifest_required'
                for dependency in json.loads(job['dependencies_json']):
                    dep = db.execute('SELECT state FROM compute_jobs WHERE id=?', (dependency,)).fetchone()
                    if dep is None or dep['state'] != 'completed':
                        reason = reason or 'dependency_not_completed'
                reason = reason or admission(profile, telemetry, reservations, now)
                if not reason and any(db.execute('SELECT attempt_id FROM compute_resource_reservations WHERE resource=?', (resource,)).fetchone() for resource in profile.resources):
                    reason = 'shared_resource_reserved'
                if not reason and any(r['physical_host'] == profile.physical_host for r in reservations):
                    reason = 'physical_host_reserved'
                if reason:
                    db.execute("UPDATE compute_jobs SET state='blocked',reason=?,updated_at=? WHERE id=?", (reason, now, job['id']))
                    continue
                attempt_id, generation = identifier('compute-attempt'), job['generation'] + 1
                db.execute('''INSERT INTO compute_attempts(id,job_id,generation,physical_host,guest_id,state,
                    lease_until,heartbeat_at,started_at,profile_json) VALUES(?,?,?,?,?,'starting',?,?,?,?)''',
                    (attempt_id, job['id'], generation, profile.physical_host, profile.guest_id,
                     now + LEASE_SECONDS, now, now, canonical(asdict(profile))))
                for resource in profile.resources:
                    db.execute('INSERT INTO compute_resource_reservations VALUES(?,?)', (resource, attempt_id))
                db.execute('INSERT INTO compute_reservations VALUES(?,?,?,?,?,?)',
                    (profile.physical_host, attempt_id, profile.guest_id, profile.memory_max_bytes, profile.scratch_bytes, profile.cpu_cores))
                db.execute("UPDATE compute_jobs SET state='starting',reason=NULL,generation=?,updated_at=? WHERE id=?", (generation, now, job['id']))
                self._event(db, 'reserved', attempt_id, {'job_id': job['id'], 'generation': generation, 'physical_host': profile.physical_host})
                return dict(db.execute('SELECT * FROM compute_attempts WHERE id=?', (attempt_id,)).fetchone())
            return None

    def _current(self, db, attempt_id, generation):
        row = db.execute('''SELECT a.* FROM compute_attempts a JOIN compute_jobs j ON j.id=a.job_id
            WHERE a.id=? AND a.generation=? AND j.generation=a.generation''', (attempt_id, generation)).fetchone()
        if row is None or row['state'] not in ('starting', 'running') or row['lease_until'] <= self.clock():
            raise ValueError('stale or unresolved compute attempt')
        return row

    def heartbeat(self, attempt_id, generation, *, checkpoint_digest=None):
        if checkpoint_digest is not None:
            self._digest(checkpoint_digest)
        now = self.clock()
        with self.store.connect(write=True) as db:
            row = self._current(db, attempt_id, generation)
            # A heartbeat cannot extend the immutable profile's overall timeout.
            timeout = json.loads(row['profile_json'])['timeout_seconds']
            if now >= row['started_at'] + timeout:
                raise ValueError('attempt exceeded its runtime budget')
            db.execute("UPDATE compute_attempts SET state='running',heartbeat_at=?,lease_until=?,checkpoint_digest=COALESCE(?,checkpoint_digest) WHERE id=?",
                (now, min(now + LEASE_SECONDS, row['started_at'] + timeout), checkpoint_digest, attempt_id))
            db.execute("UPDATE compute_jobs SET state='running',updated_at=? WHERE id=?", (now, row['job_id']))

    def finish(self, attempt_id, generation, *, report=None, failure=None):
        if failure not in (None, 'adapter_failed', 'oom'):
            raise ValueError('unsupported failure classification')
        if (report is None) == (failure is None):
            raise ValueError('exactly one report or failure is required')
        encoded = canonical(report) if report is not None else None
        if encoded is not None:
            bounded_text(encoded, 'report', 8192, True)
        now = self.clock()
        with self.store.connect(write=True) as db:
            row = self._current(db, attempt_id, generation)
            state = 'failed' if failure else 'completed'
            db.execute('UPDATE compute_attempts SET state=?,reason=?,ended_at=?,result_json=?,result_digest=? WHERE id=?',
                (state, failure, now, encoded, hashlib.sha256(encoded.encode()).hexdigest() if encoded else None, attempt_id))
            db.execute('UPDATE compute_jobs SET state=?,reason=?,updated_at=? WHERE id=?', (state, failure, now, row['job_id']))
            db.execute('DELETE FROM compute_reservations WHERE attempt_id=?', (attempt_id,))
            db.execute('DELETE FROM compute_resource_reservations WHERE attempt_id=?', (attempt_id,))
            if report is not None and isinstance(report.get('inventory_artifact'), dict):
                inventory_digest = self._digest(report['inventory_artifact'].get('sha256'))
                db.execute('INSERT OR IGNORE INTO compute_remote_acks VALUES(?,?,NULL)', (attempt_id, inventory_digest))
            self._event(db, state, attempt_id, {'reason': failure})

    def reconcile_terminated(self, attempt_id, *, evidence, actor, failure=None, write_not_started=False):
        bounded_text(evidence, 'termination evidence', 4000, True)
        if failure not in (None, 'oom'):
            raise ValueError('unsupported reconciled failure')
        failure = failure or 'verified_terminated'
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_attempts WHERE id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] != 'unknown':
                raise ValueError('only unknown attempts require termination reconciliation')
            job = db.execute('SELECT manifest_json FROM compute_jobs WHERE id=?', (row['job_id'],)).fetchone()
            write_started = (job['manifest_json'] and json.loads(job['manifest_json']).get('production_writes')
                             and row['remote_start_sent_at'] is not None)
            if row['external_write_unknown'] or (write_started and not write_not_started):
                raise ValueError('external write requires separate business outcome reconciliation')
            now = self.clock()
            db.execute("UPDATE compute_attempts SET state='failed',reason=?,ended_at=? WHERE id=?", (failure, now, attempt_id))
            db.execute("UPDATE compute_jobs SET state='failed',reason=?,updated_at=? WHERE id=?", (failure, now, row['job_id']))
            db.execute('DELETE FROM compute_reservations WHERE attempt_id=?', (attempt_id,))
            db.execute('DELETE FROM compute_resource_reservations WHERE attempt_id=?', (attempt_id,))
            self._event(db, 'termination_reconciled', attempt_id, {'evidence': evidence}, actor)
        return self.inspect(row['job_id'])

    def retry(self, job_id, *, actor):
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['state'] != 'failed' or row['reason'] == 'oom':
                raise ValueError('retry requires confirmed termination without an unchanged OOM budget')
            if db.execute('SELECT 1 FROM compute_attempts WHERE job_id=? AND external_write_unknown=1', (job_id,)).fetchone():
                raise ValueError('external outcome requires a new explicitly reconciled request')
            if row['manifest_json'] and json.loads(row['manifest_json']).get('production_writes'):
                raise ValueError('production write jobs require a newly reconciled request; never replay')
            if row['generation'] >= 3:
                raise ValueError('compute retry limit reached')
            delay = 300 if row['generation'] == 1 else 1800
            if self.clock() < row['updated_at'] + delay:
                raise ValueError('compute retry backoff has not elapsed')
            db.execute("UPDATE compute_jobs SET state='queued',reason=NULL,updated_at=? WHERE id=?", (self.clock(), job_id))
            self._event(db, 'retry_queued', job_id, {'previous_generation': row['generation']}, actor)
        return self.inspect(job_id)

    def cancel(self, job_id, *, actor):
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT state FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['state'] not in ('queued', 'blocked'):
                raise ValueError('only unstarted compute work can be cancelled')
            db.execute("UPDATE compute_jobs SET state='cancelled',reason='owner_cancelled',updated_at=? WHERE id=?", (self.clock(), job_id))
            self._event(db, 'cancelled', job_id, {}, actor)
        return self.inspect(job_id)


    def active_attempts(self, physical_host):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM compute_attempts WHERE physical_host=? AND state IN ('starting','running','unknown')",
                (physical_host,))]

    def mark_unknown(self, attempt_id, *, reason):
        # Fixed reason codes only: no remote stderr or credentials in receipts.
        if reason not in ('remote_state_uncertain', 'remote_protocol_invalid', 'remote_runtime_budget'):
            raise ValueError('unsupported unknown reason')
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_attempts WHERE id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] not in ACTIVE:
                raise ValueError('attempt is not active')
            if row['external_write_unknown']:
                return  # Protocol or lease errors must never erase the business fence.
            db.execute("UPDATE compute_attempts SET state='unknown',reason=? WHERE id=?", (reason, attempt_id))
            db.execute("UPDATE compute_jobs SET state='unknown',reason='reconciliation_required',updated_at=? WHERE id=?",
                       (self.clock(), row['job_id']))
            self._event(db, 'unknown', attempt_id, {'reason': reason})


    def mark_external_unknown(self, attempt_id, generation, *, terminated=False, report=None):
        encoded = canonical(report) if report is not None else None
        if encoded is not None:
            bounded_text(encoded, 'external evidence', 8192, True)
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_attempts WHERE id=? AND generation=?',
                             (attempt_id, generation)).fetchone()
            if row is None or row['state'] not in ACTIVE:
                raise ValueError('external evidence requires the unresolved attempt')
            if row['result_json'] and encoded is not None and row['result_json'] != encoded:
                raise ValueError('external evidence cannot change immutable result')
            db.execute("""UPDATE compute_attempts SET state='unknown',reason='external_write_unknown',
                external_write_unknown=1,termination_confirmed_at=COALESCE(termination_confirmed_at,?),
                result_json=COALESCE(result_json,?),result_digest=COALESCE(result_digest,?) WHERE id=?""",
                (self.clock() if terminated else None, encoded,
                 hashlib.sha256(encoded.encode()).hexdigest() if encoded else None, attempt_id))
            db.execute("UPDATE compute_jobs SET state='unknown',reason='external_write_unknown',updated_at=? WHERE id=?",
                       (self.clock(), row['job_id']))
            if not row['external_write_unknown'] or (encoded and not row['result_json']):
                self._event(db, 'external_write_unknown', attempt_id,
                            {'termination_confirmed': terminated, 'evidence_archived': encoded is not None})
            # No reservation deletion and no worker ack/GC outbox entry.

    def reconcile_external(self, attempt_id, generation, *, outcome, evidence, actor):
        bounded_text(evidence, 'external readback evidence', 4000, True)
        if outcome not in ('applied', 'not_applied'):
            raise ValueError('external outcome must be established by readback')
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_attempts WHERE id=? AND generation=?',
                             (attempt_id, generation)).fetchone()
            if (row is None or row['state'] != 'unknown' or not row['external_write_unknown']
                    or row['termination_confirmed_at'] is None):
                raise ValueError('external reconciliation requires confirmed process termination')
            reason = 'external_write_verified_' + outcome
            db.execute("UPDATE compute_attempts SET state='failed',reason=?,ended_at=? WHERE id=?",
                       (reason, self.clock(), attempt_id))
            db.execute("UPDATE compute_jobs SET state='failed',reason=?,updated_at=? WHERE id=?",
                       (reason, self.clock(), row['job_id']))
            db.execute('DELETE FROM compute_reservations WHERE attempt_id=?', (attempt_id,))
            db.execute('DELETE FROM compute_resource_reservations WHERE attempt_id=?', (attempt_id,))
            self._event(db, 'external_outcome_reconciled', attempt_id,
                        {'outcome': outcome, 'evidence': evidence}, actor)
        return self.inspect(row['job_id'])


    def claim_remote_start(self, attempt_id, generation):
        with self.store.connect(write=True) as db:
            self._current(db, attempt_id, generation)
            changed = db.execute('UPDATE compute_attempts SET remote_start_sent_at=? WHERE id=? AND remote_start_sent_at IS NULL',
                                 (self.clock(), attempt_id)).rowcount
            return changed == 1


    def pending_remote_acks(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT a.*,k.inventory_digest FROM compute_remote_acks k JOIN compute_attempts a ON a.id=k.attempt_id WHERE k.acknowledged_at IS NULL LIMIT 10')]

    def confirm_remote_ack(self, attempt_id):
        with self.store.connect(write=True) as db:
            db.execute('UPDATE compute_remote_acks SET acknowledged_at=? WHERE attempt_id=? AND acknowledged_at IS NULL',
                       (self.clock(), attempt_id))
