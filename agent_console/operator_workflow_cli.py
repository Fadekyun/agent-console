"""Local owner workflow controls sharing the authenticated UI's request contracts.

Structured requests are read from files or stdin, never JSON in command arguments.
This surface must not run inside a managed agent session, even for inspection.
"""
import argparse
import json
from functools import partial
import os
from pathlib import Path
import sys

from pydantic import ValidationError

from .workflow_api import AttachRequest, DependenciesRequest, DeliverRequest
from .workflow_dispatch_api import (
    ControlRequest, DecisionRequest, EditRequest, PolicyRequest, ProposalRequest,
    ReconcileRequest,
)
from .workflow_release_api import Authorize, Preview, Start

MARKERS = ('AGENT_CONSOLE_REPORTING_URL', 'AGENT_CONSOLE_SESSION_ID',
           'AGENT_CONSOLE_EVIDENCE_CAPABILITY')
MAX_PAYLOAD_BYTES = 256 * 1024


def argument_error(parser, message):
    # Invalid arguments can accidentally contain credentials or JSON values.
    argparse.ArgumentParser.error(parser, 'invalid workflow arguments; see --help and supply structured requests with --json-file or --stdin')


class WorkflowArgumentParser(argparse.ArgumentParser):
    error = argument_error


def add_commands(commands):
    manage = commands.add_parser('manage', help='local human owner workflow administration')
    manage.error = partial(argument_error, manage)
    actions = manage.add_subparsers(dest='manage_group', required=True,
                                   parser_class=WorkflowArgumentParser)

    def command(parsers, name, model=None, identity=True):
        item = parsers.add_parser(name)
        if identity:
            item.add_argument('identity', help='durable session, step, attempt or release ID')
        if model:
            source = item.add_mutually_exclusive_group(required=True)
            source.add_argument('--json-file', type=Path, help='UTF-8 API request object (maximum 256 KiB)')
            source.add_argument('--stdin', action='store_true', help='read the UTF-8 API request object from stdin')
        item.set_defaults(workflow_request_model=model)
        return item

    command(actions, 'inspect')
    command(actions, 'propose', ProposalRequest)
    command(actions, 'policy', PolicyRequest)
    command(actions, 'control', ControlRequest).description = 'Set state to running, paused or stopped using a ControlRequest object.'
    for group, choices in (
        ('step', [('preview', None, True), ('review', DecisionRequest, True),
                  ('edit', EditRequest, True), ('retry', None, True)]),
        ('attempt', [('reconcile', ReconcileRequest, True)]),
        ('connections', [('inspect', None, True), ('attach', AttachRequest, True),
                         ('dependencies', DependenciesRequest, True), ('deliver', DeliverRequest, True)]),
        ('releases', [('targets', None, False), ('evidence', None, True),
                      ('preview', Preview, False), ('authorize', Authorize, False),
                      ('list', None, True), ('inspect', None, True), ('start', Start, True)]),
    ):
        nested = actions.add_parser(group).add_subparsers(dest='manage_action', required=True,
                                                         parser_class=WorkflowArgumentParser)
        for name, model, identity in choices:
            command(nested, name, model, identity)


def payload(args):
    model = args.workflow_request_model
    if model is None:
        return {}
    try:
        if args.json_file is not None:
            with args.json_file.open('rb') as stream:
                raw = stream.read(MAX_PAYLOAD_BYTES + 1)
        else:
            raw = getattr(sys.stdin, 'buffer', sys.stdin).read(MAX_PAYLOAD_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError()
        value = json.loads(raw.decode('utf-8'))
        return model.model_validate(value).model_dump()
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        # Pydantic/JSON exceptions include raw input; never print those values.
        raise ValueError('Invalid workflow request: supply a UTF-8 JSON object matching the API request schema (maximum 256 KiB)') from None


def run(args, manager_factory=None):
    # Check before reading payloads or creating a manager, including read routes:
    # local managers/services migrate stores and therefore are not agent readers.
    if any(os.getenv(key) for key in MARKERS):
        raise PermissionError('workflow manage is unsupported in managed sessions; use a local human owner terminal')
    data = payload(args)
    if manager_factory is None:
        from .manager import SessionManager
        manager_factory = SessionManager
    try:
        return dispatch(args, data, manager_factory())
    except (KeyError, ValueError, PermissionError, RuntimeError, OSError):
        # Service errors can quote untrusted configuration values or native output.
        raise ValueError('Workflow request rejected; inspect current state and check the payload, authority, expected version, preview hash and request key') from None


def dispatch(args, data, manager):
    from .workflow_engine import WorkflowEngine
    from .workflow_service import WorkflowService
    from .workflow_release import ReleaseService

    group = args.manage_group
    action = getattr(args, 'manage_action', None)
    identity = getattr(args, 'identity', None)
    actor = 'CLI-user'
    if group == 'releases':
        service = ReleaseService(manager)
        if action == 'targets':
            return service.catalog()
        if action == 'evidence':
            return service.evidence_options(identity)
        if action == 'preview':
            return service.preview(**data)
        if action == 'authorize':
            return service.authorize(**data, actor=actor)
        if action == 'list':
            return service.list(identity)
        if action == 'inspect':
            return service.inspect(identity)
        if action == 'start':
            return service.start(identity, **data, actor=actor)
    elif group == 'connections':
        service = WorkflowService(manager)
        if action == 'attach':
            return service.attach(identity, data.pop('session_id'), **data, actor=actor)
        session_id = service.session(identity)['id']
        if action == 'inspect':
            return service.graph().inspect(session_id)
        if action == 'dependencies':
            return service.graph().dependencies(session_id, **data, actor=actor)
        if action == 'deliver':
            return service.graph().deliver(session_id, **data, actor=actor)
    else:
        engine = WorkflowEngine(manager)
        if group == 'inspect':
            return engine.inspect(identity)
        if group == 'propose':
            return engine.propose(identity, **data, actor=actor)
        if group == 'policy':
            return engine.configure(identity, **data, actor=actor)
        if group == 'control':
            return engine.control(identity, **data, actor=actor)
        if group == 'attempt' and action == 'reconcile':
            return engine.reconcile(identity, **data, actor=actor)
        if group == 'step':
            if action == 'preview':
                step = engine.step(identity)
                return engine.preview(step['root_id'], step['config'])
            if action == 'review':
                return engine.decide(identity, **data, actor=actor)
            if action == 'edit':
                return engine.edit(identity, **data, actor=actor)
            if action == 'retry':
                return engine.retry(identity, actor=actor)
    raise ValueError('Unsupported owner workflow command')
