"""Coach engine - orchestrates post-trade analysis."""
from __future__ import annotations

from typing import Optional, Any

from signals.models import VirtualPosition
from .trade_analyzer import TradeAnalyzer
from journal.models import JournalWriter


class CoachEngine:
    """Generates post-trade analysis and writes to journal."""

    def __init__(self) -> None:
        self.analyzer = TradeAnalyzer()
        self.journal = JournalWriter()

    def review_trade(self, position: VirtualPosition) -> dict[str, Any]:
        """Generate and persist trade review."""
        analysis = self.analyzer.analyze(position)

        self.journal.record_analysis(
            trade_id=position.trade_id,
            summary=analysis["summary"],
            trade_quality=analysis["trade_quality"],
            regime_quality=analysis["regime_quality"],
            execution_quality=analysis["execution_quality"],
            lessons=analysis["lessons"],
            confidence=analysis["confidence"],
        )

        return analysis
