"""Delete only artifacts explicitly consumed after completed publication.

Unknown/unpublished jobs, recent uploads, and shared source objects stay pinned.
No age-only cleanup touches worker receipts or production rollback artifacts.
"""
import fcntl
import json
import os

from .compute_manifest import digest
from .workflow_store import bounded_text, canonical


class ComputeRetention:
    def __init__(self, engine):
        self.engine = engine
        self.queue = engine.queue
        with self.queue.store.connect(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS compute_artifact_pins(
                digest TEXT NOT NULL, actor TEXT NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY(digest,actor))''')
            db.execute('''CREATE TABLE IF NOT EXISTS compute_consumed(
                job_id TEXT PRIMARY KEY REFERENCES compute_jobs(id), actor TEXT NOT NULL,
                evidence TEXT NOT NULL, artifacts_json TEXT NOT NULL, created_at REAL NOT NULL)''')

    def pin_upload(self, artifact_id, actor):
        digest(artifact_id)
        with self.queue.store.connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO compute_artifact_pins VALUES(?,?,?)',
                       (artifact_id, actor, self.queue.clock() + 48 * 3600))

    def _artifacts(self, job, attempts):
        ids = set()
        if job['manifest_json']:
            manifest = json.loads(job['manifest_json'])
            for key in ('source_artifact', 'inputs_artifact'):
                if key in manifest:
                    ids.add(digest(manifest[key]['sha256']))
        for attempt in attempts:
            if attempt['result_json']:
                result = json.loads(attempt['result_json'])
                if 'artifacts' in result:
                    ids.update(self.engine.result_artifact_ids(result))
        return ids

    def consume(self, job_id, *, actor, evidence):
        bounded_text(evidence, 'central retention evidence', 4000, True)
        with self.queue.store.connect(write=True) as db:
            job = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if job is None or job['actor'] != actor or job['state'] != 'completed':
                raise ValueError('only an owned completed result can be consumed')
            publications = db.execute('SELECT state FROM compute_publications WHERE job_id=?', (job_id,)).fetchall()
            if not publications or any(row['state'] != 'completed' for row in publications):
                raise ValueError('publication must be confirmed before artifact retirement')
            attempts = db.execute('SELECT * FROM compute_attempts WHERE job_id=?', (job_id,)).fetchall()
            for attempt in attempts:
                db.execute('DELETE FROM compute_artifact_pins WHERE actor=?', ('attempt:' + attempt['id'],))
            prior = db.execute('SELECT job_id FROM compute_consumed WHERE job_id=?', (job_id,)).fetchone()
            if not prior:
                attempts = db.execute('SELECT * FROM compute_attempts WHERE job_id=?', (job_id,)).fetchall()
                ids = self._artifacts(job, attempts)
                db.execute('INSERT INTO compute_consumed VALUES(?,?,?,?,?)',
                           (job_id, actor, evidence, canonical(sorted(ids)), self.queue.clock()))
                self.queue._event(db, 'artifact_consumed', job_id, {'evidence': evidence}, actor)
        return self.collect()

    def collect(self):
        removed, freed = 0, 0
        # Hold the queue write lock while selecting and unlinking; a new enqueue
        # cannot establish a reference halfway through retirement. Upload pins
        # are installed before transfer and protect enqueue's input validation.
        with self.queue.store.connect(write=True) as db:
            acknowledged = {r['job_id']: json.loads(r['artifacts_json'])
                            for r in db.execute('SELECT * FROM compute_consumed')}
            candidates = {digest(value) for values in acknowledged.values() for value in values}
            protected = {r['digest'] for r in db.execute(
                'SELECT digest FROM compute_artifact_pins WHERE expires_at>?', (self.queue.clock(),))}
            jobs = db.execute('SELECT * FROM compute_jobs LIMIT 10001').fetchall()
            if len(jobs) > 10000:
                raise ValueError('retention requires a bounded operator review')
            for job in jobs:
                if job['id'] not in acknowledged:
                    protected.update(self._artifacts(job, db.execute(
                        'SELECT * FROM compute_attempts WHERE job_id=?', (job['id'],)).fetchall()))
            store = self.engine.artifacts
            with os.fdopen(store._open('.lock', os.O_CREAT | os.O_RDWR), 'r+b') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for artifact_id in sorted(candidates - protected):
                    size = store._size(artifact_id)
                    try:
                        os.unlink(store.root / artifact_id)
                        removed += 1
                        freed += size
                    except FileNotFoundError:
                        pass
                if removed:
                    self.queue._event(db, 'artifacts_retired', 'central-store',
                                      {'objects': removed, 'bytes': freed}, 'compute-retention')
        return {'retired_objects': removed, 'retired_bytes': freed}
