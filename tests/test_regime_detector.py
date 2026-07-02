"""Tests for regime detector."""
import pytest

from coach.regime_detector import RegimeDetector
from signals.models import MarketRegime


class TestRegimeDetector:
    """Test market regime classification."""

    def setup_method(self):
        self.detector = RegimeDetector()

    def test_empty_candles_returns_unknown(self):
        regime = self.detector.detect_regime("BTCUSDT", [])
        assert regime == MarketRegime.UNKNOWN

    def test_insufficient_candles_returns_unknown(self):
        candles = [{"close": 100 + i, "high": 101 + i, "low": 99 + i} for i in range(5)]
        regime = self.detector.detect_regime("BTCUSDT", candles)
        assert regime == MarketRegime.UNKNOWN

    def test_trending_up_detection(self):
        # Create strong uptrend: prices increasing steadily
        candles = []
        base = 100
        for i in range(30):
            close = base + i * 2  # Steady increase
            candles.append({
                "close": close,
                "high": close + 1,
                "low": close - 1,
            })

        regime = self.detector.detect_regime("BTCUSDT", candles)
        assert regime == MarketRegime.TRENDING_UP

    def test_trending_down_detection(self):
        # Create strong downtrend
        candles = []
        base = 100
        for i in range(30):
            close = base - i * 2  # Steady decrease
            candles.append({
                "close": close,
                "high": close + 1,
                "low": close - 1,
            })

        regime = self.detector.detect_regime("BTCUSDT", candles)
        assert regime == MarketRegime.TRENDING_DOWN
