"""Idempotency Guard — prevents duplicate trades from the same signal.

If run_analyst.py crashes between DB write and Telegram, the next run
will detect the duplicate signal and skip it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from config import get_config


class IdempotencyGuard:
    """Ensure each signal creates exactly one trade, no duplicates."""

    # Signal expires after 30 minutes — if not processed, assume lost
    SIGNAL_EXPIRY_SECONDS = 1800

    @staticmethod
    def signal_already_created_trade(signal_id: str, conn: sqlite3.Connection) -> bool:
        """Check if a signal already has a trade record.

        Args:
            signal_id: The unique signal ID
            conn: Database connection

        Returns:
            True if trade already exists for this signal
        """
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT trade_id FROM trades WHERE signal_id = ? LIMIT 1",
            (signal_id,)
        ).fetchone()
        return existing is not None

    @staticmethod
    def create_trade_idempotent(
        signal_id: str,
        trade_data: dict,
        conn: sqlite3.Connection,
    ) -> Optional[str]:
        """Create a trade if and only if the signal hasn't already spawned one.

        Args:
            signal_id: The unique signal ID
            trade_data: Trade record to insert
            conn: Database connection

        Returns:
            trade_id if created, None if already exists
        """
        # Check for duplicate
        if IdempotencyGuard.signal_already_created_trade(signal_id, conn):
            return None

        # Insert trade (atomic)
        try:
            conn.execute("""
                INSERT INTO trades (
                    trade_id, signal_id, symbol, direction, leverage,
                    margin_mode, entry_price, stop_loss, take_profit,
                    liquidation_price, status, opened_at, setup_type,
                    confidence_score, signal_source, signal_raw,
                    market_regime, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data["trade_id"],
                signal_id,
                trade_data["symbol"],
                trade_data["direction"],
                trade_data.get("leverage", 10),
                trade_data.get("margin_mode", "ISOLATED"),
                trade_data["entry_price"],
                trade_data.get("stop_loss"),
                trade_data.get("take_profit"),
                trade_data.get("liquidation_price"),
                trade_data.get("status", "OPEN"),
                datetime.now(timezone.utc).isoformat(),
                trade_data.get("setup_type", "PRICE_ACTION"),
                trade_data.get("confidence_score", 0),
                trade_data.get("signal_source", "analyst"),
                trade_data.get("signal_raw", ""),
                trade_data.get("market_regime", "UNKNOWN"),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
            return trade_data["trade_id"]

        except sqlite3.IntegrityError as e:
            # Duplicate trade_id or other constraint violation
            conn.rollback()
            return None

    @staticmethod
    def garbage_collect_expired_signals(conn: sqlite3.Connection) -> int:
        """Delete pending signals older than SIGNAL_EXPIRY_SECONDS.

        These are signals that were never processed (no trade created).
        Safe to clean up.

        Returns:
            Number of signals deleted
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=IdempotencyGuard.SIGNAL_EXPIRY_SECONDS)).isoformat()

        cursor = conn.execute("""
            DELETE FROM signals
            WHERE timestamp < ?
            AND signal_id NOT IN (SELECT DISTINCT signal_id FROM trades)
        """, (cutoff,))

        conn.commit()
        return cursor.rowcount
