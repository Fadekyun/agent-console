"""Digest-bound cleanup proposals.

The guard decides what is eligible; this module is how a human authorises a
specific decision. A proposal is a canonical manifest of the exact actions the
guard would take, plus a digest over the fields that matter (host, state root,
policy version, tier, every path with its kind, tier and size).

Approval happens outside this module (Discord), and execution accepts only the
proposal identified by that digest:

* the manifest is re-hashed, so an edited file cannot be approved-by-hash;
* it expires, so a stale approval cannot be replayed weeks later;
* execution re-plans and refuses on drift, so anything that became eligible or
  ineligible after approval requires a fresh approval;
* a digest can be claimed once, so a double-click or a retried webhook cannot
  run the same deletion twice.

Models may summarise or critique a proposal; they never create one, add paths to
one, or approve one.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path

from . import storage_guard
from .storage import free_bytes, level_for

DAY = 86400
VERSION = 1
PROPOSAL_TTL_SECONDS = 7 * DAY


class ProposalError(RuntimeError):
    """Base class for refused proposals."""


class ProposalInvalid(ProposalError):
    pass


class ProposalExpired(ProposalError):
    pass


class ProposalDrift(ProposalError):
    pass


class ProposalReplayed(ProposalError):
    pass


def policy() -> dict[str, object]:
    """The retention policy that produced the proposal.

    Changing any threshold changes this mapping, which changes every digest, so
    a proposal generated under one policy cannot be executed under another.
    """
    return {
        'version': VERSION,
        'releases_keep': storage_guard.RELEASES_KEEP,
        'assessment_keep': storage_guard.ASSESSMENT_KEEP,
        'backup_max_age_days': storage_guard.BACKUP_MAX_AGE_DAYS,
        'context_max_age_days': storage_guard.CONTEXT_MAX_AGE_DAYS,
        'log_max_age_days': storage_guard.LOG_MAX_AGE_DAYS,
        'overlay_max_age_days': storage_guard.OVERLAY_MAX_AGE_DAYS,
        'provider_max_age_days': storage_guard.PROVIDER_MAX_AGE_DAYS,
        'tmp_max_age_days': storage_guard.TMP_MAX_AGE_DAYS,
        'protect_seconds': storage_guard.PROTECT_SECONDS,
    }


def canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _bound(proposal: dict) -> dict:
    """The digest covers everything a human is being asked to approve."""
    return {
        'version': proposal.get('version'),
        'host': proposal.get('host'),
        'state_root': proposal.get('state_root'),
        'tmp_dir': proposal.get('tmp_dir'),
        'tier2': bool(proposal.get('tier2')),
        'policy': proposal.get('policy'),
        'actions': [
            {'path': action['path'], 'kind': action['kind'],
             'tier': action['tier'], 'bytes': action['bytes']}
            for action in proposal.get('actions', [])
        ],
    }


def compute_digest(proposal: dict) -> str:
    return hashlib.sha256(canonical(_bound(proposal)).encode()).hexdigest()


def _action_key(action: dict) -> tuple[str, str, int, int]:
    return (str(Path(action['path']).resolve()), action['kind'], int(action['tier']),
            int(action['bytes']))


def build(state_dir: str | Path, *, tmp_dir: str | Path | None = '/tmp',
          tier2: bool = False, now: float | None = None,
          host: str | None = None) -> dict:
    """Plan without applying anything and return a digest-bound proposal."""
    root = Path(state_dir)
    now = time.time() if now is None else now
    report = storage_guard.run(root, tmp_dir=tmp_dir, tier2=tier2, now=now,
                              apply_actions=False)
    proposal = {
        'version': VERSION,
        'generated_at': int(now),
        'expires_at': int(now) + PROPOSAL_TTL_SECONDS,
        'host': host or socket.gethostname(),
        'state_root': str(root.resolve()),
        'tmp_dir': str(tmp_dir) if tmp_dir else None,
        'tier2': bool(tier2),
        'level': report.level,
        'free_gib': round(report.free_bytes / (1024 * 1024 * 1024), 2),
        'policy': policy(),
        'actions': [action.as_dict() for action in report.actions],
        'warnings': list(report.errors),
    }
    proposal['planned_bytes'] = sum(int(a['bytes']) for a in proposal['actions'])
    proposal['digest'] = compute_digest(proposal)
    return proposal


def directory(state_dir: str | Path) -> Path:
    return Path(state_dir) / 'storage-proposals'


def write(state_dir: str | Path, proposal: dict) -> Path:
    root = directory(state_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = proposal['digest']
    if compute_digest(proposal) != digest:
        raise ProposalInvalid('digest does not match proposal content')
    target = root / f'{digest}.json'
    temporary = root / f'.{digest}.{os.getpid()}.tmp'
    temporary.write_text(json.dumps(proposal, indent=1, sort_keys=True), encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


def load(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ProposalInvalid('proposal must be an object')
    if payload.get('version') != VERSION:
        raise ProposalInvalid(f"unsupported proposal version: {payload.get('version')}")
    for key in ('digest', 'host', 'state_root', 'policy', 'actions', 'expires_at'):
        if key not in payload:
            raise ProposalInvalid(f'proposal is missing {key}')
    if compute_digest(payload) != payload['digest']:
        raise ProposalInvalid('digest does not match proposal content')
    return payload


def resolve(state_dir: str | Path, reference: str) -> dict:
    """Accept a digest or a path to a stored proposal."""
    candidate = Path(reference)
    if candidate.is_file():
        return load(candidate)
    stored = directory(state_dir) / f'{reference}.json'
    if not stored.is_file():
        raise ProposalInvalid(f'no proposal for {reference}')
    return load(stored)


def verify(state_dir: str | Path, proposal: dict, *, now: float | None = None) -> dict:
    """Re-check digest, expiry and drift; return the proposal when still valid."""
    now = time.time() if now is None else now
    if compute_digest(proposal) != proposal.get('digest'):
        raise ProposalInvalid('digest does not match proposal content')
    if int(proposal['expires_at']) <= int(now):
        raise ProposalExpired('proposal expired; generate a new one')
    if policy() != proposal['policy']:
        raise ProposalInvalid('retention policy changed since this proposal was made')
    root = Path(proposal['state_root'])
    if str(root.resolve()) != str(Path(state_dir).resolve()):
        raise ProposalInvalid('proposal belongs to a different state root')
    fresh = build(root, tmp_dir=proposal.get('tmp_dir'), tier2=bool(proposal.get('tier2')),
                  now=now, host=proposal['host'])
    approved = {_action_key(action) for action in proposal['actions']}
    current = {_action_key(action) for action in fresh['actions']}
    if approved != current:
        added = sorted(path for path, *_ in current - approved)[:5]
        removed = sorted(path for path, *_ in approved - current)[:5]
        raise ProposalDrift(
            f'state changed since approval: {len(current - approved)} new, '
            f'{len(approved - current)} withdrawn; new={added} withdrawn={removed}')
    return proposal


def claim(state_dir: str | Path, digest: str) -> Path:
    """Mark a digest as executed exactly once."""
    root = directory(state_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / f'{digest}.claimed'
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ProposalReplayed('this proposal was already executed') from None
    with os.fdopen(handle, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps({'claimed_at': int(time.time())}) + '\n')
    return path


def execute(state_dir: str | Path, reference: str, *, now: float | None = None) -> storage_guard.Report:
    """Verify, claim once and apply exactly the approved actions."""
    proposal = resolve(state_dir, reference)
    # Check the claim before re-planning: a digest that already ran must report a
    # replay rather than a confusing drift error caused by its own deletions.
    if (directory(state_dir) / f"{proposal['digest']}.claimed").exists():
        raise ProposalReplayed('this proposal was already executed')
    proposal = verify(state_dir, proposal, now=now)
    claim(state_dir, proposal['digest'])
    actions = [
        storage_guard.Action(Path(action['path']), int(action['tier']),
                             action['reason'], int(action['bytes']), action['kind'])
        for action in proposal['actions']
    ]
    root = Path(state_dir)
    free = free_bytes(root)
    report = storage_guard.Report(level=level_for(free), free_bytes=free)
    storage_guard.apply(actions, report, state_dir=root)
    report.free_bytes = free_bytes(root)
    report.level = level_for(report.free_bytes)
    return report
