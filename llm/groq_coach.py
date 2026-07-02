"""Groq-powered Coach Engine.

Uses Groq LLM (llama-3.3-70b) for post-trade analysis.
Replaces heuristic coach with actual AI reasoning.
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


class GroqCoach:
    """AI-powered trade coach using Groq LLM."""

    MODEL = "llama-3.3-70b-versatile"

    def __init__(self) -> None:
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.client = None
        if HAS_GROQ and self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                print(f"[GroqCoach] Init error: {e}")

    @property
    def available(self) -> bool:
        return self.client is not None

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _load_trade_history(self, limit: int = 20) -> list[dict]:
        """Load recent closed trades for pattern context."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT symbol, direction, result, actual_r, market_regime,
                   confidence_score, exit_reason, opened_at, closed_at
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY closed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _load_signal_metadata(self, signal_id: str) -> dict:
        """Load the original signal metadata for full context."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT metadata FROM signals WHERE signal_id = ?",
            (signal_id,)
        ).fetchone()
        conn.close()
        if row:
            try:
                return json.loads(row[0] or "{}")
            except Exception:
                return {}
        return {}

    def _build_prompt(self, trade: dict, meta: dict, history: list[dict]) -> str:
        """Build the coaching prompt with full trade context."""
        direction = trade.get("direction", "LONG")
        result = trade.get("result", "LOSS")
        exit_reason = trade.get("exit_reason", "UNKNOWN")
        entry = trade.get("entry_price", 0)
        exit_p = trade.get("exit_price", 0)
        symbol = trade.get("symbol", "?")
        confidence = trade.get("confidence_score", 0)
        regime = trade.get("market_regime", "UNKNOWN")
        actual_r = trade.get("actual_r", 0)

        # Duration
        opened_at = trade.get("opened_at", "")
        closed_at = trade.get("closed_at", "")
        try:
            t0 = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            duration_min = int((t1 - t0).total_seconds() / 60)
            duration = f"{duration_min}m" if duration_min < 60 else f"{duration_min//60}h {duration_min%60}m"
        except Exception:
            duration = "unknown"

        thesis = meta.get("thesis", "N/A")
        session = meta.get("session", "UNKNOWN")
        funding = meta.get("funding_bias", "NEUTRAL")
        bd = meta.get("confidence_breakdown", {})
        bd_text = ""
        if bd and bd.get("factors"):
            for f in bd["factors"]:
                sign = "+" if f["score"] >= 0 else ""
                bd_text += f"  {f['name']}: {sign}{f['score']:.0f} — {f['reason']}\n"

        # Recent history summary
        hist_text = ""
        if history:
            wins = sum(1 for h in history if h["result"] == "WIN")
            losses = len(history) - wins
            win_rate = wins / len(history) * 100 if history else 0
            hist_text = f"\nRecent {len(history)} trades: {wins}W/{losses}L ({win_rate:.0f}% win rate)\n"
            for h in history[:5]:
                hist_text += f"  {h['symbol']} {h['direction']} → {h['result']} ({h.get('actual_r', 0):.1f}R) [{h.get('market_regime','?')}]\n"

        return f"""You are Aegis, an elite crypto trading coach. Analyze this closed trade and provide actionable insights.

TRADE DETAILS:
- Symbol: {symbol}
- Direction: {direction}
- Result: {result}
- Exit reason: {exit_reason}
- Entry: {entry}
- Exit: {exit_p}
- Actual R: {actual_r}
- Duration: {duration}
- Confidence: {confidence}%
- Regime: {regime}
- Session: {session}
- Funding bias: {funding}

THESIS:
{thesis}

CONFIDENCE BREAKDOWN:
{bd_text if bd_text else '  N/A'}
{hist_text}

