"""Trade Thesis Invalidation Engine.

Checks THREE conditions on every monitor cycle for every open trade:
1. Zone broken  — price has closed BEYOND the zone that justified the entry
2. Regime flip  — 4H regime has changed from what it was at entry
3. Time stop    — trade has been open >MAX_HOLD_HOURS with no TP hit
4. Funding flip — funding rate has inverted against the trade direction

All checks are additive to the existing SL/TP monitor.
None of these replace the hard SL — they trigger early exits BEFORE SL.
"""
from __future__ import annotations

import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────
MAX_HOLD_HOURS = 12          # Time-based stop: close if open >12h with no TP
FUNDING_FLIP_THRESHOLD = 0.05  # % — funding now fights direction by this much


def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Invalidation] Telegram error: {e}")


class ThesisInvalidationEngine:
    """
    Run against each open trade to determine if the entry thesis is still valid.
    Returns an InvalidationReason if exit is warranted, else None.
    """

    @staticmethod
    def check(
        trade: dict,
        current_price: float,
        current_funding_rate: Optional[float],
        current_regime: Optional[str],
        candles_15m: Optional[list] = None,
    ) -> Optional["InvalidationReason"]:
        """
        Returns InvalidationReason if the trade should be exited early.
        Returns None if the trade thesis is still intact.

        Checks (in order of severity):
          1. Time stop  — open too long with no progress
          2. Zone break — price has moved through the entry zone
          3. Funding flip — funding now strongly opposes direction
          4. Regime flip  — 4H regime has changed against the trade
        """
        direction = trade.get("direction", "LONG")
        entry     = trade.get("entry_price", 0) or 0
        opened_at = trade.get("opened_at", "")
        meta      = _load_meta(trade)

        # ── 1. Time-based stop ─────────────────────────────────────────
        if opened_at:
            try:
                t0 = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - t0).total_seconds() / 3600
                if age_hours > MAX_HOLD_HOURS:
                    return InvalidationReason(
                        trigger="TIME_STOP",
                        detail=(
                            f"Trade open {age_hours:.1f}h — exceeds {MAX_HOLD_HOURS}h time stop. "
                            f"No TP hit. Exit to free capital."
                        ),
                        severity="MEDIUM",
                    )
            except Exception:
                pass

        # ── 2. Zone break ───────────────────────────────────────────────
        zone_top    = meta.get("zone_top")
        zone_bottom = meta.get("zone_bottom")
        zone_type   = meta.get("zone_type")  # "DEMAND" or "SUPPLY"

        if zone_top and zone_bottom:
            if direction == "LONG" and zone_type == "DEMAND":
                # Demand zone broken: price CLOSES below zone bottom
                if current_price < zone_bottom * 0.998:
                    return InvalidationReason(
                        trigger="ZONE_BROKEN",
                        detail=(
                            f"Demand zone broken: price {current_price:,.4f} "
                            f"< zone bottom {zone_bottom:,.4f} (−0.2% buffer). "
                            f"Entry thesis invalidated — supply overwhelmed demand."
                        ),
                        severity="HIGH",
                    )
            elif direction == "SHORT" and zone_type == "SUPPLY":
                # Supply zone broken: price CLOSES above zone top
                if current_price > zone_top * 1.002:
                    return InvalidationReason(
                        trigger="ZONE_BROKEN",
                        detail=(
                            f"Supply zone broken: price {current_price:,.4f} "
                            f"> zone top {zone_top:,.4f} (+0.2% buffer). "
                            f"Entry thesis invalidated — demand overwhelmed supply."
                        ),
                        severity="HIGH",
                    )

        # ── 3. Funding flip ─────────────────────────────────────────────
        if current_funding_rate is not None:
            rate_pct = current_funding_rate * 100
            if direction == "LONG" and rate_pct > FUNDING_FLIP_THRESHOLD:
                return InvalidationReason(
                    trigger="FUNDING_FLIP",
                    detail=(
                        f"Funding now {rate_pct:.3f}% — market overleveraged LONG. "
                        f"Squeeze risk favours shorts. Long thesis weakened."
                    ),
                    severity="LOW",
                )
            elif direction == "SHORT" and rate_pct < -FUNDING_FLIP_THRESHOLD:
                return InvalidationReason(
                    trigger="FUNDING_FLIP",
                    detail=(
                        f"Funding now {rate_pct:.3f}% — market overleveraged SHORT. "
                        f"Squeeze risk favours longs. Short thesis weakened."
                    ),
                    severity="LOW",
                )

        # ── 4. Regime flip ──────────────────────────────────────────────
        entry_regime = trade.get("market_regime", "UNKNOWN")
        if current_regime and entry_regime and current_regime != "UNKNOWN":
            regime_flipped = False
            reason_text    = ""

            if direction == "LONG":
                if entry_regime == "BULL_TREND" and current_regime in ("BEAR_TREND", "HIGH_VOLATILITY"):
                    regime_flipped = True
                    reason_text = f"4H regime flipped {entry_regime} → {current_regime}. Trend reversed."
                elif entry_regime == "SIDEWAYS" and current_regime == "BEAR_TREND":
                    regime_flipped = True
                    reason_text = f"Range turned into BEAR_TREND. Long thesis invalidated."
            elif direction == "SHORT":
                if entry_regime == "BEAR_TREND" and current_regime in ("BULL_TREND", "HIGH_VOLATILITY"):
                    regime_flipped = True
                    reason_text = f"4H regime flipped {entry_regime} → {current_regime}. Trend reversed."
                elif entry_regime == "SIDEWAYS" and current_regime == "BULL_TREND":
                    regime_flipped = True
                    reason_text = f"Range turned into BULL_TREND. Short thesis invalidated."

            if regime_flipped:
                return InvalidationReason(
                    trigger="REGIME_FLIP",
                    detail=reason_text,
                    severity="HIGH",
                )

        return None  # Thesis still valid — hold


class InvalidationReason:
    def __init__(self, trigger: str, detail: str, severity: str) -> None:
        self.trigger  = trigger   # "ZONE_BROKEN" | "REGIME_FLIP" | "FUNDING_FLIP" | "TIME_STOP"
        self.detail   = detail
        self.severity = severity  # "HIGH" | "MEDIUM" | "LOW"

    def format_telegram(self, trade: dict) -> str:
        icon = "🔴" if self.severity == "HIGH" else ("🟡" if self.severity == "MEDIUM" else "🟠")
        return (
            f"{icon} THESIS INVALIDATED — {trade['symbol']} {trade['direction']}\n\n"
            f"Trigger: {self.trigger}\n"
            f"Reason:  {self.detail}\n\n"
            f"Entry:  {trade['entry_price']:,.4f}\n"
            f"SL was: {trade['stop_loss']:,.4f}\n"
            f"Exiting early to preserve capital.\n\n"
            f"This is NOT a SL hit — the thesis changed."
        )


def _load_meta(trade: dict) -> dict:
    """Load signal metadata for a trade — zone info lives there."""
    db_path = Path(__file__).parent.parent / "data" / "journal.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        sig = conn.execute(
            "SELECT metadata FROM signals WHERE signal_id=?",
            (trade.get("signal_id", ""),)
        ).fetchone()
        conn.close()
        if sig:
            return json.loads(sig[0] or "{}")
    except Exception:
        pass
    return {}
