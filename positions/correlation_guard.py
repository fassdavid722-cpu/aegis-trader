"""Correlation Risk Guard.

Crypto futures are highly correlated. BTC, ETH, SOL, AVAX, LINK 
often move together. Opening 3 longs simultaneously is frequently 
one correlated bet at 3× the intended size.

RULES (hardcoded — no learning adjustments):
  MAX_CORRELATED_LONGS  = 1   (one long at a time across all alts)
  MAX_CORRELATED_SHORTS = 1   (one short at a time across all alts)
  TOTAL_OPEN_TRADES     = 3   (already in run_analyst.py — unchanged)

BTC is treated as the benchmark asset.
All other assets (ETH, SOL, AVAX, LINK, XRP, DOGE, ADA) are
classified as "ALT" and share the correlated pool.

So the limits in practice are:
  1× BTC position (either direction, any time)
  1× ALT position (either direction, any time)
  Max 2 simultaneous positions total via this guard
  (still capped by MAX_DAILY_TRADES=3 in the heartbeat runner)

RATIONALE:
  In a crypto sell-off, BTC drops and alts drop harder.
  Opening ETH + SOL + AVAX LONG = 3× the BTC beta with 3× the risk.
  This guard prevents that without needing a correlation matrix.
  Simple, explicit, hard to game.

FUTURE:
  Replace asset-class bucketing with a live correlation coefficient
  (30-day rolling) once 500+ trades exist in the journal.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


# ── Config ──────────────────────────────────────────────────────────────────
BTC_ASSETS = {"BTCUSDT"}

# Everything else treated as correlated "crypto alt"
ALT_ASSETS = {
    "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
}

MAX_BTC_LONGS  = 1
MAX_BTC_SHORTS = 1
MAX_ALT_LONGS  = 1
MAX_ALT_SHORTS = 1


def get_open_positions_all() -> list[dict]:
    """Load all OPEN/PARTIAL trades from DB."""
    db_path = Path(__file__).parent.parent / "data" / "journal.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, direction, status FROM trades WHERE status IN ('OPEN','PARTIAL')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class CorrelationGuard:
    """
    Call CorrelationGuard.allows(symbol, direction) before opening any trade.
    Returns (True, "") if allowed, or (False, reason_string) if blocked.
    """

    @classmethod
    def allows(cls, symbol: str, direction: str) -> tuple[bool, str]:
        """
        Check if adding this trade would breach correlation limits.

        Returns:
            (True, "")              → trade is allowed
            (False, reason_str)     → trade is blocked, reason explains why
        """
        open_trades = get_open_positions_all()
        return cls.allows_given_positions(symbol, direction, open_trades)

    @classmethod
    def allows_given_positions(
        cls,
        symbol: str,
        direction: str,
        open_trades: list[dict],
    ) -> tuple[bool, str]:
        """Same as allows() but takes pre-loaded trade list (testable without DB)."""

        is_btc = symbol in BTC_ASSETS
        is_alt = symbol in ALT_ASSETS

        # Count current open positions by bucket and direction
        btc_longs  = sum(1 for t in open_trades
                         if t["symbol"] in BTC_ASSETS and t["direction"] == "LONG")
        btc_shorts = sum(1 for t in open_trades
                         if t["symbol"] in BTC_ASSETS and t["direction"] == "SHORT")
        alt_longs  = sum(1 for t in open_trades
                         if t["symbol"] in ALT_ASSETS and t["direction"] == "LONG")
        alt_shorts = sum(1 for t in open_trades
                         if t["symbol"] in ALT_ASSETS and t["direction"] == "SHORT")

        if is_btc:
            if direction == "LONG" and btc_longs >= MAX_BTC_LONGS:
                return False, (
                    f"BTC LONG limit reached ({btc_longs}/{MAX_BTC_LONGS}). "
                    f"BTC position already open."
                )
            if direction == "SHORT" and btc_shorts >= MAX_BTC_SHORTS:
                return False, (
                    f"BTC SHORT limit reached ({btc_shorts}/{MAX_BTC_SHORTS}). "
                    f"BTC position already open."
                )

        elif is_alt:
            if direction == "LONG" and alt_longs >= MAX_ALT_LONGS:
                existing = [t["symbol"] for t in open_trades
                            if t["symbol"] in ALT_ASSETS and t["direction"] == "LONG"]
                return False, (
                    f"Alt LONG correlation limit reached ({alt_longs}/{MAX_ALT_LONGS}). "
                    f"Already long: {', '.join(existing)}. "
                    f"Adding {symbol} LONG would create correlated exposure."
                )
            if direction == "SHORT" and alt_shorts >= MAX_ALT_SHORTS:
                existing = [t["symbol"] for t in open_trades
                            if t["symbol"] in ALT_ASSETS and t["direction"] == "SHORT"]
                return False, (
                    f"Alt SHORT correlation limit reached ({alt_shorts}/{MAX_ALT_SHORTS}). "
                    f"Already short: {', '.join(existing)}. "
                    f"Adding {symbol} SHORT would create correlated exposure."
                )

        return True, ""

    @classmethod
    def exposure_summary(cls, open_trades: Optional[list[dict]] = None) -> str:
        """Human-readable exposure report for heartbeat summaries."""
        if open_trades is None:
            open_trades = get_open_positions_all()

        btc_l = [t["symbol"] for t in open_trades if t["symbol"] in BTC_ASSETS and t["direction"] == "LONG"]
        btc_s = [t["symbol"] for t in open_trades if t["symbol"] in BTC_ASSETS and t["direction"] == "SHORT"]
        alt_l = [t["symbol"] for t in open_trades if t["symbol"] in ALT_ASSETS and t["direction"] == "LONG"]
        alt_s = [t["symbol"] for t in open_trades if t["symbol"] in ALT_ASSETS and t["direction"] == "SHORT"]

        lines = []
        if btc_l: lines.append(f"BTC LONG:  {', '.join(btc_l)}")
        if btc_s: lines.append(f"BTC SHORT: {', '.join(btc_s)}")
        if alt_l: lines.append(f"Alt LONG:  {', '.join(alt_l)}")
        if alt_s: lines.append(f"Alt SHORT: {', '.join(alt_s)}")

        remaining_btc_l = MAX_BTC_LONGS  - len(btc_l)
        remaining_btc_s = MAX_BTC_SHORTS - len(btc_s)
        remaining_alt_l = MAX_ALT_LONGS  - len(alt_l)
        remaining_alt_s = MAX_ALT_SHORTS - len(alt_s)

        lines.append(
            f"Capacity: BTC {remaining_btc_l}L/{remaining_btc_s}S "
            f"| Alt {remaining_alt_l}L/{remaining_alt_s}S remaining"
        )
        return "\n".join(lines) if lines else "No open positions"
