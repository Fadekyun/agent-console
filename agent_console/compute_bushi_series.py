"""Bind approved series writes to immutable requests and accepted dry-run receipts.

This module does not grant application policy or run browsers. Service producer
scope and the fixed adapter remain separate authorities. Parsing is deliberately
small: one request.json in a bounded tar, never filesystem extraction.
"""
import base64
import hashlib
import io
import json
import re
import tarfile
import zlib

from .compute_manifest import artifact, digest
from .compute_transport import binding
from .workflow_store import canonical

ACTIONS = frozenset({'apply_series', 'apply_manual', 'modify_criteria'})
REQUEST_BYTES = 128 * 1024
ARCHIVE_BYTES = 256 * 1024


def action(manifest):
    parameters = manifest.get('parameters')
    return (parameters.get('action') if manifest.get('kind') == 'bushi.run'
            and isinstance(parameters, dict) and parameters.get('action') in ACTIONS else None)


def namespace(manifest):
    selected = action(manifest)
    if selected and manifest.get('production_writes') is True:
        return 'agc:bushi-series-modify' if selected == 'modify_criteria' else 'agc:bushi-series-apply'
    return None


def validate_envelope(manifest):
    selected = action(manifest)
    if not selected:
        if 'dry_run_job_id' in manifest:
            raise ValueError('dry-run proof is only valid for reviewed series actions')
        return
    parameters = manifest['parameters']
    if (set(parameters) != {'action', 'seriesId', 'mode'}
            or not isinstance(parameters['seriesId'], str)
            or not re.fullmatch('[1-9][0-9]{0,14}', parameters['seriesId'])
            or parameters['mode'] not in ('dry-run', 'execute')
            or manifest.get('production_writes') is not (parameters['mode'] == 'execute')
            or 'inputs_artifact' not in manifest):
        raise ValueError('series action requires an exact immutable request')
    if parameters['mode'] == 'execute':
        job_id = manifest.get('dry_run_job_id')
        if not isinstance(job_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', job_id):
            raise ValueError('series execution requires accepted dry-run identity')
        prefix = namespace(manifest) + ':' + parameters['seriesId']
        supplied = manifest.get('business_key')
        if selected == 'modify_criteria':
            valid = isinstance(supplied, str) and re.fullmatch(re.escape(prefix) + ':[a-f0-9]{64}', supplied)
        else:
            valid = supplied == prefix
        if not valid:
            raise ValueError('series business identity is missing or invalid')
    elif 'dry_run_job_id' in manifest or 'business_key' in manifest:
        raise ValueError('dry-run cannot carry execution authority')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
        # Enforce finite ordinary JSON and bound recursion in all proof paths.
        def check(item, depth=0):
            if depth > 24:
                raise ValueError('JSON nesting exceeds limit')
            if isinstance(item, dict):
                for child in item.values(): check(child, depth+1)
            elif isinstance(item, list):
                for child in item: check(child, depth+1)
        check(value)
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, RecursionError, OverflowError):
        raise ValueError('invalid bounded JSON') from None


def _object(store, description, maximum):
    artifact(description)
    if not 0 < description['size'] <= maximum:
        raise ValueError('series proof artifact exceeds limit')
    data, offset = bytearray(), 0
    while offset < description['size']:
        try:
            chunk = store.get(description['sha256'], offset=offset)
        except OSError:
            raise ValueError('series proof artifact unavailable') from None
        if chunk['size'] != description['size']:
            raise ValueError('series artifact size mismatch')
        content = base64.b64decode(chunk['data'], validate=True)
        if not content or len(content) + offset > description['size']:
            raise ValueError('series artifact is incomplete')
        data.extend(content)
        offset += len(content)
    if hashlib.sha256(data).hexdigest() != description['sha256']:
        raise ValueError('series artifact digest mismatch')
    return bytes(data)


def request_payload(store, manifest):
    raw = _object(store, manifest['inputs_artifact'], ARCHIVE_BYTES)
    if raw.startswith(b'\x1f\x8b'):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            raw = decoder.decompress(raw, ARCHIVE_BYTES + 1)
        except zlib.error:
            raise ValueError('invalid series gzip archive') from None
        if len(raw) > ARCHIVE_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError('series archive expansion exceeds limit')
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            members = archive.getmembers()
            if (len(members) != 1 or members[0].name != 'request.json'
                    or members[0].type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or not 0 < members[0].size <= REQUEST_BYTES or members[0].pax_headers):
                raise ValueError('series input must contain exactly request.json')
            with archive.extractfile(members[0]) as stream:
                payload = _json(stream.read(REQUEST_BYTES + 1))
    except tarfile.TarError:
        raise ValueError('invalid series request archive') from None
    parameters = manifest['parameters']
    if (not isinstance(payload, dict) or set(payload) - {'version','action','series_id','request','dry_run'}
            or type(payload.get('version')) is not int or payload['version'] != 1
            or payload.get('action') != parameters['action'] or payload.get('series_id') != parameters['seriesId']
            or not isinstance(payload.get('request'), dict)):
        raise ValueError('series request identity mismatch')
    if parameters['mode'] == 'dry-run' and 'dry_run' in payload:
        raise ValueError('dry-run input cannot carry an execution proof')
    return payload


