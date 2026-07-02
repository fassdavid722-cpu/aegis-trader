"""Tests for Analyst Engine.

Tests indicator calculations, setup detection, and signal bridge.
"""
import pytest

from analyst.indicators import (
    calculate_ema, calculate_rsi, calculate_atr,
    calculate_bollinger_bands, calculate_macd, calculate_adx,
    calculate_all_indicators,
)
from analyst.setup_detector import SetupDetector
from analyst.models import TradeCandidate, SetupType


class TestIndicators:
    """Test technical indicator calculations."""

    def test_ema_calculation(self):
        prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109]
        ema = calculate_ema(prices, 5)
        assert ema is not None
        assert ema > 0

    def test_rsi_calculation(self):
        # Strong uptrend
        prices = [100 + i * 2 for i in range(20)]
        rsi = calculate_rsi(prices, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100
        assert rsi > 50  # Uptrend should have RSI > 50

    def test_atr_calculation(self):
        candles = [
            {"high": 105, "low": 95, "close": 100},
            {"high": 108, "low": 98, "close": 103},
            {"high": 110, "low": 100, "close": 105},
        ] * 10  # Repeat for sufficient data
        atr = calculate_atr(candles, 14)
        assert atr is not None
        assert atr > 0

    def test_bollinger_bands(self):
        prices = [100 + (i % 10) for i in range(30)]
        bb = calculate_bollinger_bands(prices, 20)
        assert bb["upper"] is not None
        assert bb["lower"] is not None
        assert bb["upper"] > bb["lower"]

    def test_macd_calculation(self):
        prices = [100 + i * 2 for i in range(50)]
        macd = calculate_macd(prices)
        assert macd["macd_line"] is not None

    def test_all_indicators(self):
        candles = [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
            for _ in range(60)
        ]
        # Add some variation
        for i in range(len(candles)):
            candles[i]["close"] = 100 + i * 0.5
            candles[i]["high"] = candles[i]["close"] + 2
            candles[i]["low"] = candles[i]["close"] - 2

        indicators = calculate_all_indicators(candles)
        assert "ema_8" in indicators
        assert "rsi_14" in indicators
        assert "atr_14" in indicators
        assert indicators["ema_8"] is not None


class TestSetupDetector:
    """Test setup detection logic."""

    def setup_method(self):
        self.detector = SetupDetector()

    def test_no_setup_on_insufficient_data(self):
        candles = []
        candidates = self.detector.analyze_symbol("BTCUSDT", candles, 50000)
        assert len(candidates) == 0

    def test_detects_ema_pullback_long(self):
        # Create uptrend candles with pullback to EMA21
        candles = []
        base = 100000
        for i in range(60):
            if i < 40:
                close = base + i * 100  # Uptrend
            else:
                close = base + 4000 - (i - 40) * 50  # Pullback

            candles.append({
                "open": close - 50,
                "high": close + 100,
                "low": close - 100,
                "close": close,
                "volume": 1000 + i * 10,
            })

        candidates = self.detector.analyze_symbol("BTCUSDT", candles, candles[-1]["close"])

        # Should detect at least one setup
        assert len(candidates) > 0

        # Check for EMA pullback
        ema_pullbacks = [c for c in candidates if c.setup_type == SetupType.EMA_PULLBACK]
        assert len(ema_pullbacks) > 0

        # Verify structure
        candidate = ema_pullbacks[0]
        assert candidate.entry > 0
        assert candidate.stop_loss > 0
        assert candidate.take_profit > 0
        assert candidate.risk_reward is not None
        assert candidate.thesis != ""

    def test_candidate_has_valid_risk_reward(self):
        candles = []
        base = 100000
        for i in range(60):
            close = base + i * 100
            candles.append({
                "open": close - 50,
                "high": close + 100,
                "low": close - 100,
                "close": close,
                "volume": 1000,
            })

        candidates = self.detector.analyze_symbol("BTCUSDT", candles, candles[-1]["close"])

        for candidate in candidates:
            if candidate.side == "LONG":
                assert candidate.take_profit > candidate.entry
                assert candidate.stop_loss < candidate.entry
            elif candidate.side == "SHORT":
                assert candidate.take_profit < candidate.entry
                assert candidate.stop_loss > candidate.entry

            # Risk:reward should be positive
            assert candidate.risk_reward is not None
            assert candidate.risk_reward > 0


class TestSignalBridge:
    """Test analyst-to-pipeline bridge."""

    def test_candidate_to_signal_conversion(self):
        from analyst.signal_bridge import AnalystSignalBridge
        from positions import PositionManager

        pm = PositionManager()
        bridge = AnalystSignalBridge(pm)

        candidate = TradeCandidate(
            symbol="BTCUSDT",
            setup_type=SetupType.EMA_PULLBACK,
            side="LONG",
            entry=105000,
            stop_loss=103500,
            take_profit=108000,
            leverage=10,
            confidence=70,
            thesis="Test setup",
        )

        signal = bridge.submit_candidate(candidate)

        assert signal is not None
        assert signal.symbol == "BTCUSDT"
        assert signal.side.value == "LONG"
        assert signal.entry == 105000
