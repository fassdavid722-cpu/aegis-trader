"""Groq-Powered Trader Brain.

The LLM IS the trader — not just a reviewer. It looks at live market data
and decides whether to trade, what direction, entry/exit levels, and mode
(scalp vs swing). Learns from its own past trades via in-context examples.

Architecture:
  Market Data (candles, regime, funding) → Groq → Trade Decision
  Past Trade Results → In-context learning → Better future decisions
"""
from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

try:
    from groq import Groq
    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"


class GroqTrader:
    """AI trader that makes real trading decisions using Groq LLM."""

    MODEL = "llama-3.3-70b-versatile"

    # Session windows for scalping (wider than before)
    SCALP_SESSIONS = {
        "LONDON": (7, 11),    # 07:00-11:00 UTC
        "NY": (13, 17),       # 13:00-17:00 UTC
        "OVERLAP": (13, 15),  # Highest volume
    }

    def __init__(self) -> None:
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.client = None
        if HAS_GROQ and self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                print(f"[GroqTrader] Init error: {e}")

    @property
    def available(self) -> bool:
        return self.client is not None

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_session(self) -> tuple[str, bool]:
        """Check if we're in a scalping session."""
        hour = datetime.now(timezone.utc).hour
        for name, (start, end) in self.SCALP_SESSIONS.items():
            if start <= hour < end:
                return name, True
        return "OFF_HOURS", False

    def _load_recent_trades(self, limit: int = 15) -> list[dict]:
        """Load recent closed trades for in-context learning."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT t.symbol, t.direction, t.result, t.actual_r,
                   t.market_regime, t.confidence_score, t.exit_reason,
                   t.opened_at, t.closed_at, t.entry_price, t.exit_price,
                   t.stop_loss, t.take_profit,
                   s.metadata
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.signal_id
            WHERE t.status = 'CLOSED'
            ORDER BY t.closed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _load_trade_stats(self) -> dict:
        """Load aggregate trading stats."""
        conn = self._get_conn()
        total = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
        wins = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED' AND result='WIN'").fetchone()[0]
        losses = total - wins
        avg_r = conn.execute("SELECT avg(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0
        total_r = conn.execute("SELECT sum(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0
        conn.close()
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "avg_r": avg_r,
            "total_r": total_r,
        }

    def _format_candles(self, candles: list[dict], max_bars: int = 40) -> str:
        """Format candle data for the LLM prompt."""
        if not candles:
            return "No data"
        recent = candles[-max_bars:]
        lines = []
        for c in recent:
            t = c.get("timestamp", "")
            if isinstance(t, str):
                t = t[-8:-3]  # HH:MM
            lines.append(
                f"  {t} | O:{c['open']:.4f} H:{c['high']:.4f} "
                f"L:{c['low']:.4f} C:{c['close']:.4f} V:{c.get('volume',0):.0f}"
            )
        return "\n".join(lines)

    def _format_recent_trades(self, trades: list[dict]) -> str:
        """Format recent trade history for learning context."""
        if not trades:
            return "No trade history yet — this is your first session."
        lines = []
        for t in trades[:10]:
            result = t.get("result", "?")
            r = t.get("actual_r", 0) or 0
            symbol = t.get("symbol", "?")
            direction = t.get("direction", "?")
            regime = t.get("market_regime", "?")
            exit_reason = t.get("exit_reason", "?")
            confidence = t.get("confidence_score", 0) or 0

            # Load metadata for reasoning
            meta = {}
            try:
                meta = json.loads(t.get("metadata", "{}") or "{}")
            except Exception:
                pass
            reasoning = meta.get("llm_reasoning", "")[:100]

            icon = "✅" if result == "WIN" else "❌"
            lines.append(
                f"  {icon} {symbol} {direction} → {result} ({r:+.1f}R) "
                f"[{regime}] conf={confidence:.0f}% exit={exit_reason} — {reasoning}"
            )
        return "\n".join(lines)

    def _build_trading_prompt(
        self,
        symbol: str,
        candles_5m: list[dict],
        candles_15m: list[dict],
        funding_rate: Optional[float],
        current_price: float,
        session: str,
        recent_trades: list[dict],
        stats: dict,
        open_positions: list[dict],
    ) -> str:
        """Build the full trading decision prompt."""

        # Calculate some quick stats from candles
        if candles_5m and len(candles_5m) >= 10:
            last_10 = candles_5m[-10:]
            highs = [c["high"] for c in last_10]
            lows = [c["low"] for c in last_10]
            range_high = max(highs)
            range_low = min(lows)
            current_range_pct = ((range_high - range_low) / range_low) * 100
            avg_volume = sum(c.get("volume", 0) for c in last_10) / 10
            last_volume = candles_5m[-1].get("volume", 0)
            vol_surge = (last_volume / avg_volume) if avg_volume > 0 else 1.0
        else:
            range_high = range_low = current_price
            current_range_pct = 0
            vol_surge = 1.0

        # 15min trend context
        if candles_15m and len(candles_15m) >= 5:
            last_5 = candles_15m[-5:]
            trend_closes = [c["close"] for c in last_5]
            trending_up = trend_closes[-1] > trend_closes[0]
            trend_text = f"{'↗ BULLISH' if trending_up else '↘ BEARISH'} (5-bar close: {trend_closes[0]:.2f} → {trend_closes[-1]:.2f})"
        else:
            trend_text = "Insufficient data"

        open_pos_text = "None"
        if open_positions:
            open_pos_text = "\n".join(
                f"  {p['symbol']} {p['direction']} @ {p['entry_price']} SL={p['stop_loss']} TP={p['take_profit']}"
                for p in open_positions
            )

        return f"""You are Aegis, an elite crypto scalper and intraday trader on Bitget futures.