Respond in EXACTLY this JSON format (no markdown, no code fences):
{{
  "summary": "one-line summary",
  "trade_quality": "valid|mixed|poor",
  "regime_quality": "favorable|neutral|unfavorable",
  "execution_quality": "good|average|bad",
  "lessons": ["lesson 1", "lesson 2", "lesson 3"],
  "pattern_detected": "what pattern this trade reveals",
  "improvement_suggestion": "one specific thing to improve"
}}"""

    def analyze_trade(self, trade: dict) -> dict[str, Any]:
        """Generate AI-powered trade analysis via Groq."""
        if not self.available:
            print("[GroqCoach] Groq not available — falling back to heuristic")
            return self._heuristic_fallback(trade)

        meta = self._load_signal_metadata(trade.get("signal_id", ""))
        history = self._load_trade_history(20)

        prompt = self._build_prompt(trade, meta, history)

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise crypto trading coach. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=800,
                timeout=30,
            )

            raw = response.choices[0].message.content.strip()
            # Clean any markdown fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            analysis = json.loads(raw)

            # Ensure all required fields
            defaults = {
                "summary": f"{trade.get('direction','')} {trade.get('result','')}",
                "trade_quality": "valid",
                "regime_quality": "neutral",
                "execution_quality": "average",
                "lessons": [],
                "pattern_detected": "",
                "improvement_suggestion": "",
            }
            for k, v in defaults.items():
                if k not in analysis:
                    analysis[k] = v

            return analysis

        except json.JSONDecodeError as e:
            print(f"[GroqCoach] JSON parse error: {e}")
            return self._heuristic_fallback(trade)
        except Exception as e:
            print(f"[GroqCoach] Error: {e}")
            return self._heuristic_fallback(trade)

    def _heuristic_fallback(self, trade: dict) -> dict[str, Any]:
        """Fallback if Groq is unavailable."""
        result = trade.get("result", "LOSS")
        lessons = []
        if result == "WIN":
            lessons.append("Setup executed as planned — maintain discipline")
        else:
            lessons.append("SL hit — review entry timing and zone edge proximity")

        return {
            "summary": f"{trade.get('direction','')} {result}",
            "trade_quality": "valid" if result == "WIN" else "mixed",
            "regime_quality": "neutral",
            "execution_quality": "average",
            "lessons": lessons,
            "pattern_detected": "insufficient data",
            "improvement_suggestion": "Continue accumulating trade data for pattern analysis",
        }

    def save_analysis(self, trade_id: str, analysis: dict) -> None:
        """Save coach analysis to journal."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT OR REPLACE INTO trade_analysis
                (trade_id, summary, trade_quality, regime_quality,
                 execution_quality, lessons, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
            analysis.get("summary", ""),
            analysis.get("trade_quality", "valid"),
            analysis.get("regime_quality", "neutral"),
            analysis.get("execution_quality", "average"),
            json.dumps(analysis.get("lessons", [])),
            75 if analysis.get("trade_quality") == "valid" else 50,
            now,
        ))
        conn.commit()
        conn.close()

    def review_and_save(self, trade: dict) -> dict[str, Any]:
        """Full pipeline: analyze + save."""
        analysis = self.analyze_trade(trade)
        self.save_analysis(trade["trade_id"], analysis)
        return analysis

    def weekly_review(self) -> str:
        """Generate a weekly performance review for the twice-weekly check-in."""
        if not self.available:
            return "Groq not available for weekly review"

        history = self._load_trade_history(50)
        if not history:
            return "No trades to review yet."

        wins = sum(1 for h in history if h["result"] == "WIN")
        losses = len(history) - wins
        win_rate = wins / len(history) * 100 if history else 0

        # Group by regime
        by_regime = {}
        for h in history:
            r = h.get("market_regime", "UNKNOWN")
            if r not in by_regime:
                by_regime[r] = {"wins": 0, "losses": 0, "r": []}
            if h["result"] == "WIN":
                by_regime[r]["wins"] += 1
            else:
                by_regime[r]["losses"] += 1
            by_regime[r]["r"].append(h.get("actual_r", 0))

        regime_summary = "\n".join(
            f"  {r}: {v['wins']}W/{v['losses']}L, avg R={sum(v['r'])/len(v['r']):.2f}"
            for r, v in by_regime.items()
        )

        prompt = f"""You are Aegis, an elite trading coach. Generate a weekly performance review.

PERFORMANCE SUMMARY:
- Total trades: {len(history)}
- Win rate: {win_rate:.0f}% ({wins}W/{losses}L)
- Average R: {sum(h.get('actual_r',0) for h in history)/len(history):.2f}

BY REGIME:
{regime_summary}

RECENT TRADES:
""" + "\n".join(
            f"  {h['symbol']} {h['direction']} → {h['result']} ({h.get('actual_r',0):.1f}R) [{h.get('market_regime','?')}]"
            for h in history[:10]
        )

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise trading coach. Provide actionable analysis."},
                    {"role": "user", "content": prompt + "\n\nProvide: 1) Overall assessment 2) What's working 3) What needs improvement 4) Specific recommendations for next week"},
                ],
                temperature=0.4,
                max_tokens=1000,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Weekly review error: {e}"
