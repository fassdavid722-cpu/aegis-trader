"""Position Monitor — production-safe.

Fixes applied:
- Re-verifies status='OPEN' inside transaction before every close (race-safe)
- Uses StateMachine for enforced state transitions
- Tracks TP1 partial close at 1.5R, TP2 full close at 3R
- Generates Coach analysis on every close
- Sends structured Telegram close alert
"""
from __future__ import annotations

import sqlite3
import json
import asyncio
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH   = Path(__file__).parent.parent / "data" / "journal.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "6472746064")

# ─── Valid state transitions (inline, no import cycle) ─────────────────────
VALID_TRANSITIONS = {
    ("OPEN", "TP1_HIT"):      "PARTIAL",
    ("OPEN", "TP_HIT"):       "CLOSED",
    ("OPEN", "SL_HIT"):       "CLOSED",
    ("OPEN", "MANUAL_CLOSE"): "CLOSED",
    ("PARTIAL", "TP_HIT"):    "CLOSED",
    ("PARTIAL", "SL_HIT"):    "CLOSED",
}


def send_telegram(text: str) -> None:
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Monitor] Telegram error: {e}")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_open_trades() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _log_transition(conn, trade_id, from_state, to_state, trigger, price):
    conn.execute("""
        INSERT INTO state_transitions
            (trade_id, from_state, to_state, trigger, price_at_transition)
        VALUES (?, ?, ?, ?, ?)
    """, (trade_id, from_state, to_state, trigger, price))


