"""One owner-directed routing transition, before any attempt exists.

The producer's original request hash remains immutable for lost-ack retries.
Both complete specs and the original manifest survive in the append-only receipt.
No process, lease, unknown outcome or business claim is reset by this operation.
"""
import hashlib
import json
from itertools import product

from .compute_manifest import business_key, digest, validate_emergency, validate_manifest
from .workflow_store import bounded_text, canonical, identifier


def spec(job, *, manifest_digest=None, kind=None, target=None, priority=None, deadline=None):
    return dict(kind=kind or job['kind'], execution_target=target or job['execution_target'],
        source_digest=job['source_digest'], input_digest=job['input_digest'],
        manifest_digest=manifest_digest or job['manifest_digest'],
        dependencies=json.loads(job['dependencies_json']),
        priority=job['priority'] if priority is None else priority,
        not_before=job['not_before'], deadline=job['deadline'] if deadline is None else deadline)


def original_spec(job):
    value = spec(job)
    # SQLite REAL columns normalize integer request timestamps to floats.
    # Recover the original JSON representation by its immutable hash, rather
    # than weakening binding checks for otherwise equivalent numeric values.
    alternatives = []
    for key in ('not_before', 'deadline'):
        number = value[key]
        alternatives.append((number, int(number)) if isinstance(number, float) and number.is_integer() else (number,))
    for not_before, deadline in product(*alternatives):
        candidate = {**value, 'not_before': not_before, 'deadline': deadline}
        if hashlib.sha256(canonical(candidate).encode()).hexdigest() == job['spec_hash']:
            return candidate
    raise ValueError('original request binding mismatch')


def transfer_emergency(queue, job_id, *, request_key, expected_manifest_sha256, deadline, reason, actor):
    bounded_text(request_key, 'transfer request key', 100, True)
    bounded_text(reason, 'transfer reason', 1000, True)
    bounded_text(actor, 'operator', 200, True)
    digest(expected_manifest_sha256)
    if actor.startswith('producer:'):
        raise PermissionError('emergency transfer requires operator authority')
    if type(deadline) is not int:
        raise ValueError('integer execution deadline required')
    request_hash = hashlib.sha256(canonical(dict(job_id=job_id, request_key=request_key,
        expected_manifest_sha256=expected_manifest_sha256, deadline=deadline,
        reason=reason, actor=actor)).encode()).hexdigest()
    with queue.store.connect(write=True) as db:
        prior = db.execute('SELECT * FROM compute_transfers WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['request_hash'] != request_hash:
                raise ValueError('transfer key already identifies a different request')
            return json.loads(prior['receipt_json'])
        job = db.execute('SELECT * FROM compute_jobs WHERE id=?', (job_id,)).fetchone()
        if (job is None or job['kind'] != 'bushi.run' or job['execution_target'] != 'agc-laptop'
                or job['state'] not in ('queued', 'blocked') or job['generation'] != 0
                or job['manifest_digest'] != expected_manifest_sha256
                or not job['manifest_json']):
            raise ValueError('only an unchanged unstarted laptop Bushi job can transfer')
        if db.execute('SELECT 1 FROM compute_attempts WHERE job_id=?', (job_id,)).fetchone():
            raise ValueError('any prior attempt prevents transfer')
        if db.execute('SELECT 1 FROM compute_transfers WHERE job_id=?', (job_id,)).fetchone():
            raise ValueError('job already has an immutable transfer')
        # An attempt owns every physical/named reservation and remote result.
        # Absence of ALL attempt rows is stricter than absence of active leases.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='compute_publications'").fetchone():
            if db.execute('SELECT 1 FROM compute_publications WHERE job_id=?', (job_id,)).fetchone():
                raise ValueError('publication evidence prevents transfer')
        if ((job['deadline'] is not None and deadline > job['deadline'])
                or (job['not_before'] is not None and job['not_before'] >= deadline)):
            raise ValueError('transfer may not extend or bypass the original admission window')
        manifest = json.loads(job['manifest_json'])
        if hashlib.sha256(canonical(manifest).encode()).hexdigest() != expected_manifest_sha256:
            raise ValueError('original manifest binding mismatch')
        before_spec = original_spec(job)
        manifest = {**manifest, 'kind': 'bushi.emergency',
                    'emergency': {'urgent': True, 'deadline': deadline}}
        priority = max(90, job['priority'])
        now = queue.clock()
        validate_emergency(manifest, priority=priority, deadline=deadline, now=now)
        claim = business_key(manifest)
        if claim:
            row = db.execute('SELECT job_id FROM compute_business_claims WHERE business_key=?', (claim,)).fetchone()
            if row is None or row['job_id'] != job_id:
                raise ValueError('original business claim is missing or conflicts')
        encoded, manifest_digest = validate_manifest(manifest, kind='bushi.emergency',
            source_digest=job['source_digest'], input_digest=job['input_digest'])
        effective_spec = spec(job, manifest_digest=manifest_digest, kind='bushi.emergency',
                              target='n100', priority=priority, deadline=deadline)
        receipt = dict(version=1, id=identifier('compute-transfer'), request_key=request_key,
            job_id=job_id, original_kind=job['kind'], effective_kind='bushi.emergency',
            from_execution_target=job['execution_target'], to_execution_target='n100',
            from_manifest_sha256=expected_manifest_sha256, to_manifest_sha256=manifest_digest,
            original_deadline=job['deadline'], deadline=deadline, reason=reason, at=now, operator=actor)
        db.execute('INSERT INTO compute_transfers VALUES(?,?,?,?,?,?,?,?)',
            (receipt['id'], job_id, request_key, request_hash, canonical(before_spec),
             canonical(effective_spec), job['manifest_json'], canonical(receipt)))
        db.execute('''UPDATE compute_jobs SET kind='bushi.emergency',execution_target='n100',
            manifest_json=?,manifest_digest=?,priority=?,deadline=?,state='queued',reason=NULL,
            updated_at=? WHERE id=?''', (encoded, manifest_digest, priority, deadline, now, job_id))
        queue._event(db, 'transferred_emergency', job_id, receipt, actor)
        return receipt
