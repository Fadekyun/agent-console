"""Central publication fence: a lost commit acknowledgement is never replayable."""
import hashlib
import secrets

from .workflow_store import bounded_text, identifier

PUBLICATION_SECONDS = 600


class ComputePublications:
    def __init__(self, queue):
        self.queue = queue
        with queue.store.connect(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS compute_publications(
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES compute_jobs(id),
                target TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL,
                result_digest TEXT NOT NULL, actor TEXT NOT NULL, state TEXT NOT NULL,
                token_hash TEXT NOT NULL, lease_until REAL NOT NULL,
                created_at REAL NOT NULL, ended_at REAL, evidence TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS compute_publication_target ON compute_publications(target,state)')

    def _expire(self, db):
        db.execute("UPDATE compute_publications SET state='unknown' WHERE state='publishing' AND lease_until<=?",
                   (self.queue.clock(),))

    @staticmethod
    def _public(row):
        value = dict(row)
        value.pop('token_hash', None)
        return value

    def begin(self, job_id, *, target, request_key, actor):
        bounded_text(target, 'publication target', 100, True)
        bounded_text(request_key, 'publication key', 100, True)
        with self.queue.store.connect(write=True) as db:
            self._expire(db)
            job = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
            if job is None or job['actor'] != actor or job['state'] != 'completed':
                raise ValueError('publication requires an owned completed job')
            attempt = db.execute('SELECT result_digest FROM compute_attempts WHERE job_id=? AND generation=?',
                                 (job_id, job['generation'])).fetchone()
            if not attempt or not attempt['result_digest']:
                raise ValueError('publication requires an immutable result receipt')
            prior = db.execute('SELECT * FROM compute_publications WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                if (prior['job_id'], prior['target'], prior['actor'], prior['result_digest']) != (job_id, target, actor, attempt['result_digest']):
                    raise ValueError('publication key is bound to different content')
                return {**self._public(prior), 'may_publish': False}
            unresolved = db.execute("SELECT id FROM compute_publications WHERE target=? AND state IN ('publishing','unknown')", (target,)).fetchone()
            if unresolved:
                raise ValueError('target publication requires reconciliation')
            # The same completed result can never be published twice under a new
            # idempotency key, including after an unknown commit outcome.
            if db.execute('SELECT id FROM compute_publications WHERE job_id=? AND target=?', (job_id, target)).fetchone():
                raise ValueError('result already has a publication receipt')
            now, receipt_id, token = self.queue.clock(), identifier('compute-publication'), secrets.token_urlsafe(32)
            db.execute('INSERT INTO compute_publications VALUES(?,?,?,?,?,?,\'publishing\',?,?,?,NULL,NULL)',
                       (receipt_id, job_id, target, request_key, attempt['result_digest'], actor,
                        hashlib.sha256(token.encode()).hexdigest(), now + PUBLICATION_SECONDS, now))
            self.queue._event(db, 'publication_claimed', receipt_id, {'job_id': job_id, 'target': target}, actor)
            row = db.execute('SELECT * FROM compute_publications WHERE id=?', (receipt_id,)).fetchone()
            return {**self._public(row), 'may_publish': True, 'receipt_token': token}

    def finish(self, receipt_id, *, token, evidence, actor):
        bounded_text(evidence, 'publication evidence', 4000, True)
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise ValueError('invalid publication capability')
        with self.queue.store.connect(write=True) as db:
            self._expire(db)
            row = db.execute('SELECT * FROM compute_publications WHERE id=?', (receipt_id,)).fetchone()
            if (row is None or row['actor'] != actor or row['state'] != 'publishing'
                    or not secrets.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())):
                raise ValueError('publication receipt cannot be completed without reconciliation')
            db.execute("UPDATE compute_publications SET state='completed',ended_at=?,evidence=? WHERE id=?",
                       (self.queue.clock(), evidence, receipt_id))
            self.queue._event(db, 'publication_completed', receipt_id, {'job_id': row['job_id']}, actor)
            return self._public(db.execute('SELECT * FROM compute_publications WHERE id=?', (receipt_id,)).fetchone())

    def inspect(self, receipt_id, *, actor):
        with self.queue.store.connect(write=True) as db:
            self._expire(db)
            row = db.execute('SELECT * FROM compute_publications WHERE id=? AND actor=?', (receipt_id, actor)).fetchone()
            if row is None:
                raise ValueError('unknown publication receipt')
            return self._public(row)


    def status(self):
        with self.queue.store.connect(write=True) as db:
            self._expire(db)
            return [self._public(row) for row in db.execute(
                'SELECT * FROM compute_publications ORDER BY created_at DESC LIMIT 100')]

    def reconcile(self, receipt_id, *, outcome, evidence, actor):
        if outcome not in ('completed', 'abandoned'):
            raise ValueError('invalid publication reconciliation outcome')
        bounded_text(evidence, 'external publication evidence', 4000, True)
        with self.queue.store.connect(write=True) as db:
            self._expire(db)
            row = db.execute('SELECT * FROM compute_publications WHERE id=?', (receipt_id,)).fetchone()
            if row is None or row['state'] != 'unknown':
                raise ValueError('only an unknown publication requires reconciliation')
            db.execute('UPDATE compute_publications SET state=?,ended_at=?,evidence=? WHERE id=?',
                       (outcome, self.queue.clock(), evidence, receipt_id))
            self.queue._event(db, 'publication_reconciled', receipt_id,
                              {'outcome': outcome, 'evidence': evidence}, actor)
            return self._public(db.execute('SELECT * FROM compute_publications WHERE id=?', (receipt_id,)).fetchone())
