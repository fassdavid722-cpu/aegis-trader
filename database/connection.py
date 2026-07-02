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
    """Add columns and fix constraints introduced after initial schema creation."""
    existing = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
    if "expected_r" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN expected_r REAL")
        print("  [migrate] added expected_r column")
    if "actual_r" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN actual_r REAL")
        print("  [migrate] added actual_r column")

    # Fix: trades status CHECK constraint must include 'PARTIAL'
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='trades'").fetchone()
    if schema and "PARTIAL" not in schema[0]:
        print("  [migrate] fixing trades status CHECK constraint (adding PARTIAL)")
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades_new AS SELECT * FROM trades WHERE 0;
            INSERT INTO trades_new SELECT * FROM trades;
            DROP TABLE trades;
            CREATE TABLE trades (
                trade_id TEXT PRIMARY KEY,
                signal_id TEXT REFERENCES signals(signal_id),
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT')),
                leverage INTEGER DEFAULT 10,
                margin_mode TEXT DEFAULT 'ISOLATED' CHECK(margin_mode IN ('ISOLATED', 'CROSS')),
                entry_price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                liquidation_price REAL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                opened_at TEXT,
                closed_at TEXT,
                result TEXT CHECK(result IS NULL OR result IN ('WIN', 'LOSS', 'BREAKEVEN')),
                exit_price REAL,
                exit_reason TEXT,
                pnl_percent REAL,
                pnl_absolute REAL,
                trading_fee REAL DEFAULT 0,
                setup_type TEXT,
                confidence_score REAL,
                signal_source TEXT,
                signal_raw TEXT,
                market_regime TEXT,
                expected_r REAL,
                actual_r REAL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO trades SELECT * FROM trades_new;
            DROP TABLE trades_new;
        """)
        conn.execute("PRAGMA foreign_keys = ON")
        print("  [migrate] trades status constraint fixed ✅")

    # Fix: trade_analysis CHECK constraints (remove them)
    ta_schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='trade_analysis'").fetchone()
    if ta_schema and "CHECK" in ta_schema[0]:
        print("  [migrate] removing trade_analysis CHECK constraints")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ta_new AS SELECT * FROM trade_analysis WHERE 0;
            INSERT INTO ta_new SELECT * FROM trade_analysis;
            DROP TABLE trade_analysis;
            CREATE TABLE trade_analysis (
                analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL UNIQUE REFERENCES trades(trade_id),
                summary TEXT,
                trade_quality TEXT,
                regime_quality TEXT,
                execution_quality TEXT,
                lessons TEXT,
                confidence REAL,
                created_at TEXT
            );
            INSERT INTO trade_analysis SELECT * FROM ta_new;
            DROP TABLE ta_new;
        """)
        conn.execute("PRAGMA foreign_keys = ON")
        print("  [migrate] trade_analysis constraints fixed ✅")

    # Fix: signals source CHECK constraint
    sig_schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='signals'").fetchone()
    if sig_schema and "groq" not in sig_schema[0]:
        print("  [migrate] fixing signals source CHECK constraint (adding groq/analyst)")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals_new AS SELECT * FROM signals WHERE 0;
            INSERT INTO signals_new SELECT * FROM signals;
            DROP TABLE signals;
            CREATE TABLE signals (
                signal_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                symbol TEXT NOT NULL,
                contract_type TEXT DEFAULT 'PERPETUAL',
                side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT', 'CLOSE')),
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                leverage INTEGER DEFAULT 10,
                margin_mode TEXT DEFAULT 'ISOLATED' CHECK(margin_mode IN ('ISOLATED', 'CROSS')),
                timestamp TEXT NOT NULL,
                confidence REAL,
                metadata TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO signals SELECT * FROM signals_new;
            DROP TABLE signals_new;
        """)
        conn.execute("PRAGMA foreign_keys = ON")
        print("  [migrate] signals source constraint fixed ✅")

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
