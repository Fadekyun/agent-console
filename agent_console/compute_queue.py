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
            if row and row[0] != 1:
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

    def enqueue(self, *, kind, execution_target, source_digest, input_digest, request_key, actor):
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
        spec_hash = hashlib.sha256(canonical(spec).encode()).hexdigest()
        with self.store.connect(write=True) as db:
            prior = db.execute('SELECT * FROM compute_jobs WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                if prior['spec_hash'] != spec_hash:
                    raise ValueError('request key already identifies a different immutable job')
                return dict(prior)
            job_id, now = identifier('compute'), self.clock()
            db.execute('''INSERT INTO compute_jobs(id,kind,execution_target,source_digest,input_digest,
                request_key,spec_hash,state,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?,?)''',
                (job_id, kind, execution_target, source_digest, input_digest, request_key, spec_hash, actor, now, now))
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
            for job in db.execute("SELECT * FROM compute_jobs WHERE state IN ('queued','blocked') ORDER BY (kind='maintenance.report') DESC,created_at,id LIMIT 100").fetchall():
                profile = PROFILES[job['kind']]
                reason = admission(profile, telemetry, reservations, now)
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
            self._event(db, state, attempt_id, {'reason': failure})

    def reconcile_terminated(self, attempt_id, *, evidence, actor):
        bounded_text(evidence, 'termination evidence', 4000, True)
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_attempts WHERE id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] != 'unknown':
                raise ValueError('only unknown attempts require termination reconciliation')
            now = self.clock()
            db.execute("UPDATE compute_attempts SET state='failed',reason='verified_terminated',ended_at=? WHERE id=?", (now, attempt_id))
            db.execute("UPDATE compute_jobs SET state='failed',reason='verified_terminated',updated_at=? WHERE id=?", (now, row['job_id']))
            db.execute('DELETE FROM compute_reservations WHERE attempt_id=?', (attempt_id,))
            self._event(db, 'termination_reconciled', attempt_id, {'evidence': evidence}, actor)
        return self.inspect(row['job_id'])

    def retry(self, job_id, *, actor):
        with self.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['state'] != 'failed' or row['reason'] == 'oom':
                raise ValueError('retry requires confirmed termination without an unchanged OOM budget')
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
