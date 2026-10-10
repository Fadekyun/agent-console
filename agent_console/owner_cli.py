"""Local human owner project and write-only environment commands."""
import argparse
import getpass
import sys
import warnings

from .environment_service import describe, mutate, scope


class OwnerArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown arguments might contain an accidentally supplied secret.
        if message.startswith('unrecognized arguments:'):
            message = 'unrecognized arguments; environment values must use --stdin or the hidden prompt'
        super().error(message)


def add_commands(commands):
    projects = commands.add_parser('project', help='local owner project administration')
    actions = projects.add_subparsers(dest='owner_action', required=True)
    actions.add_parser('list')
    for action in ('show', 'create', 'update', 'delete', 'assign', 'unassign'):
        command = actions.add_parser(action)
        command.add_argument('name' if action == 'create' else 'project_id')
        if action in ('create', 'update'):
            if action == 'update':
                command.add_argument('--name')
                command.add_argument('--status', choices=('active', 'paused', 'completed'))
            command.add_argument('--repository')
            command.add_argument('--description')
        if action in ('assign', 'unassign'):
            command.add_argument('session_name')
    environment = commands.add_parser('environment', help='local owner environment administration; values never returned')
    actions = environment.add_subparsers(dest='owner_action', required=True)
    for action in ('list', 'set', 'unset', 'enable', 'disable', 'suppress'):
        command = actions.add_parser(action)
        command.add_argument('--project', dest='project_id', help='project scope; default: global')
        if action != 'list':
            command.add_argument('name')
        if action == 'set':
            command.add_argument('--stdin', action='store_true', help='read exact UTF-8 value from stdin, at most 32 KiB; otherwise use hidden terminal prompt')
            command.add_argument('--state', choices=('enabled', 'disabled'), default='enabled')


def secret_value(args):
    if args.stdin:
        stream = getattr(sys.stdin, 'buffer', sys.stdin)
        value = stream.read(32769)
        if isinstance(value, bytes):
            if len(value) > 32768:
                raise ValueError()
            value = value.decode('utf-8')
        return value
    if not sys.stdin.isatty():
        raise ValueError('Environment set requires --stdin or a terminal for the hidden prompt')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass('Environment value: ')
    except (EOFError, OSError, getpass.GetPassWarning):
        raise ValueError('Cannot read hidden environment value') from None


def run(args, manager):
    action = args.owner_action
    actor = {'actor': 'CLI-user', 'surface': 'CLI'}
    if args.command == 'project':
        if action == 'list':
            return manager.list_projects()
        if action in ('create', 'update'):
            for field, limit in (('name', 200), ('repository', 1000), ('description', 2000)):
                value = getattr(args, field, None)
                if value is not None and (len(value) > limit or (action == 'create' and field == 'name' and not value)):
                    raise ValueError('Invalid project ' + field)
            fields = {field: getattr(args, field) for field in ('name', 'repository', 'description')}
            if action == 'create':
                return manager.create_project(**fields, **actor)
            return manager.update_project(args.project_id, **fields, status=args.status, **actor)
        if action == 'show':
            return manager.get_project(args.project_id)
        if action == 'delete':
            manager.delete_project(args.project_id, **actor)
            return {'deleted': args.project_id}
        if not 1 <= len(args.session_name) <= 80:
            raise ValueError('Invalid session name')
        method = manager.assign_session_to_project if action == 'assign' else manager.unassign_session_from_project
        return method(args.session_name, args.project_id, **actor)
    project_id = args.project_id
    scope(manager, project_id)
    if action == 'list':
        return describe(manager, project_id)
    try:
        if action == 'unset':
            mutate(manager, project_id, lambda: manager.environment.delete(args.name, project_id=project_id))
        else:
            state = args.state if action == 'set' else {'enable': 'enabled', 'disable': 'disabled', 'suppress': 'suppressed'}[action]
            value = secret_value(args) if action == 'set' else None
            mutate(manager, project_id, lambda: manager.environment.put(args.name, value=value, state=state, project_id=project_id))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Invalid environment request: check name, UTF-8 value (32 KiB maximum), state, scope and reserved variable rules; set values with --stdin or a hidden terminal prompt') from None
    manager.database.audit('environment.deleted' if action == 'unset' else 'environment.updated', args.name,
                           'success', **actor, details={'scope': manager.environment.scope(project_id)})
    return describe(manager, project_id)
