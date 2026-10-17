"""Workload-scoped service producers, distinct from owner and managed agents.

Configuration lives in a root-owned host file. Transport must be direct TLS or
loopback (e.g. an authenticated SSH tunnel); forwarded headers are not evidence
of TLS or source identity. No producer can lift holds, retry, or clear unknowns.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from .compute_api import ComputeRequest, ComputeRoute, JobRequest
from .compute_manifest import digest
from .compute_publication import ComputePublications
from .compute_retention import ComputeRetention
from .compute_transport import trusted_json


@dataclass(frozen=True)
class Producer:
    name: str
    policy: dict

    @property
    def actor(self):
        return 'producer:' + self.name


class ProducerRoute(ComputeRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 512 * 1024:
                    raise HTTPException(413, 'Compute request exceeds limit')
            request._body = bytes(data)
            return await handler(request)
        return bounded


class ChunkRequest(ComputeRequest):
    size: int = Field(gt=0, le=2 * 1024 ** 3)
    offset: int = Field(ge=0)
    data: str = Field(max_length=349528)
    final: bool = False


class PublicationRequest(ComputeRequest):
    target: str = Field(min_length=1, max_length=100)
    request_key: str = Field(min_length=1, max_length=100)


class ConsumeRequest(ComputeRequest):
    evidence: str = Field(min_length=1, max_length=4000)


class PublicationFinish(ComputeRequest):
    token: str = Field(min_length=20, max_length=100)
    evidence: str = Field(min_length=1, max_length=4000)


def configured_producers():
    location = os.getenv('AGENT_CONSOLE_COMPUTE_PRODUCERS_CONFIG')
    return trusted_json(location).get('producers', {}) if location else {}


def producer_routes(engine_callable, *, config_loader=configured_producers):
    def authenticate(request: Request):
        if any(name in request.headers for name in ('origin', 'sec-fetch-site',
                'x-agent-console-session', 'x-agent-console-capability')):
            raise HTTPException(403, 'Compute producers require a service identity')
        try:
            address = ipaddress.ip_address(request.client.host)
            if request.url.scheme != 'https' and not address.is_loopback:
                raise ValueError('transport must be encrypted')
            name = request.headers.get('x-compute-producer', '')
            policy = config_loader().get(name)
            if not isinstance(policy, dict) or not policy.get('enabled') is True:
                raise ValueError('unknown producer')
            if not any(address in ipaddress.ip_network(value) for value in policy['source_cidrs']):
                raise ValueError('source denied')
            authorization = request.headers.get('authorization', '')
            if not authorization.startswith('Bearer ') or not 32 <= len(authorization[7:]) <= 128:
                raise ValueError('invalid credential')
            expected = digest(policy['token_sha256'])
            actual = hashlib.sha256(authorization[7:].encode()).hexdigest()
            if not secrets.compare_digest(expected, actual):
                raise ValueError('invalid credential')
            return Producer(name, policy)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(403, 'Compute producer identity is not permitted') from None

    def owned(engine, job_id, producer):
        job = engine.queue.inspect(job_id)
        if job['actor'] != producer.actor:
            raise PermissionError('job belongs to another producer')
        return job

    router = APIRouter(prefix='/api/compute/producers', route_class=ProducerRoute,
                       dependencies=[Depends(authenticate)])

    @router.post('/jobs')
    def enqueue(payload: JobRequest, producer=Depends(authenticate)):
        policy = producer.policy
        if (payload.kind not in policy.get('kinds', [])
                or payload.execution_target != 'agc-laptop'
                or not payload.request_key.startswith(policy.get('request_prefix', producer.name + ':'))
                or payload.priority > policy.get('max_priority', 0)
                or payload.manifest is None):
            raise PermissionError('job is outside producer scope')
        if payload.manifest.get('production_writes') and policy.get('allow_production_writes') is not True:
            raise PermissionError('producer cannot authorize production writes')
        engine = engine_callable()
        for key in ('source_artifact', 'inputs_artifact'):
            if key in payload.manifest and not engine.artifacts.has(payload.manifest[key]):
                raise ValueError('immutable input artifact must be uploaded first')
        return engine.queue.enqueue(**payload.model_dump(exclude_unset=True), actor=producer.actor)

    @router.get('/jobs/{job_id}')
    def inspect(job_id: str, producer=Depends(authenticate)):
        return owned(engine_callable(), job_id, producer)

    @router.post('/artifacts/{artifact_id}')
    def stage(artifact_id: str, payload: ChunkRequest, producer=Depends(authenticate)):
        # No path, URL, command or telemetry is accepted from producers.
        engine = engine_callable()
        ComputeRetention(engine).pin_upload(artifact_id, producer.actor)
        return engine.artifacts.put(artifact_id, **payload.model_dump())

    @router.get('/jobs/{job_id}/artifacts/{artifact_id}')
    def fetch(job_id: str, artifact_id: str, offset: int = 0, length: int = 262144,
              producer=Depends(authenticate)):
        engine = engine_callable()
        job = owned(engine, job_id, producer)
        attempt = job['attempts'][-1] if job['attempts'] else {}
        if job['state'] != 'completed' and not attempt.get('external_write_unknown'):
            raise ValueError('result is not committed centrally')
        if not attempt.get('result_json'):
            raise ValueError('result evidence is not archived centrally')
        result = json.loads(attempt['result_json'])
        if artifact_id not in engine.result_artifact_ids(result):
            raise PermissionError('artifact is not part of this result')
        return engine.artifacts.get(artifact_id, offset=offset, length=length)

    @router.post('/jobs/{job_id}/publication')
    def publish(job_id: str, payload: PublicationRequest, producer=Depends(authenticate)):
        if (payload.target not in producer.policy.get('publish_targets', [])
                or not payload.request_key.startswith(producer.policy.get('request_prefix', producer.name + ':'))):
            raise PermissionError('publication target is outside producer scope')
        engine = engine_callable()
        owned(engine, job_id, producer)
        return ComputePublications(engine.queue).begin(job_id, **payload.model_dump(), actor=producer.actor)

    @router.post('/publications/{receipt_id}/finish')
    def finish(receipt_id: str, payload: PublicationFinish, producer=Depends(authenticate)):
        return ComputePublications(engine_callable().queue).finish(receipt_id, **payload.model_dump(), actor=producer.actor)

    @router.post('/jobs/{job_id}/consume')
    def consume(job_id: str, payload: ConsumeRequest, producer=Depends(authenticate)):
        engine = engine_callable()
        owned(engine, job_id, producer)
        ComputePublications(engine.queue)
        return ComputeRetention(engine).consume(job_id, actor=producer.actor, evidence=payload.evidence)

    @router.get('/publications/{receipt_id}')
    def publication(receipt_id: str, producer=Depends(authenticate)):
        return ComputePublications(engine_callable().queue).inspect(receipt_id, actor=producer.actor)

    return router
