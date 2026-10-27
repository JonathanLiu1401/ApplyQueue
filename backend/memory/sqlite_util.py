"""Shared SQLite open/recover logic for memory_store.db (MemoryStore and MetricsStore).

- ``connect`` waits up to BUSY_TIMEOUT_S for a lock held by another process (a second app
  instance, the MCP server, a backup tool) instead of the 5 s default.
- ``init_or_recover`` runs the schema setup; if the file is not a database or is malformed
  (truncated copy, disk error, a sync tool's conflict file), the db and its -wal/-shm files are
  moved aside as ``*.corrupt-<time>`` (never deleted) and a fresh db is created. Memories and
  metrics are rebuildable; a corrupt file must not stop every apply run.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path

BUSY_TIMEOUT_S = 15.0
_log = logging.getLogger("applyqueue.sqlite")
_CORRUPT_MARKERS = ("file is not a database", "database disk image is malformed", "file is encrypted",
                    "malformed database schema")


_SQLITE_HEADER = b"SQLite format 3\x00"


def _check_header(db_path: Path) -> None:
    """Refuse a non-empty file that is not SQLite before SQLite opens it: closing a connection to
    such a file can delete its -wal (which quarantine must keep), and keeping it open blocks the
    quarantine rename on Windows."""
    try:
        with open(db_path, "rb") as f:
            head = f.read(len(_SQLITE_HEADER))
    except FileNotFoundError:
        return
    if head and head != _SQLITE_HEADER:
        raise sqlite3.DatabaseError("file is not a database")


def connect(db_path: Path) -> sqlite3.Connection:
    _check_header(Path(db_path))
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:  # locked right now: WAL is set by whoever holds it
        if "locked" not in str(e).lower() and "busy" not in str(e).lower():
            conn.close()
            raise
    except BaseException:
        # A corrupt file fails here. Close the handle now: the exception's traceback keeps this
        # frame (and the connection) alive, and on Windows an open handle makes the quarantine
        # rename fail with "being used by another process" (WinError 32). SQLite may delete the
        # -wal on close, so keep a copy and put it back for quarantine to move aside.
        _close_keeping_sidecars(conn, Path(db_path))
        raise
    return conn


def _close_keeping_sidecars(conn: sqlite3.Connection, db_path: Path) -> None:
    stash = []
    for suffix in ("-wal", "-journal"):
        p = Path(str(db_path) + suffix)
        try:
            if p.exists() and p.stat().st_size:
                copy = p.with_name(p.name + ".keep")
                shutil.copyfile(p, copy)
                stash.append((p, copy))
        except OSError:
            pass
    conn.close()
    for p, copy in stash:
        try:
            if p.exists():
                copy.unlink()
            else:
                copy.replace(p)
        except OSError:
            pass


def is_corrupt_error(e: BaseException) -> bool:
    return isinstance(e, sqlite3.DatabaseError) and any(m in str(e).lower() for m in _CORRUPT_MARKERS)


def quarantine(db_path: Path) -> list[Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            dest = p.with_name(f"{p.name}.corrupt-{stamp}")
            p.replace(dest)
            moved.append(dest)
    _log.error("%s was corrupt; moved it aside (%s) and started a new one",
               db_path.name, ", ".join(m.name for m in moved))
    return moved


def init_or_recover(store, init_fn) -> None:
    """Call ``init_fn()`` (which uses ``store._get_conn()``); recover once from a corrupt file."""
    try:
        init_fn()
        store._get_conn().execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as e:
        if not is_corrupt_error(e):
            raise
        store.close()
        quarantine(Path(store.db_path))
        init_fn()
