"""Groq-Powered Trader Brain v2 — The Hunter.

This is not an analyst. This is a trader. A hungry scalper who:
- Takes 5-10+ trades per session (not 0)
- Hunts momentum bursts, volume surges, level reactions
- Sizes risk appropriately but ACTS when edge appears
- Learns from every trade — gets more aggressive as it finds what works
- Knows that sitting out all session = not eating

The LLM receives pre-processed market intelligence (not raw candles),
so it can make fast, decision-ready calls like a real trader.
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

from analyst.market_intelligence import build_intelligence, MarketIntelligence
from analyst.regime_detector_v2 import detect_regime, should_trade_against_regime, MarketRegime

DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"


class GroqTrader:
    """AI trader with a scalper's hunger and a risk manager's discipline."""

    MODEL = "llama-3.3-70b-versatile"

    # Wider session windows for scalping
    SESSIONS = {
        "LONDON": (7, 11),     # 07:00-11:00 UTC
        "NY": (13, 17),        # 13:00-17:00 UTC
    }

    # Confidence threshold drops as hunger increases
    BASE_CONFIDENCE = 55  # Start lower — take more trades
    HUNGER_CONFIDENCE_DROP = 10  # After 30 min no trades, threshold drops by 10

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

    def get_session(self) -> tuple[str, bool, float]:
        """Returns (session_name, is_active, progress 0-1)."""
        hour = datetime.now(timezone.utc)
        h = hour.hour
        m = hour.minute

        for name, (start, end) in self.SESSIONS.items():
            if start <= h < end:
                progress = (h - start + m / 60) / (end - start)
                return name, True, progress
        return "OFF_HOURS", False, 0.0

    def _load_recent_trades(self, limit: int = 15) -> list[dict]:
        """Load recent closed trades for learning."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT t.symbol, t.direction, t.result, t.actual_r,
                   t.market_regime, t.confidence_score, t.exit_reason,
                   t.opened_at, t.closed_at, t.entry_price, t.exit_price,
                   t.stop_loss, t.take_profit, s.metadata
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.signal_id
            WHERE t.status = 'CLOSED'
            ORDER BY t.closed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _load_stats(self) -> dict:
        """Load aggregate trading stats."""
        conn = self._get_conn()
        total = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
        wins = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED' AND result='WIN'").fetchone()[0]
        avg_r = conn.execute("SELECT avg(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0
        total_r = conn.execute("SELECT sum(actual_r) FROM trades WHERE status='CLOSED'").fetchone()[0] or 0

        # Today's trades
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_trades = conn.execute(
            "SELECT count(*) FROM trades WHERE date(opened_at)=? AND status IN ('OPEN','PARTIAL','CLOSED')",
            (today,)
        ).fetchone()[0]

        # Open positions count
        open_count = conn.execute(
            "SELECT count(*) FROM trades WHERE status IN ('OPEN','PARTIAL')"
        ).fetchone()[0]

        # Mode-specific stats
        scalp_wins = conn.execute("""
            SELECT count(*) FROM trades WHERE status='CLOSED' AND result='WIN'
            AND market_regime='SCALP'
        """).fetchone()[0]
        scalp_total = conn.execute("""
            SELECT count(*) FROM trades WHERE status='CLOSED' AND market_regime='SCALP'
        """).fetchone()[0]

        conn.close()

        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "avg_r": avg_r,
            "total_r": total_r,
            "today_trades": today_trades,
            "open_positions": open_count,
            "scalp_win_rate": (scalp_wins / scalp_total * 100) if scalp_total > 0 else 0,
            "scalp_total": scalp_total,
        }

    def _load_lessons(self) -> list[str]:
        """Load recent lessons from trade analysis."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT lessons FROM trade_analysis
            ORDER BY created_at DESC LIMIT 5
        """).fetchall()
        conn.close()
        lessons = []
        for r in rows:
            try:
                items = json.loads(r[0] or "[]")
                for item in items:
                    if isinstance(item, str) and len(item) < 150:
                        lessons.append(item)
            except Exception:
                pass
        return lessons[:8]  # Last 8 lessons

    def _format_recent_trades(self, trades: list[dict]) -> str:
        """Format trade history for learning context."""
        if not trades:
            return "No trades yet — this is your first session. Time to hunt."
        lines = []
        for t in trades[:8]:
            result = t.get("result", "?")
            r = t.get("actual_r", 0) or 0
            symbol = t.get("symbol", "?")
            direction = t.get("direction", "?")
            exit_reason = t.get("exit_reason", "?")
            confidence = t.get("confidence_score", 0) or 0
            meta = {}
            try:
                meta = json.loads(t.get("metadata", "{}") or "{}")
            except Exception:
                pass
            mode = meta.get("mode", "?")
            reasoning = meta.get("llm_reasoning", "")[:80]
            icon = "✅" if result == "WIN" else "❌"
            lines.append(
                f"  {icon} {symbol} {direction} ({mode}) → {result} {r:+.1f}R "
                f"[{exit_reason}] conf={confidence:.0f}% — {reasoning}"
            )
        return "\n".join(lines)

    def _format_lessons(self, lessons: list[str]) -> str:
        """Format lessons for the prompt."""
        if not lessons:
            return "No lessons learned yet."
        return "\n".join(f"  • {l}" for l in lessons)


    def _build_pattern_insights(self) -> str:
        """Generate data-driven insights from trade history. These are FACTS, not opinions."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT direction, result, actual_r, exit_reason, market_regime
            FROM trades WHERE status='CLOSED'
            ORDER BY closed_at DESC LIMIT 20
        """).fetchall()
        conn.close()

        if len(rows) < 3:
            return "Not enough trades for pattern analysis yet."

        longs = [r for r in rows if r["direction"] == "LONG"]
        shorts = [r for r in rows if r["direction"] == "SHORT"]
        long_wins = [r for r in longs if r["result"] == "WIN"]
        short_wins = [r for r in shorts if r["result"] == "WIN"]
        long_r = sum(r["actual_r"] or 0 for r in longs)
        short_r = sum(r["actual_r"] or 0 for r in shorts)

        insights = []

        # Direction bias
        if len(longs) >= 2:
            lr = len(long_wins) / len(longs) * 100
            insights.append(f"LONGS: {len(long_wins)}/{len(longs)} wins ({lr:.0f}% WR), {long_r:+.1f}R total")
        if len(shorts) >= 2:
            sr = len(short_wins) / len(shorts) * 100
            insights.append(f"SHORTS: {len(short_wins)}/{len(shorts)} wins ({sr:.0f}% WR), {short_r:+.1f}R total")

        # Strong directional bias warning
        if len(shorts) >= 3 and len(short_wins) == 0:
            insights.append("🚨 CRITICAL: 0% win rate on shorts. STOP shorting unless overwhelming bearish evidence.")
        if len(longs) >= 3 and len(long_wins) == 0:
            insights.append("🚨 CRITICAL: 0% win rate on longs. STOP going long unless overwhelming bullish evidence.")

        if len(shorts) >= 3 and len(short_wins) / len(shorts) < 0.3 and len(longs) >= 2 and len(long_wins) / len(longs) > 0.5:
            insights.append("📊 PATTERN: Longs are profitable, shorts are bleeding. BIAS TO LONG unless strong bearish regime.")

        if len(longs) >= 3 and len(long_wins) / len(longs) < 0.3 and len(shorts) >= 2 and len(short_wins) / len(shorts) > 0.5:
            insights.append("📊 PATTERN: Shorts are profitable, longs are bleeding. BIAS TO SHORT unless strong bullish regime.")

        # Streak detection
        recent_5 = rows[:5]
        recent_losses = sum(1 for r in recent_5 if r["result"] == "LOSS")
        if recent_losses >= 4:
            insights.append(f"⚠️ SLUMP: {recent_losses}/5 recent trades lost. Be more selective — only A+ setups.")

        # Exit reason patterns
        sl_hits = [r for r in rows if r["exit_reason"] == "SL_HIT"]
        tp_hits = [r for r in rows if r["exit_reason"] == "TP_HIT"]
        if len(sl_hits) > len(tp_hits) * 2:
            insights.append(f"📐 SL hit {len(sl_hits)}x vs TP hit {len(tp_hits)}x. Your entries or SL placement is off.")

        if not insights:
            return "No strong patterns detected yet."

        return "\n".join(f"  📌 {i}" for i in insights)


    def _check_direction_bias(self, direction: str, confidence: int) -> str:
        """Hard block on directions with terrible track records. Returns block reason or empty string."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT direction, result FROM trades
            WHERE status='CLOSED' AND direction=?
            ORDER BY closed_at DESC LIMIT 10
        """, (direction,)).fetchall()
        conn.close()

        if len(rows) < 5:
            return ""  # Not enough data to block

        wins = sum(1 for r in rows if r["result"] == "WIN")
        wr = wins / len(rows) * 100

        # If 0% WR with 5+ trades and confidence < 80%, block it
        if wr == 0 and confidence < 80:
            return f"{direction} has 0% WR over {len(rows)} trades. Need 80%+ confidence to override."

        # If <20% WR with 5+ trades and confidence < 70%, block it
        if wr < 20 and confidence < 70:
            return f"{direction} has {wr:.0f}% WR over {len(rows)} trades. Need 70%+ confidence to override."

        return ""


    def _build_hunger_context(self, stats: dict, session: str, session_progress: float) -> str:
        """Build context about how hungry the trader should be."""
        today = stats["today_trades"]
        open_pos = stats["open_positions"]

        if today == 0 and session_progress > 0.25:
            return ("⚠️ HUNGER ALERT: You're 25%+ into the session with ZERO trades taken. "
                    "A real scalper doesn't sit on their hands this long. "
                    "If there's even a 55% edge, take it. Small wins compound. "
                    "Stop waiting for the perfect setup — it rarely comes.")
        elif today == 0:
            return ("Session just started. Scan aggressively — look for momentum bursts "
                    "and volume surges. First trade sets the tone.")
        elif today < 3:
            return (f"You've taken {today} trade(s) this session. "
                    f"Good start but a scalper takes 5-10+. Keep hunting.")
        elif today < 6:
            return f"{today} trades today. You're in the zone — keep finding edges."
        else:
            return f"{today} trades today. You're active. Maintain discipline on risk."

    def _build_prompt(
        self,
        intel: MarketIntelligence,
        funding_rate: Optional[float],
        stats: dict,
        recent_trades: list[dict],
        lessons: list[str],
        session: str,
        session_progress: float,
        open_positions: list[dict],
        market_regime: Optional[Any] = None,
    ) -> str:
        """Build the hunter prompt."""

        regime_briefing = market_regime.to_briefing() + "\n" if market_regime else ""

        hunger = self._build_hunger_context(stats, session, session_progress)

        open_pos_text = "None" if not open_positions else "\n".join(
            f"  {p['symbol']} {p['direction']} @ {p.get('entry_price', p.get('entry', 0)):.4f} "
            f"SL={p['stop_loss']:.4f} TP={p.get('take_profit', p.get('take_profit_2', 0)):.4f}"
            for p in open_positions
        )

        funding_text = f"{funding_rate:.6f}" if funding_rate is not None else "N/A"

        briefing = intel.to_briefing()

        # ATR-based SL suggestion
        atr_sl_scalp = intel.atr_5m * 1.2 if intel.atr_5m > 0 else intel.current_price * 0.003
        atr_sl_swing = intel.atr_15m * 1.5 if intel.atr_15m > 0 else intel.current_price * 0.008
        scalp_sl_pct = (atr_sl_scalp / intel.current_price) * 100
        swing_sl_pct = (atr_sl_swing / intel.current_price) * 100

        return f"""You are Aegis — a hungry, elite crypto scalper on Bitget futures.

YOU ARE A TRADER, NOT AN ANALYST.
- You make your living scalping. Sitting out all session = you don't eat.
- You take trades with 55%+ edge. You don't wait for 90% setups — they don't exist.
- You hunt momentum bursts, volume surges, level reactions, and structure breaks.
- You manage risk on every trade but you ACT when edge appears.
- Missing a good trade bothers you MORE than a small loss.
- You scalp primarily (5-30 min holds). On rare high-conviction setups, you swing.
- Every loss is a lesson. Every win confirms your read. You get sharper all session.

SCALPING PARAMETERS:
- SL: {scalp_sl_pct:.3f}% ({atr_sl_scalp:.4f} — 1.2x ATR)
- TP1: 0.3-0.7% (quick partial)
- TP2: 0.5-1.0% (full exit)
- Risk: 0.5-1.5% of account per trade
- Max hold: 30 min (if price stalls, close and move on)

SWING PARAMETERS (only for high-conviction):
- SL: {swing_sl_pct:.3f}% ({atr_sl_swing:.4f} — 1.5x 15min ATR)
- TP1: 1.0-1.5R
- TP2: 2.0-3.0R
- Risk: 1.0-2.0% of account

{briefing}

FUNDING RATE: {funding_text}

{hunger}

YOUR TRACK RECORD:
- Total: {stats['total']} trades | Win rate: {stats['win_rate']:.0f}% | Avg R: {stats['avg_r']:.2f} | Total R: {stats['total_r']:.2f}
- Today: {stats['today_trades']} trades | Open: {stats['open_positions']}
- Scalp win rate: {stats['scalp_win_rate']:.0f}% ({stats['scalp_total']} trades)

DATA-DRIVEN INSIGHTS (these are FACTS from your trade history — obey them):
{self._build_pattern_insights()}

RECENT TRADES:
{self._format_recent_trades(recent_trades)}

LESSONS YOU'VE LEARNED:
{self._format_lessons(lessons)}

⚠️ ADAPTIVE RULES (based on your track record):
- If your SHORT WR is below 30%, you need 75%+ confidence to take a short. No exceptions.
- If your LONG WR is below 30%, you need 75%+ confidence to take a long. No exceptions.
- If one direction is clearly winning and the other is losing, bias toward the winning direction.
- These rules OVERRIDE your hunger. A hungry trader who keeps losing isn't hungry — they're reckless.

OPEN POSITIONS:
{open_pos_text}

YOUR DECISION:
Look at the pre-processed signals above. If signal_strength >= 4, you should probably trade.
If there's a pattern + volume + momentum alignment, that's your edge. Take it.

{regime_briefing}

Choose TRADE only if you can articulate: (1) what edge you see, (2) where your SL goes and why,
(3) where price should go and why. If you can't answer all three, don't trade.

Respond in EXACTLY this JSON (no markdown, no code fences):
{{
  "decision": "TRADE" or "NO_TRADE",
  "mode": "SCALP" or "SWING",
  "symbol": "{intel.symbol}",
  "direction": "LONG" or "SHORT",
  "entry": {intel.current_price},
  "stop_loss": <price>,
  "take_profit_1": <price>,
  "take_profit_2": <price>,
  "confidence": <int 50-95>,
  "risk_percent": <float 0.3-2.0>,
  "reasoning": "<what edge you see, 2-3 sentences>",
  "what_you_see": "<the specific price action pattern>",
  "invalidation": "<what would prove you wrong>"
}}"""

    def make_trading_decision(
        self,
        symbol: str,
        candles_5m: list[dict],
        candles_15m: list[dict],
        funding_rate: Optional[float],
        open_positions: list[dict] = None,
        market_regime: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        """Make a trading decision using market intelligence + Groq."""
        if not self.available:
            print(f"[GroqTrader] {symbol}: Groq unavailable")
            return None

        session, is_active, progress = self.get_session()
        if not is_active:
            return None

        # Already holding this symbol?
        if open_positions and any(p.get("symbol") == symbol for p in open_positions):
            return None

        # Build market intelligence
        intel = build_intelligence(
            symbol=symbol,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            funding_rate=funding_rate,
            session=session,
            session_progress=progress,
        )

        # Quick filter: if signal strength is 0 and volume is dry, skip the LLM call
        if intel.signal_strength == 0 and intel.volume_status == "DRY":
            print(f"[GroqTrader] {symbol}: No signals + dry volume — skip LLM call")
            return None

        stats = self._load_stats()
        recent = self._load_recent_trades(15)
        lessons = self._load_lessons()

        prompt = self._build_prompt(
            intel, funding_rate, stats, recent, lessons,
            session, progress, open_positions or [], market_regime,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Aegis, an elite crypto scalper. You are HUNGRY and DECISIVE. "
                            "You take 5-10+ trades per session. You don't sit on your hands. "
                            "You manage risk but you ACT when edge appears. "
                            "You learn from every trade and get sharper all session. "
                            "ALWAYS respond with valid JSON only — no markdown, no commentary."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,  # Slightly higher for more creative trade-finding
                max_tokens=600,
                timeout=30,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            decision = json.loads(raw)

            if decision.get("decision") == "NO_TRADE":
                reason = decision.get("reasoning", "no reason given")[:80]
                print(f"[GroqTrader] {symbol}: NO_TRADE — {reason}")
                return None

            # Validate
            required = ["direction", "entry", "stop_loss", "take_profit_1", "take_profit_2"]
            for field in required:
                if field not in decision or decision[field] is None:
                    print(f"[GroqTrader] {symbol}: missing {field}")
                    return None

            # Sanitize
            for field in ["entry", "stop_loss", "take_profit_1", "take_profit_2"]:
                decision[field] = float(decision[field])
            decision["confidence"] = int(decision.get("confidence", 65))
            decision["risk_percent"] = float(decision.get("risk_percent", 1.0))
            decision["symbol"] = symbol
            decision["session"] = session

            # Validate SL makes sense
            sl_dist = abs(decision["entry"] - decision["stop_loss"])
            sl_pct = (sl_dist / decision["entry"]) * 100
            if sl_pct > 2.0:
                print(f"[GroqTrader] {symbol}: SL too wide ({sl_pct:.2f}%) — adjusting")
                if decision["direction"] == "LONG":
                    decision["stop_loss"] = decision["entry"] * 0.995  # 0.5% SL
                else:
                    decision["stop_loss"] = decision["entry"] * 1.005

            # Check against market regime
            if market_regime is not None:
                allowed, regime_reason = should_trade_against_regime(market_regime, decision["direction"], decision["confidence"])
                if not allowed:
                    print(f"[GroqTrader] {symbol}: BLOCKED by regime — {regime_reason}")
                    return None

            # HARD BLOCK: Check direction bias from trade history
            # If a direction has 0% WR with 5+ attempts, block it unless confidence is 80%+
            bias_block = self._check_direction_bias(decision["direction"], decision["confidence"])
            if bias_block:
                print(f"[GroqTrader] {symbol}: BLOCKED by direction bias — {bias_block}")
                return None

            mode = decision.get("mode", "SCALP")
            emoji = "🟢" if decision["direction"] == "LONG" else "🔴"
            print(f"[GroqTrader] {symbol}: {emoji} {decision['direction']} {mode} "
                  f"@ {decision['entry']:.4f} conf={decision['confidence']}% "
                  f"SL={decision['stop_loss']:.4f} TP1={decision['take_profit_1']:.4f}")

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

        stats = self._load_stats()
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

        prompt = f"""You are Aegis, reviewing your own closed trade. Be brutally honest.

TRADE: {trade.get('symbol')} {trade.get('direction')} ({meta.get('mode', '?')})
Result: {trade.get('result')} | {trade.get('actual_r', 0):.2f}R | Exit: {trade.get('exit_reason')}
Entry: {trade.get('entry_price')} → Exit: {trade.get('exit_price')}
Your reasoning: {meta.get('llm_reasoning', 'N/A')}
What you saw: {meta.get('llm_observation', 'N/A')}

Stats: {stats['total']} trades, {stats['win_rate']:.0f}% WR, {stats['avg_r']:.2f} avg R

What did you get right? What did you miss? What should you remember next time?

JSON only:
{{
  "summary": "one line",
  "what_went_right": "...",
  "what_went_wrong": "...",
  "lesson": "key takeaway",
  "should_repeat": true/false,
  "confidence_adjustment": <-5 to +5>
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "Self-aware trading AI. JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=400,
                timeout=30,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            review = json.loads(raw)
            self._save_review(trade["trade_id"], review)
            return review

        except Exception as e:
            print(f"[GroqTrader] Review error: {e}")
            return {"summary": "review failed", "lesson": "N/A"}

    def _save_review(self, trade_id: str, review: dict) -> None:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        lessons = [review.get("lesson", "")]
        if review.get("what_went_right"):
            lessons.append(f"Right: {review['what_went_right']}")
        if review.get("what_went_wrong"):
            lessons.append(f"Wrong: {review['what_went_wrong']}")

        conn.execute("""
            INSERT OR REPLACE INTO trade_analysis
                (trade_id, summary, trade_quality, regime_quality,
                 execution_quality, lessons, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, review.get("summary", ""),
            "valid" if review.get("should_repeat", True) else "poor",
            "neutral", "average",
            json.dumps(lessons), 70, now,
        ))
        conn.commit()
        conn.close()

    def weekly_review(self) -> str:
        """Comprehensive weekly review for the check-in automations."""
        if not self.available:
            return "Groq unavailable"

        stats = self._load_stats()
        recent = self._load_recent_trades(30)

        if stats["total"] == 0:
            return "No trades to review yet."

        prompt = f"""Weekly trading review.

Stats: {stats['total']} trades | {stats['win_rate']:.0f}% WR | {stats['avg_r']:.2f} avg R | {stats['total_r']:.2f} total R
Scalp: {stats['scalp_win_rate']:.0f}% WR ({stats['scalp_total']} trades)

Recent:
{self._format_recent_trades(recent)}

Provide: 1) Performance assessment 2) What's working 3) What to improve 4) Next week adjustments"""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "Self-aware trading AI doing weekly review."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=800,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {e}"
