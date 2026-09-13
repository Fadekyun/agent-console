"""Standalone, isolated Python child for trusted SQLite inspection only.

Do not import application manager/config/auth or accept arbitrary SQL here.
There is deliberately no CLI route and no configurable guard policy.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import mmap
import os
from pathlib import Path
import platform
import resource
import sqlite3
import stat
import sys
import time


class Unavailable(Exception):
    pass


_GUARD_ACTIVE = False


class _Comparison(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("mask", ctypes.c_uint64), ("value", ctypes.c_uint64)]


def _library():
    if sys.platform != "linux" or platform.machine() != "x86_64" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise Unavailable("guard-unavailable")
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_arch_native.restype = ctypes.c_uint32
    lib.seccomp_arch_exist.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                          ctypes.c_uint, ctypes.POINTER(_Comparison)]
    lib.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint32]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    return lib


def _verify_stdio() -> None:
    # Caller-owned source files must never be usable as writable stdio.
    for fd, access in [(0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY)]:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise Unavailable("guard-unavailable")
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access:
            raise Unavailable("guard-unavailable")
        if not os.readlink(f"/proc/self/fd/{fd}").startswith("pipe:["):
            raise Unavailable("guard-unavailable")


def _verify_process_boundary() -> None:
    _verify_stdio()
    if len(os.listdir("/proc/self/task")) != 1:
        raise Unavailable("guard-unavailable")
    for line in Path("/proc/self/maps").read_text().splitlines():
        permissions = line.split()[1]
        if "w" in permissions and permissions.endswith("s"):
            raise Unavailable("guard-unavailable")
    # Fresh exec discards mappings; defense in depth closes any pass_fds extras.
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd > 2:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise


def install_guard() -> None:
    """Irreversibly restrict this fresh reader process; no best-effort path."""
    global _GUARD_ACTIVE
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        lib = _library()  # Load trusted runtime library before denying dlopen needs.
        _verify_process_boundary()
        # Deny by default: new syscalls cannot accidentally become write paths.
        context = lib.seccomp_init(0x00050000 | errno.EACCES)
        if not context:
            raise Unavailable("guard-unavailable")
        try:
            # Only native x86_64. Libseccomp rejects x32/i386 ABI bypasses.
            if lib.seccomp_arch_native() != 0xC000003E:
                raise Unavailable("guard-unavailable")
            for alternate in (0x40000003, 0x4000003E):
                if lib.seccomp_arch_exist(context, alternate) == 0:
                    raise Unavailable("guard-unavailable")
            # BADARCH=KILL_PROCESS, CTL_NNP=1; no inherited privilege escalation.
            if lib.seccomp_attr_set(context, 2, 0x80000000) or lib.seccomp_attr_set(context, 3, 1):
                raise Unavailable("guard-unavailable")

            def allow(name: str, *comparisons: _Comparison) -> None:
                number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if number < 0:
                    raise Unavailable("guard-unavailable")
                array = (_Comparison * len(comparisons))(*comparisons) if comparisons else None
                if lib.seccomp_rule_add_array(context, 0x7FFF0000, number, len(comparisons), array):
                    raise Unavailable("guard-unavailable")

            for name in ("read", "pread64", "close", "fstat", "newfstatat", "stat", "lstat", "statx",
                         "lseek", "readlink", "readlinkat", "access", "faccessat", "getcwd",
                         "brk", "munmap", "getpid", "gettid", "getuid", "geteuid", "getgid", "getegid",
                         "clock_gettime", "gettimeofday", "nanosleep", "clock_nanosleep", "getrandom",
                         "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sigaltstack",
                         "futex", "exit", "exit_group"):
                allow(name)
            # MASKED_EQ ==7. Every write/create/truncate form of open is denied.
            forbidden = os.O_ACCMODE | os.O_CREAT | os.O_TRUNC | os.O_APPEND | (os.O_TMPFILE & ~os.O_DIRECTORY)
            for name, arg in (("open", 1), ("openat", 2)):
                allow(name, _Comparison(arg, 7, forbidden, 0))
            for name in ("write", "writev"):
                for fd in (1, 2):
                    allow(name, _Comparison(0, 4, fd, 0))  # EQ ==4, datum_a=fd
            for command in (fcntl.F_GETFD, fcntl.F_SETFD, fcntl.F_GETFL,
                            fcntl.F_GETLK, fcntl.F_SETLK, fcntl.F_SETLKW):
                allow("fcntl", _Comparison(1, 4, command, 0))
            # Private writable mappings are allocator memory, never file writes.
            allow("mmap", _Comparison(3, 7, mmap.MAP_SHARED, 0), _Comparison(2, 7, mmap.PROT_EXEC, 0))
            allow("mmap", _Comparison(2, 7, mmap.PROT_WRITE | mmap.PROT_EXEC, 0))
            # No later write protection on a shared read mapping; pkey_mprotect,
            # remap_file_pages and mremap remain denied as unneeded mechanisms.
            allow("mprotect", _Comparison(2, 7, mmap.PROT_WRITE | mmap.PROT_EXEC, 0))
            allow("madvise", _Comparison(2, 4, mmap.MADV_DONTNEED, 0))
            if lib.seccomp_load(context):
                raise Unavailable("guard-unavailable")
            _GUARD_ACTIVE = True
        finally:
            lib.seccomp_release(context)
    except (OSError, AttributeError, ValueError):
        raise Unavailable("guard-unavailable") from None


SESSION_COLUMNS = (
    "id", "tmux_name", "tool", "profile", "parent_session_id", "created_at", "last_activity",
    "initial_task", "repository", "worktree", "status", "managed", "creator_surface",
    "linked_plan_id", "launcher_path", "exit_reason", "archived_transcript", "socket_scope",
    "auth_context", "agent_mode", "provider", "model", "permission_mode", "attention_state",
    "attention_note", "attention_updated_at", "attention_updated_by", "project_id",
)
PROJECTIONS = {
    "sessions": SESSION_COLUMNS,
    "delegations": ("id", "parent_session_id", "child_session_id", "profile", "task", "status",
                    "created_at", "completed_at", "result_path"),
    "session_groups": ("id", "name", "purpose", "parent_session_id", "status", "created_at", "completed_at"),
    "group_members": ("id", "group_id", "session_id", "added_at", "added_by"),
}
MAX_OUTPUT = 4 * 1024 * 1024
MAX_ROWS = 10000


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    # All callers supply literal table names from the fixed reader contract.
    row = connection.execute("SELECT type, sql FROM sqlite_schema WHERE name=?", (table,)).fetchone()
    if row is None or row[0] != "table" or (row[1] or "").lstrip().upper().startswith("CREATE VIRTUAL"):
        raise Unavailable("schema-invalid")
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def read_snapshot(path: Path) -> dict:
    # This function is private and only called after install_guard by main.
    if not _GUARD_ACTIVE:
        raise Unavailable("guard-unavailable")
    if not path.is_absolute() or not path.is_file():
        raise Unavailable("state-unavailable")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.enable_load_extension(False)
        connection.row_factory = sqlite3.Row
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 2
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute("BEGIN")
        if not {"key", "value"} <= _columns(connection, "schema_meta"):
            raise Unavailable("schema-invalid")
        versions = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchall()
        if len(versions) != 1 or versions[0][0] not in ("10", "11"):
            raise Unavailable("schema-unsupported")
        version = int(versions[0][0])
        result = {"ok": True, "schema_version": version, "source": "stored-snapshot"}
        budget = 0
        for table, columns in PROJECTIONS.items():
            expected = (*columns, "execution_kind") if table == "sessions" and version == 11 else columns
            if not set(expected) <= _columns(connection, table):
                raise Unavailable("schema-invalid")
            selected = ",".join(f'"{column}"' for column in expected)
            rows = []
            for row in connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY id LIMIT {MAX_ROWS + 1}'):
                item = dict(row)
                if table == "sessions" and version == 10:
                    item["execution_kind"] = "interactive"
                if table == "sessions" and item["execution_kind"] != "interactive":
                    for field in ("initial_task", "attention_note", "exit_reason", "archived_transcript"):
                        item[field] = None
                if table == "delegations":
                    private_ids = {s["id"] for s in result["sessions"] if s["execution_kind"] != "interactive"}
                    if item["parent_session_id"] in private_ids or item["child_session_id"] in private_ids:
                        item["task"] = None
                budget += len(json.dumps(item, ensure_ascii=True)) + 2
                if len(rows) >= MAX_ROWS or budget > MAX_OUTPUT - 4096:
                    raise Unavailable("snapshot-too-large")
                rows.append(item)
            result[table] = rows
        return result
    except sqlite3.Error:
        raise Unavailable("state-unavailable") from None
    finally:
        if connection is not None:
            connection.close()  # No commit/checkpoint/write-mode recovery.


def main() -> int:
    try:
        _verify_stdio()
    except (OSError, Unavailable):
        # Never put even an error message into a caller-supplied source file.
        return 2
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise Unavailable("guard-unavailable")
        install_guard()
        raw = sys.stdin.read(8193)
        if len(raw) > 8192:
            raise Unavailable("state-unavailable")
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {"database"} or not isinstance(request["database"], str):
            raise Unavailable("state-unavailable")
        result = read_snapshot(Path(request["database"]))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Unavailable as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError):
        print(json.dumps({"ok": False, "error": "reader-unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
