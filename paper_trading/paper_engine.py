"""Paper Trading Engine — Demo mode for LLM training.

Simulates order fills, position management, and P&L without real money.
The LLM practices trading here until it proves profitability, then we
switch to live Bitget execution.

Features:
- Virtual account balance ($10,000 starting)
- Simulated market fills (with slippage)
- TP/SL monitoring on every cycle
- Full trade journal (same schema as live)
- Performance gate: must achieve target win rate before going live
"""
from __future__ import annotations

import os
import json
import sqlite3
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, Any
import urllib.request
import urllib.parse

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6472746064")

# Paper trading config
STARTING_BALANCE = 10_000.0
SLIPPAGE_BPS = 2  # 0.02% simulated slippage
TRADING_FEE_BPS = 6  # 0.06% taker fee (Bitget)
MAX_DAILY_TRADES = 5  # Scalpers take more trades
DAILY_LOSS_LIMIT = -3.0  # -3% daily loss limit (tightened 2026-07-06)
MAX_CONSECUTIVE_LOSSES = 3  # Circuit breaker: pause after N consecutive SL hits
CONSECUTIVE_LOSS_COOLDOWN = 30  # Minutes to pause after hitting max consecutive losses
LIVE_GATE_MIN_TRADES = 50  # Need 50 demo trades before going live
LIVE_GATE_MIN_WIN_RATE = 52.0  # Need >52% win rate to go live


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Telegram] {e}")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class PaperEngine:
    """Simulated trading engine for LLM training."""

    def __init__(self) -> None:
        self.balance = STARTING_BALANCE
        self._load_balance()

    def _load_balance(self) -> None:
        """Load or initialize paper balance."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM paper_config WHERE key='balance'"
            ).fetchone()
            if row:
                self.balance = float(row[0])
        except Exception:
            # Table doesn't exist yet — create it
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT OR REPLACE INTO paper_config VALUES ('balance', ?)",
                (str(self.balance),)
            )
            conn.commit()
        conn.close()

    def _save_balance(self, conn=None) -> None:
        """Persist paper balance."""
        own_conn = conn is None
        if own_conn:
            conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO paper_config VALUES ('balance', ?)",
            (str(self.balance),)
        )
        conn.commit()
        if own_conn:
            conn.close()

    def get_balance(self) -> float:
        return self.balance

    def apply_slippage(self, price: float, direction: str, is_entry: bool = True) -> float:
        """Apply simulated slippage to a fill price."""
        slip = price * (SLIPPAGE_BPS / 10_000)
        if is_entry:
            # Buy higher, sell lower (unfavorable)
            return price + slip if direction == "LONG" else price - slip
        else:
            # Exit: sell lower, buy higher (unfavorable)
            return price - slip if direction == "LONG" else price + slip

    def calculate_fee(self, notional: float) -> float:
        """Calculate trading fee."""
        return notional * (TRADING_FEE_BPS / 10_000)

    def open_position(self, decision: dict) -> Optional[dict]:
        """Simulate opening a paper position."""
        # Check daily limits
        if self._trades_today() >= MAX_DAILY_TRADES:
            print(f"[Paper] Daily limit reached ({MAX_DAILY_TRADES})")
            return None

        if self._daily_pnl() <= DAILY_LOSS_LIMIT:
            print(f"[Paper] Daily loss limit hit ({DAILY_LOSS_LIMIT}%)")
            return None

        # CONSECUTIVE LOSS CIRCUIT BREAKER
        consec = self._consecutive_sl_losses()
        if consec >= MAX_CONSECUTIVE_LOSSES:
            last_time = self._last_loss_time()
            if last_time:
                from datetime import timedelta
                try:
                    lt = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - lt).total_seconds() / 60
                    if elapsed < CONSECUTIVE_LOSS_COOLDOWN:
                        remaining = CONSECUTIVE_LOSS_COOLDOWN - elapsed
                        msg = f"⏸️ CIRCUIT BREAKER: {consec} consecutive SL losses. Cooling down for {remaining:.0f}min"
                        print(f"[Paper] {msg}")
                        send_telegram(msg)
                        return None
                except Exception:
                    pass

        # Check for duplicate
        if self._is_already_open(decision["symbol"], decision["direction"]):
            print(f"[Paper] {decision['symbol']} {decision['direction']} already open")
            return None

        symbol = decision["symbol"]
        direction = decision["direction"]
        entry = self.apply_slippage(decision["entry"], direction, is_entry=True)
        sl = decision["stop_loss"]
        tp1 = decision["take_profit_1"]
        tp2 = decision["take_profit_2"]
        confidence = decision.get("confidence", 70)
        risk_percent = decision.get("risk_percent", 1.0)
        mode = decision.get("mode", "SCALP")
        reasoning = decision.get("reasoning", "")
        observation = decision.get("what_you_see", "")

        # Calculate position size
        risk_amount = self.balance * (risk_percent / 100.0)
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            print(f"[Paper] Invalid SL distance for {symbol}")
            return None

        position_size = risk_amount / sl_distance  # in base currency
        notional = position_size * entry
        fee = self.calculate_fee(notional)

        # Deduct fee from balance
        self.balance -= fee
        self._save_balance()

        # Save to journal
        conn = _get_conn()
        signal_id = f"groq-{uuid.uuid4().hex[:12]}"
        trade_id = f"trade-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        meta = {
            "mode": mode,
            "llm_reasoning": reasoning,
            "llm_observation": observation,
            "confidence": confidence,
            "risk_percent": risk_percent,
            "session": decision.get("session", ""),
            "slippage_applied": abs(entry - decision["entry"]),
            "fee_paid": fee,
            "tp1": tp1,
            "tp2": tp2,
            "what_you_see": observation,
            # AUDIT TRAIL — real tool values at decision time + which tools the LLM claims it used.
            # This lets us verify after the fact whether "RSI oversold" etc. was actually true.
            "tool_snapshot": decision.get("tool_snapshot", {}),
            "tools_declared": decision.get("tools_used", []),
        }

        conn.execute("""
            INSERT INTO signals
                (signal_id, source, raw_text, symbol, side,
                 entry, stop_loss, take_profit, leverage, margin_mode,
                 timestamp, confidence, metadata)
            VALUES (?, 'groq', ?, ?, ?, ?, ?, ?, 10, 'ISOLATED', ?, ?, ?)
        """, (
            signal_id, f"GROQ {direction} {symbol} @ {entry} | {reasoning}",
            symbol, direction, entry, sl, tp2,
            now, confidence, json.dumps(meta),
        ))

        risk_pts = abs(entry - sl)
        expected_r = round(abs(tp2 - entry) / risk_pts, 2) if risk_pts else 2.0

        conn.execute("""
            INSERT INTO trades
                (trade_id, signal_id, symbol, direction,
                 leverage, margin_mode, entry_price, stop_loss, take_profit,
                 liquidation_price, status, opened_at,
                 setup_type, confidence_score, signal_source, signal_raw,
                 market_regime, expected_r, created_at, updated_at)
            VALUES (?, ?, ?, ?, 10, 'ISOLATED', ?, ?, ?,
                    ?, 'OPEN', ?, 'GROQ_SCALP', ?, 'groq', ?, ?, ?, ?, ?)
        """, (
            trade_id, signal_id, symbol, direction,
            entry, sl, tp2,
            round(entry * 0.9, 4), now,
            confidence, f"GROQ {direction} {symbol}",
            mode, expected_r, now, now,
        ))

        conn.execute("""
            INSERT INTO market_context
                (trade_id, price_at_entry, session_tag, regime_tag)
            VALUES (?, ?, ?, ?)
        """, (trade_id, entry, decision.get("session", ""), mode))

        conn.execute("""
            INSERT INTO state_transitions
                (trade_id, from_state, to_state, trigger, price_at_transition)
            VALUES (?, 'PENDING', 'OPEN', 'groq_decision', ?)
        """, (trade_id, entry))

        conn.commit()
        conn.close()

        # Send Telegram alert
        emoji = "🟢" if direction == "LONG" else "🔴"
        mode_emoji = "⚡" if mode == "SCALP" else "📊"
        alert = (
            f"{emoji}{mode_emoji} PAPER TRADE — {symbol}\n\n"
            f"Mode: {mode} | Direction: {direction}\n"
            f"Entry: {entry:,.4f} (slippage: {abs(entry-decision['entry']):.4f})\n"
            f"SL: {sl:,.4f} | TP1: {tp1:,.4f} | TP2: {tp2:,.4f}\n"
            f"Confidence: {confidence}% | Risk: {risk_percent:.1f}%\n"
            f"Size: {position_size:.4f} | Fee: ${fee:.2f}\n\n"
            f"🧠 {reasoning}\n\n"
            f"Balance: ${self.balance:,.2f} | ID: {trade_id}"
        )
        send_telegram(alert)

        print(f"[Paper] OPENED {direction} {symbol} @ {entry:.4f} mode={mode} conf={confidence}%")

        return {
            "trade_id": trade_id,
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "mode": mode,
            "confidence": confidence,
            "size": position_size,
            "fee": fee,
        }

    def check_positions(self, current_prices: dict[str, float]) -> list[dict]:
        """Check all open paper positions against current prices. Close if TP/SL hit."""
        conn = _get_conn()
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
        ).fetchall()

        closed = []
        for trade in open_trades:
            trade = dict(trade)
            symbol = trade["symbol"]
            if symbol not in current_prices:
                continue

            price = current_prices[symbol]
            direction = trade["direction"]
            entry = trade["entry_price"]
            sl = trade["stop_loss"]
            tp = trade["take_profit"]
            status = trade["status"]

            # Get TP1 from metadata
            signal_row = conn.execute(
                "SELECT metadata FROM signals WHERE signal_id=?",
                (trade["signal_id"],)
            ).fetchone()
            meta = {}
            if signal_row:
                try:
                    meta = json.loads(signal_row[0] or "{}")
                except Exception:
                    pass
            tp1 = meta.get("tp1", tp)

            # Check TP/SL
            should_close = False
            trigger = ""
            exit_price = price

            if direction == "LONG":
                if price <= sl:
                    should_close, trigger = True, "SL_HIT"
                    exit_price = self.apply_slippage(sl, direction, is_entry=False)
                elif price >= tp:
                    # Full TP hit — close entire position at TP (check BEFORE TP1)
                    should_close, trigger = True, "TP_HIT"
                    exit_price = self.apply_slippage(tp, direction, is_entry=False)
                elif status == "OPEN" and price >= tp1:
                    # TP1 hit — partial close (only if price hasn't reached full TP)
                    should_close, trigger = True, "TP1_HIT"
                    exit_price = self.apply_slippage(tp1, direction, is_entry=False)
            else:  # SHORT
                if price >= sl:
                    should_close, trigger = True, "SL_HIT"
                    exit_price = self.apply_slippage(sl, direction, is_entry=False)
                elif price <= tp:
                    # Full TP hit — close entire position at TP (check BEFORE TP1)
                    should_close, trigger = True, "TP_HIT"
                    exit_price = self.apply_slippage(tp, direction, is_entry=False)
                elif status == "OPEN" and price <= tp1:
                    # TP1 hit — partial close (only if price hasn't reached full TP)
                    should_close, trigger = True, "TP1_HIT"
                    exit_price = self.apply_slippage(tp1, direction, is_entry=False)

            if should_close:
                closed_trade = self._close_position(trade, exit_price, trigger, conn, meta)
                if closed_trade:
                    closed.append(closed_trade)
            else:
                # BREAK-EVEN MANAGEMENT: Move SL to entry when price reaches 0.5R
                # This protects profits and reduces losses on reversals
                risk_dist = abs(entry - sl)
                if risk_dist > 0:
                    if direction == "LONG":
                        # Price moved up by 0.5R → move SL to entry (break-even)
                        if price >= entry + risk_dist * 0.5 and sl < entry:
                            new_sl = entry  # Break-even
                            conn.execute(
                                "UPDATE trades SET stop_loss=? WHERE trade_id=?",
                                (new_sl, trade["trade_id"])
                            )
                            print(f"[Manage] {symbol} LONG: SL moved to break-even @ {new_sl:.4f}")
                        # Trailing stop: Price moved up by 1R → trail SL at 0.5R behind
                        elif price >= entry + risk_dist and sl < entry + risk_dist * 0.5:
                            new_sl = price - risk_dist * 0.5
                            conn.execute(
                                "UPDATE trades SET stop_loss=? WHERE trade_id=?",
                                (new_sl, trade["trade_id"])
                            )
                            print(f"[Manage] {symbol} LONG: Trailing SL → {new_sl:.4f}")
                    else:  # SHORT
                        # Price moved down by 0.5R → move SL to entry
                        if price <= entry - risk_dist * 0.5 and sl > entry:
                            new_sl = entry
                            conn.execute(
                                "UPDATE trades SET stop_loss=? WHERE trade_id=?",
                                (new_sl, trade["trade_id"])
                            )
                            print(f"[Manage] {symbol} SHORT: SL moved to break-even @ {new_sl:.4f}")
                        # Trailing stop for shorts
                        elif price <= entry - risk_dist and sl > entry - risk_dist * 0.5:
                            new_sl = price + risk_dist * 0.5
                            conn.execute(
                                "UPDATE trades SET stop_loss=? WHERE trade_id=?",
                                (new_sl, trade["trade_id"])
                            )
                            print(f"[Manage] {symbol} SHORT: Trailing SL → {new_sl:.4f}")

        conn.commit()
        conn.close()
        return closed

    def _close_position(self, trade: dict, exit_price: float, trigger: str,
                        conn: sqlite3.Connection, meta: dict) -> Optional[dict]:
        """Close a paper position and update balance."""
        from positions.monitor import VALID_TRANSITIONS

        current_status = trade["status"]
        new_status = VALID_TRANSITIONS.get((current_status, trigger))
        if new_status is None:
            return None

        # Re-verify status
        live = conn.execute(
            "SELECT status FROM trades WHERE trade_id=?", (trade["trade_id"],)
        ).fetchone()
        if not live or live["status"] != current_status:
            return None

        now = datetime.now(timezone.utc).isoformat()
        entry = trade["entry_price"]
        direction = trade["direction"]
        risk_amount = self.balance * (meta.get("risk_percent", 1.0) / 100.0)

        # Calculate P&L
        if direction == "LONG":
            pnl_per_unit = exit_price - entry
        else:
            pnl_per_unit = entry - exit_price

        sl_distance = abs(entry - trade["stop_loss"])
        if sl_distance <= 0:
            return None

        position_size = risk_amount / sl_distance
        pnl = pnl_per_unit * position_size
        fee = self.calculate_fee(exit_price * position_size)
        pnl -= fee

        self.balance += pnl
        self._save_balance(conn)

        actual_r = round(pnl / risk_amount, 2) if risk_amount > 0 else 0
        result = "WIN" if pnl > 0 else "LOSS"
        pnl_percent = round((pnl / self.balance) * 100, 2) if self.balance > 0 else 0

        # Update trade
        conn.execute("""
            UPDATE trades SET
                status=?, closed_at=?, exit_price=?, exit_reason=?,
                result=?, actual_r=?, pnl_percent=?, pnl_absolute=?,
                trading_fee=?, updated_at=?
            WHERE trade_id=?
        """, (
            new_status, now, exit_price, trigger, result, actual_r,
            pnl_percent, round(pnl, 2), round(fee, 2), now, trade["trade_id"]
        ))

        conn.execute("""
            INSERT INTO state_transitions
                (trade_id, from_state, to_state, trigger, price_at_transition)
            VALUES (?, ?, ?, ?, ?)
        """, (trade["trade_id"], current_status, new_status, trigger, exit_price))

        # Send Telegram close alert
        emoji = "✅" if result == "WIN" else "❌"
        mode = meta.get("mode", "SCALP")
        mode_emoji = "⚡" if mode == "SCALP" else "📊"
        alert = (
            f"{emoji}{mode_emoji} PAPER CLOSED — {trade['symbol']}\n\n"
            f"Result: {result} | {actual_r:+.2f}R | ${pnl:+.2f}\n"
            f"Entry: {entry:,.4f} → Exit: {exit_price:,.4f}\n"
            f"Trigger: {trigger} | Mode: {mode}\n"
            f"Balance: ${self.balance:,.2f}\n\n"
            f"Reasoning was: {meta.get('llm_reasoning', 'N/A')[:100]}"
        )
        send_telegram(alert)

        print(f"[Paper] CLOSED {trade['symbol']} {result} {actual_r:+.2f}R ${pnl:+.2f} | Balance: ${self.balance:,.2f}")

        return {
            **trade,
            "status": new_status,
            "exit_price": exit_price,
            "exit_reason": trigger,
            "result": result,
            "actual_r": actual_r,
            "pnl": pnl,
            "pnl_percent": pnl_percent,
        }

    def _consecutive_sl_losses(self) -> int:
        """Count consecutive SL_HIT losses (most recent first)."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT exit_reason, result FROM trades WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        count = 0
        for r in rows:
            if r["exit_reason"] == "SL_HIT" and r["result"] == "LOSS":
                count += 1
            else:
                break
        return count

    def _last_loss_time(self) -> Optional[str]:
        """Get timestamp of the most recent SL loss."""
        conn = _get_conn()
        row = conn.execute(
            "SELECT closed_at FROM trades WHERE status='CLOSED' AND exit_reason='SL_HIT' ORDER BY closed_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def _trades_today(self) -> int:
        conn = _get_conn()
        today = date.today().isoformat()
        n = conn.execute(
            "SELECT count(*) FROM trades WHERE date(opened_at)=? AND status IN ('OPEN','PARTIAL','CLOSED')",
            (today,)
        ).fetchone()[0]
        conn.close()
        return n

    def _daily_pnl(self) -> float:
        conn = _get_conn()
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT sum(pnl_percent) FROM trades WHERE date(closed_at)=? AND status='CLOSED'",
            (today,)
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else 0.0

    def _is_already_open(self, symbol: str, direction: str) -> bool:
        conn = _get_conn()
        row = conn.execute(
            "SELECT count(*) FROM trades WHERE symbol=? AND direction=? AND status IN ('OPEN','PARTIAL')",
            (symbol, direction)
        ).fetchone()
        conn.close()
        return row[0] > 0

    def get_open_positions(self) -> list[dict]:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_performance_stats(self) -> dict:
        """Get training performance statistics."""
        conn = _get_conn()
        total = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
        wins = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED' AND result='WIN'").fetchone()[0]
        avg_r = conn.execute("SELECT avg(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0
        total_r = conn.execute("SELECT sum(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0
        conn.close()

        win_rate = (wins / total * 100) if total > 0 else 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": win_rate,
            "avg_r": avg_r,
            "total_r": total_r,
            "balance": self.balance,
            "ready_for_live": total >= LIVE_GATE_MIN_TRADES and win_rate >= LIVE_GATE_MIN_WIN_RATE,
            "trades_to_live": max(0, LIVE_GATE_MIN_TRADES - total),
            "win_rate_gap": max(0, LIVE_GATE_MIN_WIN_RATE - win_rate),
        }


    def close_stale_positions(self, current_prices: dict[str, float], max_hold_minutes: int = 25) -> list[dict]:
        """Auto-close scalp positions that have been open too long.

        Real scalpers don't hold dead trades. If price hasn't hit TP or SL
        within 25 minutes, close at market and move on.
        """
        conn = _get_conn()
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
        ).fetchall()

        closed = []
        now = datetime.now(timezone.utc)

        for trade in open_trades:
            trade = dict(trade)
            symbol = trade["symbol"]
            if symbol not in current_prices:
                continue

            # Check hold time
            opened_at_str = trade.get("opened_at", "")
            try:
                opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                hold_minutes = (now - opened_at).total_seconds() / 60
            except Exception:
                continue

            if hold_minutes < max_hold_minutes:
                continue

            # Get metadata for mode
            signal_row = conn.execute(
                "SELECT metadata FROM signals WHERE signal_id=?",
                (trade["signal_id"],)
            ).fetchone()
            meta = {}
            if signal_row:
                try:
                    meta = json.loads(signal_row[0] or "{}")
                except Exception:
                    pass

            mode = meta.get("mode", "SCALP")
            # Only auto-close scalps (swing trades get more time)
            if mode != "SCALP":
                continue

            price = current_prices[symbol]
            exit_price = self.apply_slippage(price, trade["direction"], is_entry=False)
            print(f"[Paper] STALE CLOSE: {symbol} held {hold_minutes:.0f}min — auto-closing at {exit_price:.4f}")

            closed_trade = self._close_position(trade, exit_price, "TIMEOUT", conn, meta)
            if closed_trade:
                closed.append(closed_trade)

        conn.commit()
        conn.close()
        return closed
