"""Database connection management.

SQLite with WAL mode for concurrent reads.
Thread-safe, single file path from config.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

# Thread-local connection storage per db path
_local = threading.local()

# Single canonical path — set once at init
_DB_PATH: Optional[Path] = None


def set_db_path(path: Path) -> None:
    """Set the canonical DB path. Must be called before first connection."""
    global _DB_PATH
    _DB_PATH = path
    # Reset any open connections so they re-open with the new path
    _local.__dict__.clear()


def _resolve_path() -> Path:
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    # Fallback: read from config (never hardcoded)
    try:
        from config import get_config
        _DB_PATH = get_config().system.database_path
    except Exception:
        _DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"
    return _DB_PATH


def get_db_connection() -> sqlite3.Connection:
    """Get a thread-local database connection with WAL + FK enforcement."""
    db_path = _resolve_path()
    db_key  = str(db_path)

    conn_key = f"conn_{db_key}"
    if not hasattr(_local, conn_key) or getattr(_local, conn_key) is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")   # concurrent reads during writes
        conn.execute("PRAGMA synchronous = NORMAL")  # safe + fast
        setattr(_local, conn_key, conn)

    return getattr(_local, conn_key)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema creation."""
    existing = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
    if "expected_r" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN expected_r REAL")
        print("  [migrate] added expected_r column")
    if "actual_r" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN actual_r REAL")
        print("  [migrate] added actual_r column")
    conn.commit()


def init_database(db_path: Optional[Path] = None) -> None:
    """Initialize the database schema. Safe to call on every startup."""
    if db_path is not None:
        set_db_path(db_path)

    resolved = _resolve_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(resolved))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path) as f:
        conn.executescript(f.read())

    conn.commit()
    print(f"✅ Database initialized at {resolved}")
    _migrate_schema(conn)
    conn.close()
