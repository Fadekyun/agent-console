from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_console.database import Database
from agent_console.inspection import InspectionUnavailable, read_session_snapshot
from agent_console import _inspection_reader as reader


WORKER = Path(reader.__file__).resolve()
ENV = {"LC_ALL": "C", "PATH": os.defpath}


def fingerprint(root: Path) -> dict:
    return {str(path.relative_to(root)): (
        path.lstat().st_mode, path.lstat().st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    ) for path in root.rglob("*")}


def probe(body: str, before: str = "", *, pass_fds=()) -> subprocess.CompletedProcess:
    program = f"""
import importlib.util, ctypes, os, mmap, errno, sys, json, socket, fcntl
spec = importlib.util.spec_from_file_location('private_reader', {str(WORKER)!r})
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
libc = ctypes.CDLL(None, use_errno=True)
lib = r._library()
{before}
r.install_guard()
{body}
"""
    return subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                          input="", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, cwd="/", env=ENV, close_fds=True,
                          pass_fds=pass_fds, timeout=8)


class InspectionFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="inspection93-")
        self.root = Path(self.temp.name)
        self.db = self.root / "state" / "console ? fixture.db"
        Database(self.db).migrate()  # Fixture setup only, outside reader boundary.
        self.writer = sqlite3.connect(self.db)
        # Deliberate historical schema10 fixture on the schema11 dependency.
        self.writer.execute("DROP TABLE integration_requests")
        self.writer.execute("ALTER TABLE sessions DROP COLUMN execution_kind")
        self.writer.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
        self.writer.commit()
        self.writer.execute("SELECT * FROM sessions").fetchall()  # Keep WAL/SHM present.

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def session(self, name="one", *, kind=None):
        self.writer.execute(
            "INSERT INTO sessions(id,tmux_name,created_at,status,evidence_capability_hash) VALUES(?,?,?,'reserved','DO-NOT-EXPOSE')",
            (name, name, "2026-09-13"))
        if kind is not None:
            self.writer.execute("UPDATE sessions SET execution_kind=? WHERE id=?", (kind, name))
        self.writer.commit()

    def test_schema10_reads_committed_wal_without_changing_files(self):
        self.session()
        before = fingerprint(self.root)
        result = read_session_snapshot(self.db)
        self.assertEqual(result["schema_version"], 10)
        self.assertEqual(result["sessions"][0]["execution_kind"], "interactive")
        self.assertEqual(result["sessions"][0]["status"], "reserved")
        self.assertNotIn("evidence_capability_hash", result["sessions"][0])
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(result))
        self.assertEqual(fingerprint(self.root), before)

    def test_schema11_projection_and_integration_data_are_separate(self):
        self.writer.execute("ALTER TABLE sessions ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'interactive'")
        self.writer.execute("CREATE TABLE integration_requests(secret TEXT)")
        self.writer.execute("INSERT INTO integration_requests VALUES ('PRIVATE REQUEST PROMPT')")
        self.writer.execute("UPDATE schema_meta SET value='11' WHERE key='schema_version'")
        self.session(kind="integration-plan")
        self.writer.execute("UPDATE sessions SET initial_task='PRIVATE REQUEST', attention_note='PRIVATE REQUEST', exit_reason='PRIVATE REQUEST'")
        self.writer.commit()
        before = fingerprint(self.root)
        result = read_session_snapshot(self.db)
        self.assertEqual(result["sessions"][0]["execution_kind"], "integration-plan")
        self.assertNotIn("integration_requests", result)
        self.assertNotIn("PRIVATE REQUEST", json.dumps(result))
        self.assertEqual(fingerprint(self.root), before)

    def test_new_commits_are_visible_without_immutable_or_cached_snapshot(self):
        self.session("first")
        self.assertEqual(len(read_session_snapshot(self.db)["sessions"]), 1)
        self.session("second")
        before = fingerprint(self.root)
        self.assertEqual(len(read_session_snapshot(self.db)["sessions"]), 2)
        self.assertEqual(fingerprint(self.root), before)

    def test_uncommitted_writer_is_not_visible_or_modified(self):
        self.session()
        self.writer.execute("UPDATE sessions SET status='completed'")
        before = fingerprint(self.root)
        self.assertEqual(read_session_snapshot(self.db)["sessions"][0]["status"], "reserved")
        self.assertEqual(fingerprint(self.root), before)
        self.writer.rollback()

    def test_concurrent_commits_produce_consistent_multi_table_snapshots(self):
        self.session()
        self.writer.execute("UPDATE sessions SET status='v0'")
        self.writer.execute("INSERT INTO session_groups(id,name,status,created_at) VALUES('g','group','v0','2026-09-13')")
        self.writer.commit()
        stop, ready = threading.Event(), threading.Event()
        committed, errors = [], []
        def write_transactions():
            connection = sqlite3.connect(self.db, timeout=2)
            try:
                number = 0
                while not stop.is_set():
                    number += 1; value = f"v{number}"
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("UPDATE sessions SET status=?", (value,))
                    connection.execute("UPDATE session_groups SET status=?", (value,))
                    connection.commit()
                    committed.append(value); ready.set(); time.sleep(0.002)
            except Exception as exc:
                errors.append(exc); ready.set()
            finally:
                connection.close()
        thread = threading.Thread(target=write_transactions)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            for _ in range(5):
                snapshot = read_session_snapshot(self.db)
                self.assertEqual(snapshot["sessions"][0]["status"], snapshot["session_groups"][0]["status"])
        finally:
            stop.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        self.assertGreater(len(committed), 1)
        # Attribute changing WAL/SHM bytes to the writer, then compare all source
        # bytes/modes/mtime during a quiescent final read with its connection held.
        before = fingerprint(self.root)
        final = read_session_snapshot(self.db)
        self.assertEqual(final["sessions"][0]["status"], committed[-1])
        self.assertEqual(fingerprint(self.root), before)
        self.assertEqual(self.writer.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)

    def test_missing_sidecars_are_refused_without_creation(self):
        self.writer.close()
        before = fingerprint(self.root)
        with self.assertRaises(InspectionUnavailable):
            read_session_snapshot(self.db)
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_shm_with_committed_wal_is_not_rebuilt(self):
        self.session()
        Path(str(self.db) + "-shm").unlink()  # Deliberately incomplete private fixture.
        before = fingerprint(self.root)
        with self.assertRaises(InspectionUnavailable):
            read_session_snapshot(self.db)
        self.assertEqual(fingerprint(self.root), before)

    def test_existing_rollback_journal_database_needs_no_sidecars(self):
        self.session(); self.writer.close()
        setup = sqlite3.connect(self.db)
        setup.execute("PRAGMA journal_mode=DELETE"); setup.close()
        before = fingerprint(self.root)
        self.assertEqual(len(read_session_snapshot(self.db)["sessions"]), 1)
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_state_does_not_create_parent_or_database(self):
        absent = self.root / "missing-home" / "state" / "db"
        before = fingerprint(self.root)
        with self.assertRaisesRegex(InspectionUnavailable, "state-unavailable"):
            read_session_snapshot(absent)
        self.assertEqual(fingerprint(self.root), before)

    def test_unsupported_versions_remain_untouched(self):
        for version in ("9", "12", "invalid", "10.0", ""):
            with self.subTest(version=version):
                self.writer.execute("UPDATE schema_meta SET value=?", (version,)); self.writer.commit()
                before = fingerprint(self.root)
                with self.assertRaisesRegex(InspectionUnavailable, "schema-unsupported"):
                    read_session_snapshot(self.db)
                self.assertEqual(fingerprint(self.root), before)

    def test_claimed_schema11_without_column_is_rejected(self):
        self.writer.execute("UPDATE schema_meta SET value='11'"); self.writer.commit()
        before = fingerprint(self.root)
        with self.assertRaisesRegex(InspectionUnavailable, "schema-invalid"):
            read_session_snapshot(self.db)
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_schema_or_corrupt_database_is_value_blind(self):
        self.writer.execute("DROP TABLE schema_meta"); self.writer.commit()
        with self.assertRaisesRegex(InspectionUnavailable, "schema-invalid"):
            read_session_snapshot(self.db)
        bad = self.root / "corrupt.db"; bad.write_bytes(b"PRIVATE secret not SQLite")
        with self.assertRaises(InspectionUnavailable) as caught:
            read_session_snapshot(bad)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_read_only_source_modes_succeed_and_stay_unchanged(self):
        self.session()
        paths = list((self.root / "state").iterdir())
        for path in paths: path.chmod(0o400)
        (self.root / "state").chmod(0o500)
        before = fingerprint(self.root)
        try:
            self.assertEqual(len(read_session_snapshot(self.db)["sessions"]), 1)
            self.assertEqual(fingerprint(self.root), before)
        finally:
            (self.root / "state").chmod(0o700)
            for path in paths: path.chmod(0o600)

    def test_environment_python_hooks_and_paths_are_not_loaded(self):
        marker = self.root / "hook-ran"
        (self.root / "sitecustomize.py").write_text(f"open({str(marker)!r},'w').write('bad')")
        (self.root / "usercustomize.py").write_text(f"open({str(marker)!r},'w').write('bad')")
        with patch.dict(os.environ, {"PYTHONPATH": str(self.root), "PYTHONSTARTUP": str(self.root / 'sitecustomize.py'),
                                     "PYTHONHOME": str(self.root), "LD_PRELOAD": "/missing/evil.so"}):
            self.assertTrue(read_session_snapshot(self.db)["ok"])
        self.assertFalse(marker.exists())

    def test_worker_rejects_regular_file_stdout_without_writing_error(self):
        target = self.root / "source"; target.write_bytes(b"preserve")
        with target.open("r+b") as output:
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WORKER)],
                                    input=b"{}", stdout=output, stderr=subprocess.PIPE, env=ENV)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_worker_rejects_regular_file_stderr_without_writing_error(self):
        target = self.root / "source"; target.write_bytes(b"preserve")
        with target.open("r+b") as output:
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WORKER)],
                                    input=b"{}", stdout=subprocess.PIPE, stderr=output, env=ENV)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_worker_rejects_regular_file_stdin(self):
        target = self.root / "request"; target.write_text(json.dumps({"database": str(self.db)}))
        with target.open("rb") as source:
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(WORKER)],
                                    stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")


