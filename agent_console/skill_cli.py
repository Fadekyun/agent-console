"""Registry commands; parsing and read-only inspection do not initialize sessions."""
from pathlib import Path
from .config import Settings
from .skill_registry import SkillRegistry, read_deliveries
from .skills import _resolve_canonical_root, _discover_skills
from .validation import PROFILES, TOOLS

COMMANDS = {'list', 'inspect', 'import', 'imports', 'inspect-import', 'activate', 'review', 'allow', 'disallow', 'preview', 'delivery', 'assign', 'remove'}


def add_commands(commands):
    commands.add_parser('list')
    commands.add_parser('imports')
    for command, arg in [('inspect', 'name'), ('import', 'source'), ('inspect-import', 'identifier'), ('delivery', 'session')]:
        commands.add_parser(command).add_argument(arg)
    for command in ('review', 'activate', 'allow'):
        parser = commands.add_parser(command)
        parser.add_argument('name' if command != 'activate' else 'identifier')
        parser.add_argument('--hash', required=True, dest='expected_hash')
        if command == 'allow':
            parser.add_argument('--profile', choices=sorted(PROFILES), required=True)
        else:
            parser.add_argument('--services-verified', action='store_true')
        if command == 'review':
            parser.add_argument('--decision', choices=['reviewed', 'blocked'], required=True)
    for command in ('disallow', 'assign', 'remove'):
        parser = commands.add_parser(command)
        parser.add_argument('name')
        parser.add_argument('--profile', choices=sorted(PROFILES), required=True)
    preview = commands.add_parser('preview')
    preview.add_argument('--profile', choices=sorted(PROFILES), required=True)
    preview.add_argument('--tool', choices=sorted(TOOLS), required=True)
    preview.add_argument('--repository')


def run(args):
    settings = Settings.from_env()
    registry = SkillRegistry(_resolve_canonical_root(), settings.database_path.parent)
    command = args.skills_command
    if command == 'list': return [registry.inspect(e['name']) for e in _discover_skills(registry.root)]
    if command == 'inspect': return registry.inspect(args.name)
    if command == 'imports': return registry.imports()
    if command == 'inspect-import': return registry.inspect_import(args.identifier)
    if command == 'import': return registry.stage(Path(args.source).expanduser())
    if command == 'activate':
        return registry.activate(args.identifier, expected_hash=args.expected_hash, actor='CLI-user', services_verified=args.services_verified)
    if command == 'review':
        return registry.decide(args.name, decision=args.decision, expected_hash=args.expected_hash, actor='CLI-user', services_verified=args.services_verified)
    if command == 'allow': return registry.approve(args.name, args.profile, expected_hash=args.expected_hash, actor='CLI-user')
    if command == 'disallow':
        registry.revoke(args.name, args.profile)
        return {'revoked': True}
    from .manager import SessionManager
    manager = SessionManager(settings)
    if command == 'delivery': return read_deliveries(settings.state_dir, manager.inspect(args.session)['id'])
    if command in {'assign', 'remove'}:
        from .skills import assign_skill, unassign_skill
        return (assign_skill if command == 'assign' else unassign_skill)(manager.database, args.profile, args.name, actor='CLI-user')
    from .skills import resolve_session_skills, get_profile_assignments
    result = resolve_session_skills(manager.database, args.profile, args.tool, shared_allowlist=settings.shared_skills, repository=args.repository or str(settings.workspace_root))
    names = {s['name'] for s in result['materialized']}
    names.update(a['skill_name'] for a in get_profile_assignments(manager.database, args.profile))
    result['policies'] = [registry.explain(name, args.profile, args.tool, args.repository or str(settings.workspace_root)) for name in sorted(names)]
    return result