def request_digest(payload):
    return hashlib.sha256(canonical({key:payload[key] for key in ('action','series_id','request')}).encode()).hexdigest()


def accepted_claim(db, manifest, actor, store):
    """Called under the queue write transaction, before job/claim insertion."""
    validate_envelope(manifest)
    payload = request_payload(store, manifest)
    if manifest['parameters']['mode'] == 'dry-run':
        return None
    requested = request_digest(payload)
    claim = namespace(manifest) + ':' + payload['series_id']
    if payload['action'] == 'modify_criteria':
        claim += ':' + requested
    if manifest['business_key'] != claim:
        raise ValueError('series business identity differs from immutable request')
    prior = db.execute('SELECT * FROM compute_jobs WHERE id=?', (manifest['dry_run_job_id'],)).fetchone()
    if prior is None or prior['actor'] != actor:
        raise PermissionError('dry-run belongs to another producer or is unavailable')
    if prior['state'] != 'completed' or prior['kind'] != 'bushi.run' or not prior['manifest_json']:
        raise ValueError('dry-run is not durably completed')
    dry = _json(prior['manifest_json'])
    validate_envelope(dry)
    if (dry['parameters'] != {**manifest['parameters'],'mode':'dry-run'}
            or dry['source_sha'] != manifest['source_sha']
            or dry['source_artifact'] != manifest['source_artifact']
            or dry['production_writes'] is not False
            or prior['manifest_digest'] != hashlib.sha256(canonical(dry).encode()).hexdigest()):
        raise ValueError('dry-run source or action differs from execution')
    dry_payload = request_payload(store, dry)
    if request_digest(dry_payload) != requested or dry_payload['request'] != payload['request']:
        raise ValueError('dry-run did not inspect this exact request')
    attempt = db.execute('SELECT * FROM compute_attempts WHERE job_id=? AND generation=?',
                         (prior['id'],prior['generation'])).fetchone()
    if attempt is None or attempt['state'] != 'completed' or not attempt['result_json']:
        raise ValueError('dry-run result is not accepted centrally')
    expected = binding(prior,attempt)
    result = _json(attempt['result_json'])
    if not isinstance(result,dict) or result.get('binding') != expected or not result.get('inventory_artifact'):
        raise ValueError('dry-run output lacks an authenticated inventory')
    inventory = _json(_object(store,result['inventory_artifact'],2*1024**2))
    if (not isinstance(inventory, dict) or inventory.get('version') != 1
            or inventory.get('binding') != expected or not isinstance(inventory.get('files'),list)
            or len(inventory['files']) > 4096):
        raise ValueError('dry-run inventory binding mismatch')
    entries = [entry for entry in inventory['files'] if isinstance(entry,dict) and entry.get('path') == 'response.json']
    if len(entries) != 1:
        raise ValueError('dry-run needs exactly one response.json')
    response = _json(_object(store,{'sha256':entries[0]['sha256'],'size':entries[0]['size']},1024**2))
    if not isinstance(response,dict) or response.get('ok') is not True or response.get('worker_binding') != expected:
        raise ValueError('dry-run response was not successful')
    accepted = response.get('result')
    if (not isinstance(accepted,dict) or accepted.get('action') != payload['action']
            or accepted.get('series_id') != payload['series_id'] or accepted.get('mode') != 'dry-run'
            or accepted.get('ready') is not True or accepted.get('verified') is not False
            or accepted.get('request_sha256') != requested or not isinstance(accepted.get('semantic'),dict)
            or digest(accepted.get('semantic_sha256')) != hashlib.sha256(canonical(accepted.get('semantic')).encode()).hexdigest()):
        raise ValueError('dry-run semantic confirmation is invalid')
    proof = {'job_id':prior['id'],'source_sha':dry['source_sha'],
             'manifest_sha256':prior['manifest_digest'],'input_sha256':prior['input_digest'],
             'request_sha256':requested,'semantic_sha256':accepted['semantic_sha256'],
             'semantic':accepted['semantic'],'ready':True,'action':payload['action'],'series_id':payload['series_id']}
    if canonical(payload.get('dry_run')) != canonical(proof):
        raise ValueError('execution proof differs from accepted dry-run evidence')
    return claim
