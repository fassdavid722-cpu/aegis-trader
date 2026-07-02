"""Tests for coach trade analyzer."""
import pytest
from datetime import datetime, timezone

from coach.trade_analyzer import TradeAnalyzer
from signals.models import VirtualPosition, TradeSide, TradeStatus, ExitReason, TradeResult, MarketRegime


class TestTradeAnalyzer:
    """Test post-trade analysis."""

    def setup_method(self):
        self.analyzer = TradeAnalyzer()

    def test_winning_trade_analysis(self):
        position = VirtualPosition(
            signal_id="test-sig",
            symbol="BTCUSDT",
            direction=TradeSide.LONG,
            leverage=10,
            margin_mode="ISOLATED",
            entry_price=100000,
            stop_loss=99000,
            take_profit=110000,
            status=TradeStatus.CLOSED,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            exit_price=110000,
            exit_reason=ExitReason.TP_HIT,
            result=TradeResult.WIN,
            pnl_percent=10.0,
            market_regime=MarketRegime.TRENDING_UP,
        )

        analysis = self.analyzer.analyze(position)

        assert analysis["trade_quality"] == "valid"
        assert analysis["regime_quality"] == "favorable"
        assert "won" in analysis["summary"]

    def test_liquidated_trade_analysis(self):
        position = VirtualPosition(
            signal_id="test-sig",
            symbol="BTCUSDT",
            direction=TradeSide.LONG,
            leverage=50,
            margin_mode="ISOLATED",
            entry_price=100000,
            stop_loss=99000,
            take_profit=110000,
            status=TradeStatus.CLOSED,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            exit_price=95000,
            exit_reason=ExitReason.LIQUIDATED,
            result=TradeResult.LOSS,
            pnl_percent=-50.0,
            market_regime=MarketRegime.HIGH_VOLATILITY,
        )

        analysis = self.analyzer.analyze(position)

        assert analysis["trade_quality"] == "invalid"
        assert analysis["execution_quality"] == "bad"
        assert "liquidated" in str(analysis["lessons"]).lower()

    def test_unclosed_trade_returns_unknown(self):
        position = VirtualPosition(
            signal_id="test-sig",
            symbol="BTCUSDT",
            direction=TradeSide.LONG,
            leverage=10,
            margin_mode="ISOLATED",
            entry_price=100000,
            status=TradeStatus.OPEN,
        )

        analysis = self.analyzer.analyze(position)

        assert analysis["trade_quality"] == "unknown"
        assert analysis["confidence"] == 0
