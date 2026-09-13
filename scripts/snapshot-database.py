#!/usr/bin/env python3
"""Maintenance backup using SQLite's consistent backup API; never a CLI read fallback."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import time


def snapshot(source: Path, target: Path):
    # The caller owns a private backup directory. Never replace existing evidence.
    descriptor=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    os.close(descriptor)
    completed=False
    deadline=time.monotonic()+20
    def progress(*_):
        if time.monotonic()>deadline:
            raise ValueError('backup timed out')
    try:
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=rw',uri=True,timeout=5)) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination,pages=256,sleep=0.01,progress=progress)
                destination.execute('PRAGMA journal_mode=DELETE')
                if destination.execute('PRAGMA integrity_check').fetchone()!=('ok',):
                    raise ValueError('backup integrity check failed')
                destination.commit()
        completed=True
    finally:
        if not completed:
            # Keep failed evidence for diagnosis, distinctly named; no source deletion.
            failed=target.with_name(target.name+'.failed')
            if not failed.exists():
                target.rename(failed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('target',type=Path)
    args=parser.parse_args()
    try:
        snapshot(args.source,args.target)
        print('consistent database snapshot created and checked')
        return 0
    except (OSError,sqlite3.Error,ValueError):
        print('database snapshot unavailable',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
