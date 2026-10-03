"""Tiny direct-file bootstrap: never pass environment values through a shell."""
import json
import os
import stat
import sys


def main():
    path, *argv = sys.argv[1:]
    if not argv:
        raise SystemExit("Missing harness command")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SystemExit("Launch environment must be a private owned file")
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            snapshot = json.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)
    environment = snapshot["environment"]
    # Public identity/path exports remain in launchers for rename compatibility.
    # Read only the explicit list, never the tmux server's ambient environment.
    for key in snapshot.get("launcher_keys", []):
        if key in os.environ:
            environment[key] = os.environ[key]
    os.execvpe(argv[0], argv, environment)


if __name__ == "__main__":
    main()
