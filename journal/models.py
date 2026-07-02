"""Journal models - database operations for the trading journal.

All writes are append-only. Historical records are never modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from database import get_db_connection


class JournalWriter:
    """Writes trade data to the SQLite journal."""

    def record_market_context(
        self,
        trade_id: str,
        price_at_entry: float,
        volatility: Optional[float] = None,
        trend_score: Optional[float] = None,
        volume_score: Optional[float] = None,
        session_tag: Optional[str] = None,
        regime_tag: Optional[str] = None,
        correlation_notes: Optional[str] = None,
    ) -> None:
        """Record market context at trade entry."""
        conn = get_db_connection()
        conn.execute(
            """INSERT INTO market_context 
               (trade_id, price_at_entry, volatility, trend_score, 
                volume_score, session_tag, regime_tag, correlation_notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, price_at_entry, volatility, trend_score,
             volume_score, session_tag, regime_tag, correlation_notes),
        )
        conn.commit()

    def record_analysis(
        self,
        trade_id: str,
        summary: str,
        trade_quality: str,
        regime_quality: str,
        execution_quality: str,
        lessons: list[str],
        confidence: Optional[float] = None,
    ) -> None:
        """Record post-trade analysis from coach layer."""
        conn = get_db_connection()
        conn.execute(
            """INSERT OR REPLACE INTO trade_analysis
               (trade_id, summary, trade_quality, regime_quality, 
                execution_quality, lessons, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, summary, trade_quality, regime_quality,
             execution_quality, str(lessons), confidence),
        )
        conn.commit()

    def log_system_event(
        self,
        level: str,
        component: str,
        message: str,
        details: Optional[str] = None,
    ) -> None:
        """Log operational event."""
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO system_log (level, component, message, details) VALUES (?, ?, ?, ?)",
            (level, component, message, details),
        )
        conn.commit()


class JournalReader:
    """Reads and queries trade journal data."""

    def get_trade(self, trade_id: str) -> Optional[dict[str, Any]]:
        """Get complete trade record with context and analysis."""
        conn = get_db_connection()

        # Get trade
        trade = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()

        if not trade:
            return None

        result = dict(trade)

        # Get context
        context = conn.execute(
            "SELECT * FROM market_context WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        if context:
            result["market_context"] = dict(context)

        # Get analysis
        analysis = conn.execute(
            "SELECT * FROM trade_analysis WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        if analysis:
            result["analysis"] = dict(analysis)

        # Get transitions
        transitions = conn.execute(
            "SELECT * FROM state_transitions WHERE trade_id = ? ORDER BY timestamp",
            (trade_id,),
        ).fetchall()
        result["transitions"] = [dict(t) for t in transitions]

        return result

    def get_trades_by_regime(self, regime: str) -> list[dict[str, Any]]:
        """Get all trades for a specific market regime."""
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM trades WHERE market_regime = ?", (regime,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_performance_summary(self) -> dict[str, Any]:
        """Get overall performance statistics."""
        conn = get_db_connection()

        total = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'CLOSED'"
        ).fetchone()[0]

        wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE result = 'WIN'"
        ).fetchone()[0]

        losses = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE result = 'LOSS'"
        ).fetchone()[0]

        avg_pnl = conn.execute(
            "SELECT AVG(pnl_percent) FROM trades WHERE status = 'CLOSED'"
        ).fetchone()[0]

        return {
            "total_closed": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total, 4) if total > 0 else 0,
            "avg_pnl_percent": round(avg_pnl, 4) if avg_pnl else 0,
        }
