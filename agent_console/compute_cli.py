"""Local human-owner controls; managed sessions have no writer fallback."""
import os
from pathlib import Path

from .managed_context import has_managed_markers


def add_commands(commands):
    from .operator_workflow_cli import WorkflowArgumentParser
    root = commands.add_parser('compute', help='local owner compute queue controls')
    actions = root.add_subparsers(dest='compute_action', required=True,
                                 parser_class=WorkflowArgumentParser)
    for action in ('status', 'tick'):
        actions.add_parser(action)
    inspect = actions.add_parser('inspect')
    inspect.add_argument('identity')
    for action in ('enqueue', 'hold', 'reconcile'):
        item = actions.add_parser(action)
        if action == 'reconcile':
            item.add_argument('identity')
        source = item.add_mutually_exclusive_group(required=True)
        source.add_argument('--json-file', type=Path)
        source.add_argument('--stdin', action='store_true')
    for action in ('retry', 'cancel'):
        actions.add_parser(action).add_argument('identity')


def run(args, settings_factory=None):
    # Must precede payload reads, Settings/manager construction and migrations.
    if has_managed_markers():
        raise PermissionError('compute controls are unsupported in managed sessions; use a local human owner terminal')
    from .compute_engine import ComputeEngine
    from .config import Settings
    from .compute_api import JobRequest, HoldRequest, ReconcileRequest
    from .operator_workflow_cli import payload
    models = {'enqueue': JobRequest, 'hold': HoldRequest, 'reconcile': ReconcileRequest}
    args.workflow_request_model = models.get(args.compute_action)
    data = payload(args)
    engine = ComputeEngine((settings_factory or Settings.from_env)().state_dir,
                           enabled=os.getenv('AGENT_CONSOLE_COMPUTE_ENABLED') == '1')
    action, actor = args.compute_action, 'CLI-user'
    try:
        if action == 'status':
            return engine.status()
        if action == 'inspect':
            return engine.queue.inspect(args.identity)
        if action == 'tick':
            engine.tick()
            return engine.status()
        if action == 'enqueue':
            return engine.queue.enqueue(**data, actor=actor)
        if action == 'hold':
            return engine.queue.set_hold(**data, actor=actor)
        if action == 'reconcile':
            return engine.queue.reconcile_terminated(args.identity, **data, actor=actor)
        if action == 'retry':
            return engine.queue.retry(args.identity, actor=actor)
        if action == 'cancel':
            return engine.queue.cancel(args.identity, actor=actor)
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError('Compute request rejected; inspect the receipt, limits and termination evidence') from None
    raise ValueError('unsupported compute command')
