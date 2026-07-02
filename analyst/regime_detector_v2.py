"""Market Regime Detector — Top-down market context.

Before scanning individual symbols, we check the overall market regime
using BTC as the proxy. A good SHORT setup on SOL when BTC is in a
strong uptrend is probably a bad trade.

Regimes:
- BULL_TREND: BTC making HH+HL on 1H → prefer LONGs, SHORTs need higher bar
- BEAR_TREND: BTC making LH+LL on 1H → prefer SHORTs, LONGs need higher bar
- RANGING: BTC chopping in a range → both directions OK, lower confidence
- HIGH_VOLATILITY: Large swings → reduce position sizes, wider stops
- TRANSITIONING: Regime changing → be cautious, smaller sizes

This is the "is the market good?" check before "is this setup good?"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketRegime:
    regime: str               # BULL_TREND / BEAR_TREND / RANGING / HIGH_VOLATILITY / TRANSITIONING
    bias: str                 # LONG_BIAS / SHORT_BIAS / NEUTRAL
    strength: int             # 0-10
    description: str
    btc_trend_1h: str         # UP / DOWN / RANGE
    btc_trend_15m: str        # UP / DOWN / RANGE
    range_high: float
    range_low: float
    range_pct: float
    recommended_action: str   # what the trader should do

    def to_briefing(self) -> str:
        bias_emoji = {"LONG_BIAS": "🟢", "SHORT_BIAS": "🔴", "NEUTRAL": "🟡"}
        return (
            f"MARKET REGIME: {self.regime} ({self.strength}/10) {bias_emoji.get(self.bias, '⚪')}\n"
            f"BTC Trend: 1H={self.btc_trend_1h} | 15m={self.btc_trend_15m}\n"
            f"BTC Range: {self.range_low:,.0f} - {self.range_high:,.0f} ({self.range_pct:.2f}%)\n"
            f"Bias: {self.bias} — {self.description}\n"
            f"Action: {self.recommended_action}"
        )


def detect_regime(btc_candles_1h: list[dict], btc_candles_15m: list[dict]) -> MarketRegime:
    """Detect the overall market regime from BTC candles."""
    if not btc_candles_1h or len(btc_candles_1h) < 20:
        return MarketRegime(
            regime="UNKNOWN", bias="NEUTRAL", strength=0,
            description="Insufficient data for regime detection",
            btc_trend_1h="UNKNOWN", btc_trend_15m="UNKNOWN",
            range_high=0, range_low=0, range_pct=0,
            recommended_action="Wait for more data"
        )

    current_price = btc_candles_1h[-1]["close"]

    # 1H trend detection
    closes_1h = [c["close"] for c in btc_candles_1h[-20:]]
    highs_1h = [c["high"] for c in btc_candles_1h[-20:]]
    lows_1h = [c["low"] for c in btc_candles_1h[-20:]]

    # Swing points
    swing_highs = []
    swing_lows = []
    for i in range(3, len(btc_candles_1h) - 3):
        is_high = all(btc_candles_1h[i]["high"] >= btc_candles_1h[i+j]["high"] for j in range(-3, 4) if j != 0)
        is_low = all(btc_candles_1h[i]["low"] <= btc_candles_1h[i+j]["low"] for j in range(-3, 4) if j != 0)
        if is_high:
            swing_highs.append(btc_candles_1h[i]["high"])
        if is_low:
            swing_lows.append(btc_candles_1h[i]["low"])

    trend_1h = "RANGE"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]

        if hh and hl:
            trend_1h = "UP"
        elif lh and ll:
            trend_1h = "DOWN"

    # 15m trend
    if btc_candles_15m and len(btc_candles_15m) >= 20:
        closes_15m = [c["close"] for c in btc_candles_15m[-20:]]
        trend_15m = "UP" if closes_15m[-1] > closes_15m[0] else "DOWN"
    else:
        trend_15m = "RANGE"

    # Range calculation
    range_high = max(highs_1h)
    range_low = min(lows_1h)
    range_pct = ((range_high - range_low) / range_low) * 100 if range_low > 0 else 0

    # Volatility check
    avg_range = sum(h - l for h, l in zip(highs_1h, lows_1h)) / len(highs_1h)
    last_range = btc_candles_1h[-1]["high"] - btc_candles_1h[-1]["low"]
    high_vol = last_range > avg_range * 3.0

    # Determine regime
    if high_vol:
        regime = "HIGH_VOLATILITY"
        bias = "NEUTRAL"
        strength = 5
        description = "Market is highly volatile — large swings, reduce risk"
        action = "Reduce position sizes by 50%, widen stops slightly, be cautious"
    elif trend_1h == "UP" and trend_15m == "UP":
        regime = "BULL_TREND"
        bias = "LONG_BIAS"
        strength = 8
        description = "BTC in confirmed uptrend on both 1H and 15m"
        action = "Prefer LONG setups. SHORTs need 75%+ confidence and strong confluence"
    elif trend_1h == "DOWN" and trend_15m == "DOWN":
        regime = "BEAR_TREND"
        bias = "SHORT_BIAS"
        strength = 8
        description = "BTC in confirmed downtrend on both 1H and 15m"
        action = "Prefer SHORT setups. LONGs need 75%+ confidence and strong confluence"
    elif trend_1h == "UP" and trend_15m == "DOWN":
        regime = "TRANSITIONING"
        bias = "LONG_BIAS"
        strength = 4
        description = "BTC uptrend on 1H but pulling back on 15m — potential buy zone"
        action = "Wait for 15m to confirm reversal before going LONG. SHORTs are counter-trend"
    elif trend_1h == "DOWN" and trend_15m == "UP":
        regime = "TRANSITIONING"
        bias = "SHORT_BIAS"
        strength = 4
        description = "BTC downtrend on 1H but bouncing on 15m — potential short zone"
        action = "Wait for 15m to confirm reversal before going SHORT. LONGs are counter-trend"
    else:
        regime = "RANGING"
        bias = "NEUTRAL"
        strength = 3
        description = f"BTC ranging in {range_pct:.1f}% band — no clear trend"
        action = "Trade both directions. Fade the edges (buy support, sell resistance). Lower confidence threshold OK"

    return MarketRegime(
        regime=regime,
        bias=bias,
        strength=strength,
        description=description,
        btc_trend_1h=trend_1h,
        btc_trend_15m=trend_15m,
        range_high=range_high,
        range_low=range_low,
        range_pct=range_pct,
        recommended_action=action,
    )


def should_trade_against_regime(regime: MarketRegime, direction: str, confidence: int) -> tuple[bool, str]:
    """Check if a trade direction conflicts with the market regime.

    Returns (allowed, reason).
    """
    if regime.bias == "NEUTRAL":
        return True, "Market is neutral — both directions OK"

    if regime.bias == "LONG_BIAS" and direction == "SHORT":
        if confidence < 75:
            return False, f"Counter-trend SHORT in BULL market (need 75%+ confidence, got {confidence}%)"
        return True, "Counter-trend SHORT allowed with high confidence"

    if regime.bias == "SHORT_BIAS" and direction == "LONG":
        if confidence < 75:
            return False, f"Counter-trend LONG in BEAR market (need 75%+ confidence, got {confidence}%)"
        return True, "Counter-trend LONG allowed with high confidence"

    return True, "Trade aligns with market bias"


class RegimeDetectorV2:
    """Backward-compatible wrapper for the old interface."""
    def detect(self, candles_4h: list, candles_15m: list = None):
        """Old interface — uses 4H candles instead of 1H."""
        if candles_15m is None:
            candles_15m = candles_4h
        return detect_regime(candles_4h, candles_15m)
