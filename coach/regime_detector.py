"""Market regime detection using simple heuristics.

Not perfect. Preserves original regime context at entry.
"""
from __future__ import annotations

from typing import Optional, Any

from signals.models import MarketRegime


class RegimeDetector:
    """Detects market regime from price and volume data."""

    def __init__(self) -> None:
        self.price_history: dict[str, list[float]] = {}
        self.max_history = 100

    def update_price(self, symbol: str, price: float) -> None:
        """Add price point to history."""
        if symbol not in self.price_history:
            self.price_history[symbol] = []

        self.price_history[symbol].append(price)

        # Trim history
        if len(self.price_history[symbol]) > self.max_history:
            self.price_history[symbol] = self.price_history[symbol][-self.max_history:]

    def detect_regime(self, symbol: str, candles: list[dict[str, Any]]) -> MarketRegime:
        """Detect regime from recent candle data.

        Uses simple heuristics:
        - ATR vs average range for volatility
        - EMA alignment for trend
        - Price position within recent range for ranging
        """
        if not candles or len(candles) < 20:
            return MarketRegime.UNKNOWN

        # Extract closes
        closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
        if len(closes) < 20:
            return MarketRegime.UNKNOWN

        # Calculate ATR proxy (simple)
        ranges = []
        for c in candles[-20:]:
            high = float(c.get("high", 0))
            low = float(c.get("low", 0))
            if high and low:
                ranges.append(high - low)

        avg_range = sum(ranges) / len(ranges) if ranges else 0
        current_price = closes[-1]

        # Volatility check
        if avg_range > 0:
            volatility_ratio = avg_range / current_price
            if volatility_ratio > 0.02:  # >2% average range
                return MarketRegime.HIGH_VOLATILITY
            elif volatility_ratio < 0.005:  # <0.5% average range
                return MarketRegime.LOW_VOLATILITY

        # Trend detection using EMAs
        ema_fast = self._ema(closes, 8)
        ema_slow = self._ema(closes, 21)

        if ema_fast and ema_slow:
            if ema_fast > ema_slow * 1.005:  # Fast above slow by 0.5%
                return MarketRegime.TRENDING_UP
            elif ema_fast < ema_slow * 0.995:
                return MarketRegime.TRENDING_DOWN

        # Default to ranging
        return MarketRegime.RANGING

    def _ema(self, prices: list[float], period: int) -> Optional[float]:
        """Calculate exponential moving average."""
        if len(prices) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # SMA start

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema
