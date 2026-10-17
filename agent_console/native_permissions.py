"""Describe the permissions in a prepared native command without changing them."""


def native_permission_mode(argv: list[str]) -> str:
    modes = []
    for index, argument in enumerate(argv):
        if argument == '--sandbox':
            if index + 1 >= len(argv):
                raise ValueError('Native sandbox argument has no value')
            modes.append(argv[index + 1])
        elif argument.startswith('--sandbox='):
            modes.append(argument.split('=', 1)[1])
    if len(modes) > 1 or any(mode not in {'read-only', 'workspace-write', 'danger-full-access'} for mode in modes):
        raise ValueError('Native command has ambiguous sandbox permissions')
    if '--approve-for-me' in argv:
        if modes and modes[0] != 'workspace-write':
            raise ValueError('Native automatic review conflicts with sandbox permissions')
        return 'workspace-write'
    if not modes:
        raise ValueError('Native command does not declare its permission mode')
    return modes[0]