Your job: analyze live market data and decide whether to take a trade RIGHT NOW.

TRADING STYLE:
- PRIMARY: Scalping — quick 5-30 min holds, tight SL (0.2-0.4%), small TP (0.3-0.7%)
- HYBRID: When you see a high-conviction setup (strong trend + zone + structure), take a swing trade with wider TP (1-3R)
- You are a TRADER, not a signal bot. Think about market psychology, order flow, manipulation.

CURRENT MARKET — {symbol}:
- Price: {current_price:.4f}
- Session: {session}
- Funding rate: {funding_rate:.6f}" if funding_rate is not None else "N/A"
- 5min range (last 10 bars): {range_low:.4f} - {range_high:.4f} ({current_range_pct:.2f}%)
- Volume surge: {vol_surge:.2f}x avg
- 15min trend: {trend_text}

5-MINUTE CANDLES (most recent {min(len(candles_5m), 40)}):
{self._format_candles(candles_5m, 40)}

15-MINUTE CANDLES (trend context, last 10):
{self._format_candles(candles_15m, 10)}

YOUR PAST TRADES (learn from these):
{self._format_recent_trades(recent_trades)}

YOUR STATS:
- Total trades: {stats['total']} | Win rate: {stats['win_rate']:.0f}% | Avg R: {stats['avg_r']:.2f} | Total R: {stats['total_r']:.2f}

OPEN POSITIONS:
{open_pos_text}

INSTRUCTIONS:
1. Analyze the price action — look for: momentum shifts, support/resistance rejections, volume spikes, squeeze setups, BOS/CHOCH
2. Consider your past trade results — what's working? What's not?
3. Decide: TRADE or NO_TRADE
4. If TRADE: pick mode (SCALP or SWING), set entry/SL/TP based on recent price structure

Respond in EXACTLY this JSON format (no markdown, no code fences):
{{
  "decision": "TRADE" or "NO_TRADE",
  "mode": "SCALP" or "SWING",
  "symbol": "{symbol}",
  "direction": "LONG" or "SHORT",
  "entry": <float>,
  "stop_loss": <float>,
  "take_profit_1": <float>,
  "take_profit_2": <float>,
  "confidence": <int 0-100>,
  "risk_percent": <float 0.3-2.0>,
  "reasoning": "<2-3 sentences explaining your analysis>",
  "what_you_see": "<brief description of the price action pattern>"
}}"""


    def make_trading_decision(
        self,
        symbol: str,
        candles_5m: list[dict],
        candles_15m: list[dict],
        funding_rate: Optional[float],
        current_price: float,
        open_positions: list[dict] = None,
    ) -> Optional[dict[str, Any]]:
        """Ask Groq to make a trading decision for this symbol."""
        if not self.available:
            print("[GroqTrader] Groq not available")
            return None

        session, is_active = self.get_session()
        if not is_active:
            return None  # Don't trade outside sessions

        recent_trades = self._load_recent_trades(15)
        stats = self._load_trade_stats()
        if open_positions is None:
            open_positions = []

        # Check if already holding this symbol
        already_holding = any(p.get("symbol") == symbol for p in open_positions)
        if already_holding:
            return None  # Don't add to existing position

        prompt = self._build_trading_prompt(
            symbol=symbol,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            funding_rate=funding_rate,
            current_price=current_price,
            session=session,
            recent_trades=recent_trades,
            stats=stats,
            open_positions=open_positions,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Aegis, an elite crypto scalper. You make precise, "
                            "data-driven trading decisions. You are disciplined: you skip "
                            "bad setups without hesitation. You learn from your mistakes. "
                            "Always respond with valid JSON only — no markdown, no commentary."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=600,
                timeout=30,
            )

            raw = response.choices[0].message.content.strip()
            # Clean markdown fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            decision = json.loads(raw)

            # Validate required fields
            if decision.get("decision") == "NO_TRADE":
                print(f"[GroqTrader] {symbol}: NO_TRADE — {decision.get('reasoning', '')[:80]}")
                return None

            # Validate trade decision has required fields
            required = ["direction", "entry", "stop_loss", "take_profit_1", "take_profit_2"]
            for field in required:
                if field not in decision or decision[field] is None:
                    print(f"[GroqTrader] {symbol}: missing {field}")
                    return None

            # Sanitize numeric fields
            for field in ["entry", "stop_loss", "take_profit_1", "take_profit_2"]:
                decision[field] = float(decision[field])
            decision["confidence"] = int(decision.get("confidence", 70))
            decision["risk_percent"] = float(decision.get("risk_percent", 1.0))
            decision["symbol"] = symbol
            decision["session"] = session

            print(f"[GroqTrader] {symbol}: {decision['direction']} {decision.get('mode','SCALP')} "
                  f"@ {decision['entry']:.4f} conf={decision['confidence']}% — {decision.get('reasoning','')[:60]}")

            return decision

        except json.JSONDecodeError as e:
            print(f"[GroqTrader] {symbol}: JSON parse error: {e}")
            return None
        except Exception as e:
            print(f"[GroqTrader] {symbol}: error: {e}")
            return None

    def review_own_trade(self, trade: dict) -> dict[str, Any]:
        """Groq reviews its own closed trade — the learning loop."""
        if not self.available:
            return {"summary": "Groq unavailable", "lessons": []}

        stats = self._load_trade_stats()
        recent = self._load_recent_trades(10)

        meta = {}
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT metadata FROM signals WHERE signal_id=?",
                (trade.get("signal_id", ""),)
            ).fetchone()
            conn.close()
            if row:
                meta = json.loads(row[0] or "{}")
        except Exception:
            pass

        prompt = f"""You are Aegis, reviewing your own closed trade. Be honest and learn from it.