class ReaderGuardTests(unittest.TestCase):
    def assert_probe_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guard_unavailable_has_no_unguarded_read(self):
        result = probe("raise AssertionError('guard bypass')", before="r._library = lambda: (_ for _ in ()).throw(OSError('missing library'))")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("guard-unavailable", result.stderr)
        with self.assertRaisesRegex(reader.Unavailable, "guard-unavailable"):
            reader.read_snapshot(Path("/not-opened"))

    def test_inherited_source_descriptor_is_closed(self):
        with tempfile.TemporaryFile() as source:
            fd = source.fileno()
            result = probe(f"try:\n os.fstat({fd})\n raise AssertionError('inherited fd open')\nexcept OSError as exc: assert exc.errno == errno.EBADF\nprint('closed')", pass_fds=(fd,))
            self.assert_probe_ok(result)
            source.seek(0); self.assertEqual(source.read(), b"")

    def test_existing_writable_shared_mapping_is_rejected(self):
        with tempfile.NamedTemporaryFile() as source:
            source.write(b"x" * 4096); source.flush()
            before = f"source=open({source.name!r},'r+b'); mapped=mmap.mmap(source.fileno(),4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)"
            result = probe("raise AssertionError('mapped bypass')", before=before)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("guard-unavailable", result.stderr)
            source.seek(0); self.assertEqual(source.read(), b"x" * 4096)

    def test_new_shared_write_and_later_mprotect_are_denied(self):
        result = probe("""
try:
 mmap.mmap(-1,4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)
 raise AssertionError('shared write accepted')
except OSError: pass
libc.mmap.restype=ctypes.c_void_p
address=libc.mmap(None,4096,mmap.PROT_READ,mmap.MAP_SHARED|mmap.MAP_ANONYMOUS,-1,0)
assert address != ctypes.c_void_p(-1).value
libc.mprotect.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int]
assert libc.mprotect(address,4096,mmap.PROT_READ|mmap.PROT_WRITE) == -1
print('denied')
""")
        self.assert_probe_ok(result)

    def test_filesystem_process_and_descriptor_bypasses_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"original"; path.write_bytes(b"original")
            before = fingerprint(Path(tmp))
            result = probe(f"""
path={str(path)!r}
operations=[lambda:os.open(path,os.O_RDWR), lambda:os.open(path+'new',os.O_CREAT|os.O_RDONLY),
 lambda:os.unlink(path),lambda:os.rename(path,path+'new'),lambda:os.chmod(path,0o777),
 lambda:os.utime(path),lambda:os.mkdir(path+'dir'),lambda:os.link(path,path+'link'),
 lambda:os.symlink(path,path+'symlink'),lambda:os.truncate(path,0),lambda:os.dup(1),
 lambda:fcntl.fcntl(1,fcntl.F_DUPFD,10),lambda:socket.socket(),lambda:os.fork(),
 lambda:os.execve('/bin/true',['true'],{{}})]
for operation in operations:
 try:
  operation(); raise AssertionError('mutation allowed')
 except OSError: pass
for name in ['openat2','creat','pkey_mprotect','io_uring_setup','pidfd_getfd','process_vm_writev','ptrace','clone3']:
 number=lib.seccomp_syscall_resolve_name(name.encode())
 if number>=0:
  ctypes.set_errno(0)
  assert libc.syscall(number,0,0,0,0,0,0) == -1, name
  assert ctypes.get_errno() == errno.EACCES, (name,ctypes.get_errno())
print('denied')
""")
            self.assert_probe_ok(result)
            self.assertEqual(fingerprint(Path(tmp)), before)

    def test_x32_syscall_convention_cannot_use_native_allowlist(self):
        result = probe("libc.syscall(0x40000000 | lib.seccomp_syscall_resolve_name(b'getpid')); raise AssertionError('alternate ABI returned')")
        self.assertLess(result.returncode, 0, result.stdout + result.stderr)

    def test_i386_syscall_convention_cannot_use_native_allowlist(self):
        before = """
lib.seccomp_syscall_resolve_name_arch.argtypes=[ctypes.c_uint32,ctypes.c_char_p]
number=lib.seccomp_syscall_resolve_name_arch(0x40000003,b'getpid')
assert number >= 0
code=mmap.mmap(-1,4096,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS,prot=mmap.PROT_READ|mmap.PROT_WRITE|mmap.PROT_EXEC)
code.write(b'\\xb8'+number.to_bytes(4,'little')+b'\\xcd\\x80\\xc3')
invoke=ctypes.CFUNCTYPE(ctypes.c_long)(ctypes.addressof(ctypes.c_char.from_buffer(code)))
"""
        result = probe("invoke(); raise AssertionError('alternate ABI returned')", before=before)
        self.assertLess(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
