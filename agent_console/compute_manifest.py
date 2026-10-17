"""Immutable, bounded envelopes. No job-supplied commands, paths or credentials."""
import hashlib
import re

from .workflow_store import canonical

MAX_MANIFEST_BYTES = 65536
MAX_ARTIFACT_BYTES = 2 * 1024 ** 3


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('invalid SHA-256 digest')
    return value


def artifact(value):
    if not isinstance(value, dict) or set(value) != {'sha256', 'size'}:
        raise ValueError('invalid artifact descriptor')
    digest(value['sha256'])
    size = value['size']
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_ARTIFACT_BYTES:
        raise ValueError('artifact size is outside the bounded transfer limit')
    return value


def validate_manifest(value, *, kind, source_digest, input_digest):
    if not isinstance(value, dict) or not {
            'version', 'kind', 'source_sha', 'source_artifact', 'input_sha256',
            'parameters', 'production_writes'} <= set(value):
        raise ValueError('incomplete job manifest')
    if set(value) - {'version', 'kind', 'source_sha', 'source_artifact', 'inputs_artifact',
                     'input_sha256', 'parameters', 'production_writes', 'business_key', 'emergency'}:
        raise ValueError('unsupported manifest field')
    if type(value['version']) is not int or value['version'] != 1 or value['kind'] != kind:
        raise ValueError('manifest version or kind mismatch')
    if not isinstance(value['source_sha'], str) or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', value['source_sha']):
        raise ValueError('source must be an exact commit')
    if artifact(value['source_artifact'])['sha256'] != source_digest:
        raise ValueError('source artifact digest mismatch')
    if digest(value['input_sha256']) != input_digest:
        raise ValueError('input digest mismatch')
    if 'inputs_artifact' in value and artifact(value['inputs_artifact'])['sha256'] != input_digest:
        raise ValueError('input artifact digest mismatch')
    if not isinstance(value['parameters'], dict):
        raise ValueError('parameters must be an adapter-specific object')
    if type(value['production_writes']) is not bool:
        raise ValueError('explicit production write policy required')
    if value['production_writes'] and kind not in ('bushi.run', 'bushi.emergency'):
        raise ValueError('this adapter cannot perform production writes')
    business_key(value)
    if 'emergency' in value and kind != 'bushi.emergency':
        raise ValueError('emergency authorization requires the emergency profile')
    import json
    json.dumps(value, allow_nan=False)
    encoded = canonical(value)
    if len(encoded.encode()) > MAX_MANIFEST_BYTES:
        raise ValueError('manifest exceeds limit')
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def business_key(manifest):
    """Only the reviewed paid-order identity can claim this global namespace."""
    supplied = manifest.get('business_key')
    parameters = manifest.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError('parameters must be an adapter-specific object')
    expected = None
    if (manifest.get('kind') in ('bushi.run', 'bushi.emergency')
            and manifest.get('production_writes') is True and parameters.get('action') == 'participate'):
        order = parameters.get('orderId')
        if not isinstance(order, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', order):
            raise ValueError('paid participation requires a canonical order UUID')
        expected = 'agc:paid-participation:' + order
    if supplied is not None and (expected is None or supplied != expected):
        raise ValueError('business key does not match the approved action identity')
    return expected


def validate_emergency(manifest, *, priority, deadline, now):
    emergency = manifest.get('emergency') if isinstance(manifest, dict) else None
    if (not isinstance(emergency, dict) or set(emergency) != {'urgent', 'deadline'}
            or emergency.get('urgent') is not True or type(emergency.get('deadline')) is not int
            or deadline != emergency['deadline'] or priority < 90
            or not business_key(manifest) or 'inputs_artifact' in manifest):
        raise ValueError('emergency requires explicit urgent paid action and bound deadline')
    if not now < deadline <= now + 1200:
        raise ValueError('emergency deadline must be within the next twenty minutes')