TRADE RESULT:
- Symbol: {trade.get('symbol')} | Direction: {trade.get('direction')}
- Result: {trade.get('result')} | Exit: {trade.get('exit_reason')}
- Entry: {trade.get('entry_price')} → Exit: {trade.get('exit_price')}
- R-multiple: {trade.get('actual_r', 0):.2f}R
- Confidence was: {trade.get('confidence_score', 0)}%
- Your reasoning at entry: {meta.get('llm_reasoning', 'N/A')}
- What you saw: {meta.get('llm_observation', 'N/A')}

YOUR STATS: {stats['total']} trades, {stats['win_rate']:.0f}% win rate, {stats['avg_r']:.2f} avg R
RECENT: {self._format_recent_trades(recent)}

Be brutally honest. What did you get right? What did you get wrong? What pattern should you remember?

Respond in JSON:
{{
  "summary": "one-line summary",
  "what_went_right": "what you got correct",
  "what_went_wrong": "what you missed or got wrong",
  "lesson": "the key takeaway to remember",
  "should_repeat": true/false,
  "confidence_adjustment": <-5 to +5, how to adjust future confidence for similar setups>
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "You are a self-aware trading AI learning from its decisions. Respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=500,
                timeout=30,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            review = json.loads(raw)

            # Save to journal
            self._save_review(trade["trade_id"], review, meta)

            return review

        except Exception as e:
            print(f"[GroqTrader] Review error: {e}")
            return {"summary": "review failed", "lesson": "N/A"}

    def _save_review(self, trade_id: str, review: dict, original_meta: dict) -> None:
        """Save the self-review to the trade analysis table."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        lessons = [review.get("lesson", "")]
        if review.get("what_went_right"):
            lessons.append(f"Right: {review['what_went_right']}")
        if review.get("what_went_wrong"):
            lessons.append(f"Wrong: {review['what_went_wrong']}")

        summary = review.get("summary", "")
        quality = "valid" if review.get("should_repeat", True) else "poor"

        conn.execute("""
            INSERT OR REPLACE INTO trade_analysis
                (trade_id, summary, trade_quality, regime_quality,
                 execution_quality, lessons, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, summary, quality, "neutral", "average",
            json.dumps(lessons), 70, now,
        ))
        conn.commit()
        conn.close()

    def weekly_review(self) -> str:
        """Generate a comprehensive weekly performance review."""
        if not self.available:
            return "Groq unavailable for review"

        stats = self._load_trade_stats()
        recent = self._load_recent_trades(30)

        if stats["total"] == 0:
            return "No trades to review yet."

        prompt = f"""You are Aegis, reviewing your weekly trading performance.

STATS: {stats['total']} trades | {stats['win_rate']:.0f}% win rate | {stats['avg_r']:.2f} avg R | {stats['total_r']:.2f} total R

RECENT TRADES:
{self._format_recent_trades(recent)}

Provide a detailed weekly review:
1. Overall performance assessment
2. What's working well
3. What needs improvement
4. Specific adjustments for next week (confidence levels, session focus, risk sizing)
5. Key lesson learned

Be concise but specific."""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "You are a self-aware trading AI doing a weekly review. Be honest and specific."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=800,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Weekly review error: {e}"