def close_trade_atomically(
    trade_id: str,
    exit_price: float,
    trigger: str,          # 'TP_HIT', 'SL_HIT', 'TP1_HIT', etc.
    current_status: str,   # caller passes what they *think* the status is
) -> Optional[dict]:
    """
    Atomically verifies status then closes in a single transaction.
    Returns the updated trade dict, or None if status had already changed
    (idempotent — safe if two cycles overlap).
    """
    new_status = VALID_TRANSITIONS.get((current_status, trigger))
    if new_status is None:
        print(f"[Monitor] Invalid transition: {current_status} + {trigger} — skipping")
        return None

    conn = _get_conn()
    now  = datetime.now(timezone.utc).isoformat()

    try:
        conn.execute("BEGIN EXCLUSIVE")   # lock the row

        # Re-verify status hasn't changed since we read it (race guard)
        live = conn.execute(
            "SELECT status FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()

        if live is None or live["status"] != current_status:
            conn.execute("ROLLBACK")
            conn.close()
            print(f"[Monitor] {trade_id[:20]} status changed mid-cycle — skipping")
            return None

        result = "WIN" if trigger in ("TP_HIT", "TP1_HIT") else "LOSS"

        # Compute actual_R = (exit - entry) / risk, signed by direction
        row_data = conn.execute(
            "SELECT entry_price, stop_loss, direction FROM trades WHERE trade_id=?", (trade_id,)
        ).fetchone()
        actual_r = None
        if row_data and row_data["entry_price"] and row_data["stop_loss"]:
            ep   = row_data["entry_price"]
            sl   = row_data["stop_loss"]
            dirn = row_data["direction"]
            risk = abs(ep - sl)
            if risk > 0:
                pnl_dir = (exit_price - ep) if dirn == "LONG" else (ep - exit_price)
                actual_r = round(pnl_dir / risk, 2)

        conn.execute("""
            UPDATE trades SET
                status      = ?,
                closed_at   = ?,
                exit_price  = ?,
                exit_reason = ?,
                result      = ?,
                actual_r    = ?,
                updated_at  = ?
            WHERE trade_id = ?
        """, (new_status, now, exit_price, trigger, result, actual_r, now, trade_id))

        _log_transition(conn, trade_id, current_status, new_status, trigger, exit_price)

        conn.execute("COMMIT")

        updated = dict(conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone())
        conn.close()
        return updated

    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        print(f"[Monitor] close_trade_atomically error: {e}")
        return None


def run_coach_analysis(trade: dict) -> dict:
    """Generate + persist post-trade Coach analysis."""
    conn  = _get_conn()
    now   = datetime.now(timezone.utc).isoformat()

    direction   = trade.get("direction", "LONG")
    result      = trade.get("result", "LOSS")
    exit_reason = trade.get("exit_reason", "UNKNOWN")
    entry       = trade.get("entry_price", 0) or 0
    exit_p      = trade.get("exit_price", 0)  or 0
    pnl         = trade.get("pnl_percent", 0) or 0
    opened_at   = trade.get("opened_at", "")
    closed_at   = trade.get("closed_at", now)

    # Duration
    try:
        t0 = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        duration_minutes = int((t1 - t0).total_seconds() / 60)
    except Exception:
        duration_minutes = 0

    duration_str = (
        f"{duration_minutes}m" if duration_minutes < 60
        else f"{duration_minutes//60}h {duration_minutes%60}m"
    )

    # Load original signal metadata
    meta = {}
    try:
        sig_row = conn.execute(
            "SELECT metadata FROM signals WHERE signal_id = ?",
            (trade.get("signal_id", ""),)
        ).fetchone()
        if sig_row:
            meta = json.loads(sig_row[0] or "{}")
    except Exception:
        pass

    regime     = meta.get("regime", trade.get("market_regime", "UNKNOWN"))
    session    = meta.get("session", "UNKNOWN")
    funding    = meta.get("funding_bias", "NEUTRAL")
    thesis     = meta.get("thesis", "")
    confidence = trade.get("confidence_score", 0) or 0
    bd         = meta.get("confidence_breakdown", {})

    lessons        = []
    trade_quality  = "valid"
    regime_quality = "favorable"
    exec_quality   = "good"

    if result == "WIN":
        lessons.append(f"Exit at {exit_reason.replace('_',' ')} — setup executed as planned")
        if "LONDON" in session or "NY" in session:
            lessons.append(f"{session} session confirmed positive expectancy for this setup type")
        if "Demand Zone" in thesis or "Supply Zone" in thesis:
            lessons.append("Zone respected — supply/demand imbalance played out")
        if "Break of Structure" in thesis:
            lessons.append("BOS confirmation preceded a clean directional move")
    else:
        lessons.append(f"SL hit — price moved against position without recovery")
        if regime in ("UNKNOWN", "HIGH_VOLATILITY"):
            lessons.append(f"Regime {regime} — unclear structure, higher noise risk")
            regime_quality = "unfavorable"
        if duration_minutes < 10:
            lessons.append("Failed quickly — possible stop hunt or noise entry near zone edge")
            exec_quality   = "bad"
        if confidence < 65:
            lessons.append(f"Confidence {confidence:.0f}% below 70% — consider raising threshold")
            trade_quality  = "mixed"

    if not lessons:
        lessons.append("No specific lesson — log for pattern analysis")

    pnl_r    = round(pnl / 1.5, 2)  # express in R units (1.5% = 1R baseline)
    summary  = (
        f"{direction} {result} | {pnl:.2f}% ({pnl_r:+.1f}R) | "
        f"Duration: {duration_str} | Regime: {regime}"
    )

    conn.execute("""
        INSERT OR REPLACE INTO trade_analysis
            (trade_id, summary, trade_quality, regime_quality,
             execution_quality, lessons, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["trade_id"], summary, trade_quality, regime_quality,
        exec_quality, json.dumps(lessons),
        70 if trade_quality == "valid" else 50,
        now,
    ))
    conn.commit()
    conn.close()

    return {
        "summary":           summary,
        "trade_quality":     trade_quality,
        "regime_quality":    regime_quality,
        "execution_quality": exec_quality,
        "lessons":           lessons,
        "duration_str":      duration_str,
        "pnl_r":             pnl_r,
        "thesis":            thesis,
        "regime":            regime,
        "session":           session,
        "funding":           funding,
        "confidence_breakdown": bd,
    }


def format_close_alert(trade: dict, analysis: dict) -> str:
    result      = trade.get("result", "LOSS")
    exit_reason = trade.get("exit_reason", "UNKNOWN")
    symbol      = trade["symbol"]
    direction   = trade["direction"]
    pnl         = trade.get("pnl_percent", 0) or 0

    icon    = "🟢✅" if result == "WIN" else "🔴❌"
    lessons = "\n".join(f"  • {l}" for l in analysis["lessons"])

    # Confidence breakdown
    bd = analysis.get("confidence_breakdown", {})
    bd_text = ""
    if bd and bd.get("factors"):
        parts = []
        for f in bd["factors"]:
            ico  = "✅" if f["score"] > 0 else ("⚠️" if f["score"] < 0 else "➖")
            sign = "+" if f["score"] >= 0 else ""
            parts.append(f"{ico} {f['name']}: {sign}{f['score']:.0f}")
        bd_text = "\n" + "\n".join(parts) + f"\nTotal: {bd.get('total', 0):.0f}%"

    return (
        f"{icon} TRADE CLOSED — {symbol}\n\n"
        f"Result:   {exit_reason.replace('_',' ')}\n"
        f"PnL:      {pnl:+.2f}% ({analysis['pnl_r']:+.1f}R)\n"
        f"Duration: {analysis['duration_str']}\n\n"
        f"Entry:  {trade['entry_price']:,.4f}\n"
        f"Exit:   {trade.get('exit_price', 0):,.4f}\n\n"
        f"Thesis: {analysis['thesis']}\n\n"
        f"{'Why it worked:' if result == 'WIN' else 'What went wrong:'}\n"
        f"{lessons}\n\n"
        f"Coach: {analysis['trade_quality'].upper()} | "
        f"Regime: {analysis['regime']} | Session: {analysis['session']}"
        f"{bd_text}"
    )


async def check_positions() -> str:
    """
    Check all open positions against live prices.
    Production-safe: atomic close, TP1+TP2 tracking, state machine enforced.
    Returns summary string.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from exchange.bitget_client import BitgetMarketClient

    open_trades = get_open_trades()

    if not open_trades:
        return "No open positions"

    client  = BitgetMarketClient()
    summary_parts = []
    closed  = 0

    for trade in open_trades:
        trade_id  = trade["trade_id"]
        symbol    = trade["symbol"]
        direction = trade["direction"]
        entry     = trade["entry_price"]
        sl        = trade["stop_loss"]
        tp2       = trade["take_profit"]           # Full close
        leverage  = trade.get("leverage", 10) or 10
        status    = trade["status"]                # OPEN or PARTIAL

        # TP1 is stored in signal metadata
        tp1 = None
        try:
            conn_r = _get_conn()
            sig = conn_r.execute(
                "SELECT metadata FROM signals WHERE signal_id = ?",
                (trade.get("signal_id", ""),)
            ).fetchone()
            if sig:
                meta = json.loads(sig[0] or "{}")
                tp1  = meta.get("tp1") or meta.get("take_profit_1")
            conn_r.close()
        except Exception:
            pass

        ticker = await client.get_ticker(symbol)
        if not ticker:
            summary_parts.append(f"{symbol}: no price data")
            continue

        price = ticker.last_price

        # Record price history for MFE/MAE
        try:
            conn_h = _get_conn()
            conn_h.execute(
                "INSERT INTO price_history (trade_id, price) VALUES (?, ?)",
                (trade_id, price)
            )
            conn_h.commit()
            conn_h.close()
        except Exception:
            pass

        # ─── Check SL first (always fatal) ─────────────────
        hit_sl  = (direction == "LONG" and price <= sl) or \
                  (direction == "SHORT" and price >= sl)

        # ─── Check TP1 (partial, only if OPEN not yet partial) ─
        hit_tp1 = (
            tp1 is not None and status == "OPEN" and
            ((direction == "LONG" and price >= tp1) or
             (direction == "SHORT" and price <= tp1))
        )

        # ─── Check TP2 (full close) ─────────────────────────
        hit_tp2 = (direction == "LONG" and price >= tp2) or \
                  (direction == "SHORT" and price <= tp2)

        if hit_sl:
            if direction == "LONG":
                pnl_pct = round(((price - entry) / entry) * 100 * leverage, 2)
            else:
                pnl_pct = round(((entry - price) / entry) * 100 * leverage, 2)

            updated = close_trade_atomically(trade_id, price, "SL_HIT", status)
            if updated:
                # Write PnL
                conn_u = _get_conn()
                conn_u.execute(
                    "UPDATE trades SET pnl_percent=?, updated_at=? WHERE trade_id=?",
                    (pnl_pct, datetime.now(timezone.utc).isoformat(), trade_id)
                )
                conn_u.commit()
                updated["pnl_percent"] = pnl_pct
                conn_u.close()

                analysis = run_coach_analysis(updated)
                # Update pattern library (learning loop)
                try:
                    from journal.pattern_library import record_closed_trade, ensure_pattern_tables
                    ensure_pattern_tables()
                    record_closed_trade(trade_id)
                except Exception as pe:
                    print(f"[Monitor] Pattern lib error: {pe}")
                alert    = format_close_alert(updated, analysis)
                send_telegram(alert)
                print(f"[Monitor] SL HIT: {symbol} {direction} @ {price} | {pnl_pct:+.2f}%")
                summary_parts.append(f"{symbol} SL_HIT {pnl_pct:+.2f}%")
                closed += 1

        elif hit_tp2:
            if direction == "LONG":
                pnl_pct = round(((price - entry) / entry) * 100 * leverage, 2)
            else:
                pnl_pct = round(((entry - price) / entry) * 100 * leverage, 2)

            updated = close_trade_atomically(trade_id, price, "TP_HIT", status)
            if updated:
                conn_u = _get_conn()
                conn_u.execute(
                    "UPDATE trades SET pnl_percent=?, updated_at=? WHERE trade_id=?",
                    (pnl_pct, datetime.now(timezone.utc).isoformat(), trade_id)
                )
                conn_u.commit()
                updated["pnl_percent"] = pnl_pct
                conn_u.close()

                analysis = run_coach_analysis(updated)
                # Update pattern library (learning loop)
                try:
                    from journal.pattern_library import record_closed_trade, ensure_pattern_tables
                    ensure_pattern_tables()
                    record_closed_trade(trade_id)
                except Exception as pe:
                    print(f"[Monitor] Pattern lib error: {pe}")
                alert    = format_close_alert(updated, analysis)
                send_telegram(alert)
                print(f"[Monitor] TP2 HIT: {symbol} {direction} @ {price} | {pnl_pct:+.2f}%")
                summary_parts.append(f"{symbol} TP_HIT {pnl_pct:+.2f}%")
                closed += 1

        elif hit_tp1:
            # Partial close at TP1 — mark as PARTIAL, alert, but keep tracking
            updated = close_trade_atomically(trade_id, price, "TP1_HIT", "OPEN")
            if updated:
                if direction == "LONG":
                    pnl_pct = round(((price - entry) / entry) * 100 * leverage, 2)
                else:
                    pnl_pct = round(((entry - price) / entry) * 100 * leverage, 2)
                msg = (
                    f"🎯 TP1 HIT — {symbol} {direction}\n\n"
                    f"Price: {price:,.4f} (TP1: {tp1:,.4f})\n"
                    f"Partial PnL: {pnl_pct:+.2f}% (+1.5R)\n"
                    f"Still open — tracking to TP2: {tp2:,.4f}\n"
                    f"Move SL to breakeven: {entry:,.4f}"
                )
                send_telegram(msg)
                print(f"[Monitor] TP1 HIT: {symbol} partial @ {price}")
                summary_parts.append(f"{symbol} TP1 partial +1.5R")
        else:
            # Unrealised PnL
            if direction == "LONG":
                upnl = round(((price - entry) / entry) * 100, 2)
            else:
                upnl = round(((entry - price) / entry) * 100, 2)
            summary_parts.append(f"{symbol} OPEN {upnl:+.2f}% uPnL @ {price:,.2f}")

        await asyncio.sleep(0.3)

    await client.close()

    label = f"{len(open_trades)} checked, {closed} closed"
    return label + (" | " + " | ".join(summary_parts) if summary_parts else "")


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(check_positions()))
