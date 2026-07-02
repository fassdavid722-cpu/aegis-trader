"""Trade analyzer - generates structured post-trade review.

Distinguishes between:
- Bad setup (signal was wrong)
- Bad execution (fill was poor)
- Market randomness (correct setup, random outcome)
- Regime mismatch (setup and regime didn't align)
- Risk management issue (SL too tight, leverage too high)
"""
from __future__ import annotations

from typing import Optional, Any

from signals.models import VirtualPosition, TradeResult, MarketRegime, ExitReason


class TradeAnalyzer:
    """Analyzes closed trades and generates structured reviews."""

    def analyze(self, position: VirtualPosition) -> dict[str, Any]:
        """Generate complete trade analysis.

        Args:
            position: Closed virtual position with full metrics

        Returns:
            Structured analysis dict
        """
        if position.status.value != "CLOSED":
            return {
                "summary": "Cannot analyze: trade not closed",
                "trade_quality": "unknown",
                "regime_quality": "unknown",
                "execution_quality": "unknown",
                "lessons": [],
                "confidence": 0,
            }

        lessons = []
        trade_quality = "valid"
        regime_quality = "favorable"
        execution_quality = "good"

        # === Analyze outcome ===

        # 1. Was stop loss too tight? (MAE vs SL distance)
        if position.stop_loss and position.entry_price:
            sl_distance = abs(position.entry_price - position.stop_loss)
            if position.max_adverse_excursion > sl_distance * 1.5:
                lessons.append("Stop loss was too tight - price reversed after hitting SL")
                trade_quality = "mixed"

        # 2. Did price reach near TP before reversing? (missed opportunity)
        if position.take_profit and position.entry_price:
            tp_distance = abs(position.take_profit - position.entry_price)
            if position.max_favorable_excursion > tp_distance * 0.7:
                if position.result == TradeResult.LOSS:
                    lessons.append("Price came close to TP but reversed - consider trailing stops")

        # 3. Immediate reversal (noise)
        if position.opened_at and position.closed_at:
            duration_seconds = (position.closed_at - position.opened_at).total_seconds()
            if duration_seconds < 60 and position.result == TradeResult.LOSS:
                lessons.append("Trade failed immediately - likely noise or stop hunt")
                trade_quality = "mixed"

        # 4. Regime analysis
        if position.market_regime:
            if position.market_regime in (MarketRegime.HIGH_VOLATILITY, MarketRegime.RANGING):
                if position.result == TradeResult.LOSS:
                    regime_quality = "unfavorable"
                    lessons.append(f"Unfavorable regime: {position.market_regime.value}")

        # 5. Leverage check
        if position.leverage > 20:
            lessons.append(f"High leverage ({position.leverage}x) amplified losses")
            trade_quality = "mixed"

        # 6. Liquidation proximity
        if position.exit_reason == ExitReason.LIQUIDATED:
            lessons.append("Position was liquidated - leverage was inappropriate for volatility")
            trade_quality = "invalid"
            execution_quality = "bad"

        # 7. Win with bad process
        if position.result == TradeResult.WIN:
            if position.stop_loss and position.entry_price:
                sl_distance = abs(position.entry_price - position.stop_loss)
                if position.max_adverse_excursion > sl_distance * 0.8:
                    lessons.append("Won but with significant drawdown - review position sizing")

        # Build summary
        if position.result == TradeResult.WIN:
            summary = f"{position.direction.value} won: {position.pnl_percent}%"
        elif position.result == TradeResult.LOSS:
            summary = f"{position.direction.value} lost: {position.pnl_percent}% | {position.exit_reason.value}"
        else:
            summary = f"{position.direction.value} breakeven"

        # Add regime context
        if position.market_regime:
            summary += f" in {position.market_regime.value}"

        return {
            "summary": summary,
            "trade_quality": trade_quality,
            "regime_quality": regime_quality,
            "execution_quality": execution_quality,
            "lessons": lessons,
            "confidence": 70 if trade_quality == "valid" else 50,
        }
