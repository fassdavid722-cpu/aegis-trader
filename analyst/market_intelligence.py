"""Market Intelligence Layer — Pre-processes raw candles into trader-ready signals.

This is what separates a trader from an analyst. Instead of dumping raw OHLCV
into the LLM, this module extracts:
- Momentum (direction + strength)
- Volatility (ATR-like, for SL sizing)
- Volume profile (surges, dry-ups, accumulation)
- Key levels (recent support/resistance from swing points)
- Candlestick patterns (engulfing, pin bar, momentum candles, doji)
- Trend structure (HH/HL, LH/LL, range)
- Session context (how far into the session, urgency)
- Price action narrative (human-readable summary of what just happened)

The LLM gets a decision-ready briefing, not raw data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class MarketIntelligence:
    """Rich market context for the LLM trader."""
    symbol: str
    current_price: float

    # Momentum
    momentum_5m: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    momentum_strength: int = 0            # 0-10
    momentum_description: str = ""

    # Volatility
    atr_5m: float = 0.0                   # Average true range (5min)
    atr_15m: float = 0.0
    volatility_regime: str = "NORMAL"     # LOW / NORMAL / HIGH / EXTREME
    current_range_pct: float = 0.0        # Last 10 bars range as % of price

    # Volume
    volume_status: str = "NORMAL"         # SURGE / DRY / NORMAL / ACCUMULATION
    volume_surge_ratio: float = 1.0       # Current vol / avg vol
    volume_description: str = ""

    # Key Levels
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    support_distance_pct: float = 0.0
    resistance_distance_pct: float = 0.0
    session_high: float = 0.0
    session_low: float = 0.0
    distance_from_session_high_pct: float = 0.0
    distance_from_session_low_pct: float = 0.0

    # Structure
    trend_short: str = "RANGE"            # UP / DOWN / RANGE
    trend_mid: str = "RANGE"              # From 15min
    structure_events: list[str] = field(default_factory=list)  # ["BOS_BULL", "CHOCH_BEAR", etc.]

    # Patterns (last 3 candles)
    patterns: list[str] = field(default_factory=list)  # ["BULLISH_ENGULFING", "PIN_BAR_BULL", etc.]
    pattern_description: str = ""

    # Price action narrative
    narrative: str = ""                   # Human-readable summary

    # Session
    session: str = "OFF_HOURS"
    session_progress: float = 0.0         # 0-1, how far into the session

    # Trading signals (pre-processed)
    scalp_long_signal: bool = False
    scalp_short_signal: bool = False
    swing_long_signal: bool = False
    swing_short_signal: bool = False
    signal_strength: int = 0              # 0-10, how strong the setup is

    def to_briefing(self) -> str:
        """Format as a decision-ready briefing for the LLM."""
        lines = [
            f"═══ {self.symbol} MARKET BRIEFING ═══",
            f"Price: {self.current_price:,.4f} | Session: {self.session} ({self.session_progress*100:.0f}% in)",
            "",
            f"MOMENTUM: {self.momentum_5m} ({self.momentum_strength}/10) — {self.momentum_description}",
            f"VOLATILITY: {self.volatility_regime} | ATR 5m: {self.atr_5m:.4f} ({self.atr_5m/self.current_price*100:.3f}%) | Range: {self.current_range_pct:.2f}%",
            f"VOLUME: {self.volume_status} ({self.volume_surge_ratio:.1f}x avg) — {self.volume_description}",
            "",
            f"KEY LEVELS:",
            f"  Support: {self.nearest_support:,.4f} ({self.support_distance_pct:.2f}% below)",
            f"  Resistance: {self.nearest_resistance:,.4f} ({self.resistance_distance_pct:.2f}% above)",
            f"  Session H/L: {self.session_high:,.4f} / {self.session_low:,.4f}",
            f"  From session high: -{self.distance_from_session_high_pct:.2f}% | From low: +{self.distance_from_session_low_pct:.2f}%",
            "",
            f"TREND: 5m={self.trend_short} | 15m={self.trend_mid}",
        ]

        if self.structure_events:
            lines.append(f"STRUCTURE: {', '.join(self.structure_events)}")

        if self.patterns:
            lines.append(f"PATTERNS: {', '.join(self.patterns)}")
            lines.append(f"  → {self.pattern_description}")

        # Pre-processed signals
        signals = []
        if self.scalp_long_signal:
            signals.append("⚡ SCALP LONG")
        if self.scalp_short_signal:
            signals.append("⚡ SCALP SHORT")
        if self.swing_long_signal:
            signals.append("📊 SWING LONG")
        if self.swing_short_signal:
            signals.append("📊 SWING SHORT")

        if signals:
            lines.append(f"\n⚡ PRE-PROCESSED SIGNALS: {' + '.join(signals)} (strength: {self.signal_strength}/10)")
        else:
            lines.append(f"\n⚡ PRE-PROCESSED SIGNALS: None (strength: {self.signal_strength}/10)")

        # Narrative
        lines.append(f"\n📖 NARRATIVE: {self.narrative}")

        return "\n".join(lines)


def calculate_atr(candles: list[dict], period: int = 14) -> float:
    """Calculate Average True Range."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, min(len(candles), period + 1)):
        c = candles[i]
        prev_close = candles[i-1]["close"]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - prev_close),
            abs(c["low"] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def find_swing_points(candles: list[dict], lookback: int = 3) -> tuple[list[float], list[float]]:
    """Find recent swing highs and lows."""
    highs, lows = [], []
    if len(candles) < lookback * 2 + 1:
        return highs, lows
    for i in range(lookback, len(candles) - lookback):
        is_high = all(candles[i]["high"] >= candles[i+j]["high"] for j in range(-lookback, lookback+1) if j != 0)
        is_low = all(candles[i]["low"] <= candles[i+j]["low"] for j in range(-lookback, lookback+1) if j != 0)
        if is_high:
            highs.append(candles[i]["high"])
        if is_low:
            lows.append(candles[i]["low"])
    return highs[-5:], lows[-5:]  # Last 5 of each


def detect_patterns(candles: list[dict]) -> tuple[list[str], str]:
    """Detect candlestick patterns in the last 3 candles."""
    patterns = []
    description = ""

    if len(candles) < 3:
        return patterns, description

    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    body3 = abs(c3["close"] - c3["open"])
    range3 = c3["high"] - c3["low"]

    # Bullish Engulfing
    if c2["close"] < c2["open"] and c3["close"] > c3["open"]:
        if c3["close"] >= c2["open"] and c3["open"] <= c2["close"]:
            patterns.append("BULLISH_ENGULFING")
            description = "Bullish engulfing — buyers overwhelmed sellers on last candle"

    # Bearish Engulfing
    if c2["close"] > c2["open"] and c3["close"] < c3["open"]:
        if c3["close"] <= c2["open"] and c3["open"] >= c2["close"]:
            patterns.append("BEARISH_ENGULFING")
            description = "Bearish engulfing — sellers overwhelmed buyers on last candle"

    # Pin Bar (Hammer / Shooting Star)
    if range3 > 0:
        upper_wick = c3["high"] - max(c3["open"], c3["close"])
        lower_wick = min(c3["open"], c3["close"]) - c3["low"]
        body_ratio = body3 / range3

        if lower_wick > body3 * 2 and upper_wick < body3 * 0.5:
            patterns.append("PIN_BAR_BULL")
            description = "Bullish pin bar — long lower wick, rejection of lower prices"

        if upper_wick > body3 * 2 and lower_wick < body3 * 0.5:
            patterns.append("PIN_BAR_BEAR")
            description = "Bearish pin bar — long upper wick, rejection of higher prices"

    # Momentum candle (strong directional move)
    avg_body = (body1 + body2 + body3) / 3
    if body3 > avg_body * 1.8 and range3 > 0:
        if c3["close"] > c3["open"]:
            patterns.append("MOMENTUM_BULL")
            description = "Bullish momentum candle — strong buying pressure, 1.8x avg body"
        else:
            patterns.append("MOMENTUM_BEAR")
            description = "Bearish momentum candle — strong selling pressure, 1.8x avg body"

    # Doji (indecision)
    if range3 > 0 and body3 / range3 < 0.1:
        patterns.append("DOJI")
        description = "Doji — indecision at current level, potential reversal point"

    # Three-bar pattern: higher highs + higher lows (bullish sequence)
    if (c1["high"] < c2["high"] < c3["high"] and
        c1["low"] < c2["low"] < c3["low"]):
        patterns.append("HH_HL_SEQUENCE")
        if not description:
            description = "Three-bar ascending — consistent buying, momentum building"

    # Three-bar pattern: lower highs + lower lows (bearish sequence)
    if (c1["high"] > c2["high"] > c3["high"] and
        c1["low"] > c2["low"] > c3["low"]):
        patterns.append("LH_LL_SEQUENCE")
        if not description:
            description = "Three-bar descending — consistent selling, pressure building"

    return patterns, description


def calculate_momentum(candles: list[dict]) -> tuple[str, int, str]:
    """Calculate momentum from last 10 candles."""
    if len(candles) < 10:
        return "NEUTRAL", 0, "Insufficient data"

    recent = candles[-10:]
    closes = [c["close"] for c in recent]

    # Rate of change
    roc = ((closes[-1] - closes[0]) / closes[0]) * 100

    # Recent vs older (last 3 vs previous 7)
    recent_avg = sum(closes[-3:]) / 3
    older_avg = sum(closes[-7:-3]) / 4
    short_roc = ((recent_avg - older_avg) / older_avg) * 100

    # Strength 0-10
    abs_roc = abs(roc)
    strength = min(int(abs_roc * 10), 10)

    if roc > 0.1:
        direction = "BULLISH"
        desc = f"Building upside momentum (+{roc:.3f}% over 10 bars, recent acceleration: {short_roc:+.3f}%)"
    elif roc < -0.1:
        direction = "BEARISH"
        desc = f"Building downside momentum ({roc:.3f}% over 10 bars, recent acceleration: {short_roc:+.3f}%)"
    else:
        direction = "NEUTRAL"
        strength = max(strength, 3)
        desc = f"Flat momentum ({roc:+.3f}% over 10 bars) — price coiling, breakout pending"

    return direction, strength, desc


def analyze_volume(candles: list[dict]) -> tuple[str, float, str]:
    """Analyze volume patterns."""
    if len(candles) < 20:
        return "NORMAL", 1.0, "Insufficient data"

    recent_vol = candles[-1].get("volume", 0)
    avg_vol = sum(c.get("volume", 0) for c in candles[-20:]) / 20
    ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    # Check last 3 bars for accumulation
    last_3_vol = sum(c.get("volume", 0) for c in candles[-3:]) / 3
    prev_3_vol = sum(c.get("volume", 0) for c in candles[-6:-3]) / 3
    vol_increasing = last_3_vol > prev_3_vol * 1.3

    if ratio > 2.5:
        status = "SURGE"
        desc = f"Volume surge {ratio:.1f}x average — institutional activity or breakout"
    elif ratio > 1.5:
        status = "ELEVATED"
        desc = f"Above-average volume {ratio:.1f}x — increased interest"
    elif ratio < 0.4:
        status = "DRY"
        desc = f"Very low volume {ratio:.1f}x — market asleep, avoid"
    elif vol_increasing:
        status = "ACCUMULATION"
        desc = f"Volume building over last 3 bars ({ratio:.1f}x) — positioning in progress"
    else:
        status = "NORMAL"
        desc = f"Normal volume ({ratio:.1f}x average)"

    return status, ratio, desc


def detect_structure(candles: list[dict], swing_highs: list[float], swing_lows: list[float]) -> list[str]:
    """Detect BOS/CHOCH structure events."""
    events = []
    if len(swing_highs) < 2 or len(swing_lows) < 2 or len(candles) < 5:
        return events

    current_price = candles[-1]["close"]
    last_swing_high = swing_highs[-1]
    last_swing_low = swing_lows[-1]

    # BOS Bull: price breaks above last swing high
    if current_price > last_swing_high and len(swing_highs) >= 2:
        if swing_highs[-1] > swing_highs[-2]:
            events.append("BOS_BULL")
        else:
            events.append("CHOCH_BULL")

    # BOS Bear: price breaks below last swing low
    if current_price < last_swing_low and len(swing_lows) >= 2:
        if swing_lows[-1] < swing_lows[-2]:
            events.append("BOS_BEAR")
        else:
            events.append("CHOCH_BEAR")

    return events


def generate_narrative(
    momentum_dir: str, momentum_desc: str,
    volume_status: str, volume_desc: str,
    patterns: list[str], pattern_desc: str,
    trend_short: str, trend_mid: str,
    support_dist: float, resistance_dist: float,
    current_price: float, session: str,
) -> str:
    """Generate a human-readable price action narrative."""
    parts = []

    # Trend context
    if trend_mid == "UP" and trend_short == "UP":
        parts.append("Price in uptrend on both timeframes")
    elif trend_mid == "DOWN" and trend_short == "DOWN":
        parts.append("Price in downtrend on both timeframes")
    elif trend_mid == "UP" and trend_short == "DOWN":
        parts.append("Uptrend on 15min but pulling back on 5min — potential buy zone")
    elif trend_mid == "DOWN" and trend_short == "UP":
        parts.append("Downtrend on 15min but bouncing on 5min — potential short zone")
    else:
        parts.append("Price ranging on both timeframes")

    # Momentum
    parts.append(momentum_desc.lower())

    # Volume
    parts.append(volume_desc.lower())

    # Patterns
    if pattern_desc:
        parts.append(pattern_desc.lower())

    # Level proximity
    if support_dist < 0.3:
        parts.append(f"Price testing support ({support_dist:.2f}% below) — reaction zone")
    if resistance_dist < 0.3:
        parts.append(f"Price near resistance ({resistance_dist:.2f}% above) — potential rejection")

    return ". ".join(parts) + "."


def build_intelligence(
    symbol: str,
    candles_5m: list[dict],
    candles_15m: list[dict],
    funding_rate: Optional[float],
    session: str,
    session_progress: float,
) -> MarketIntelligence:
    """Build complete market intelligence from raw candle data."""
    current_price = candles_5m[-1]["close"] if candles_5m else 0

    # Momentum
    mom_dir, mom_str, mom_desc = calculate_momentum(candles_5m)

    # Volatility
    atr_5m = calculate_atr(candles_5m, 14)
    atr_15m = calculate_atr(candles_15m, 14)
    current_range_pct = 0
    if len(candles_5m) >= 10:
        last_10 = candles_5m[-10:]
        range_high = max(c["high"] for c in last_10)
        range_low = min(c["low"] for c in last_10)
        current_range_pct = ((range_high - range_low) / range_low) * 100 if range_low > 0 else 0

    avg_atr_pct = (atr_5m / current_price * 100) if current_price > 0 else 0
    if avg_atr_pct < 0.15:
        vol_regime = "LOW"
    elif avg_atr_pct > 0.5:
        vol_regime = "HIGH"
    elif avg_atr_pct > 0.8:
        vol_regime = "EXTREME"
    else:
        vol_regime = "NORMAL"

    # Volume
    vol_status, vol_ratio, vol_desc = analyze_volume(candles_5m)

    # Key levels
    swing_highs, swing_lows = find_swing_points(candles_15m, 3)
    nearest_resistance = min((h for h in swing_highs if h > current_price), default=current_price * 1.01)
    nearest_support = max((l for l in swing_lows if l < current_price), default=current_price * 0.99)

    support_dist = ((current_price - nearest_support) / current_price) * 100 if nearest_support > 0 else 0
    resistance_dist = ((nearest_resistance - current_price) / current_price) * 100 if nearest_resistance > 0 else 0

    # Session high/low (from 5min candles in last 4 hours = 48 bars)
    session_candles = candles_5m[-48:] if len(candles_5m) >= 48 else candles_5m
    session_high = max(c["high"] for c in session_candles) if session_candles else current_price
    session_low = min(c["low"] for c in session_candles) if session_candles else current_price
    dist_high = ((session_high - current_price) / current_price) * 100 if current_price > 0 else 0
    dist_low = ((current_price - session_low) / current_price) * 100 if current_price > 0 else 0

    # Structure
    structure = detect_structure(candles_5m, swing_highs, swing_lows)

    # Patterns
    patterns, pattern_desc = detect_patterns(candles_5m)

    # Trend detection
    if len(candles_5m) >= 20:
        closes_5m = [c["close"] for c in candles_5m[-20:]]
        trend_short = "UP" if closes_5m[-1] > closes_5m[0] else "DOWN"
    else:
        trend_short = "RANGE"

    if len(candles_15m) >= 20:
        closes_15m = [c["close"] for c in candles_15m[-20:]]
        trend_mid = "UP" if closes_15m[-1] > closes_15m[0] else "DOWN"
    else:
        trend_mid = "RANGE"

    # Pre-processed trading signals
    scalp_long = False
    scalp_short = False
    swing_long = False
    swing_short = False
    signal_strength = 0

    # Scalp long signals
    if mom_dir in ("BULLISH", "NEUTRAL") and vol_status in ("SURGE", "ELEVATED", "ACCUMULATION"):
        if any(p in patterns for p in ["BULLISH_ENGULFING", "PIN_BAR_BULL", "MOMENTUM_BULL", "HH_HL_SEQUENCE"]):
            scalp_long = True
            signal_strength += 3
        if support_dist < 0.3 and vol_status in ("SURGE", "ACCUMULATION"):
            scalp_long = True
            signal_strength += 2
        if "BOS_BULL" in structure or "CHOCH_BULL" in structure:
            scalp_long = True
            signal_strength += 2

    # Scalp short signals
    if mom_dir in ("BEARISH", "NEUTRAL") and vol_status in ("SURGE", "ELEVATED", "ACCUMULATION"):
        if any(p in patterns for p in ["BEARISH_ENGULFING", "PIN_BAR_BEAR", "MOMENTUM_BEAR", "LH_LL_SEQUENCE"]):
            scalp_short = True
            signal_strength += 3
        if resistance_dist < 0.3 and vol_status in ("SURGE", "ELEVATED"):
            scalp_short = True
            signal_strength += 2
        if "BOS_BEAR" in structure or "CHOCH_BEAR" in structure:
            scalp_short = True
            signal_strength += 2

    # Swing signals (stronger conviction)
    if trend_mid == "UP" and trend_short == "UP" and mom_dir == "BULLISH":
        if scalp_long:
            swing_long = True
            signal_strength += 3

    if trend_mid == "DOWN" and trend_short == "DOWN" and mom_dir == "BEARISH":
        if scalp_short:
            swing_short = True
            signal_strength += 3

    signal_strength = min(signal_strength, 10)

    # Narrative
    narrative = generate_narrative(
        mom_dir, mom_desc, vol_status, vol_desc,
        patterns, pattern_desc, trend_short, trend_mid,
        support_dist, resistance_dist, current_price, session,
    )

    return MarketIntelligence(
        symbol=symbol,
        current_price=current_price,
        momentum_5m=mom_dir,
        momentum_strength=mom_str,
        momentum_description=mom_desc,
        atr_5m=atr_5m,
        atr_15m=atr_15m,
        volatility_regime=vol_regime,
        current_range_pct=current_range_pct,
        volume_status=vol_status,
        volume_surge_ratio=vol_ratio,
        volume_description=vol_desc,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        support_distance_pct=support_dist,
        resistance_distance_pct=resistance_dist,
        session_high=session_high,
        session_low=session_low,
        distance_from_session_high_pct=dist_high,
        distance_from_session_low_pct=dist_low,
        trend_short=trend_short,
        trend_mid=trend_mid,
        structure_events=structure,
        patterns=patterns,
        pattern_description=pattern_desc,
        narrative=narrative,
        session=session,
        session_progress=session_progress,
        scalp_long_signal=scalp_long,
        scalp_short_signal=scalp_short,
        swing_long_signal=swing_long,
        swing_short_signal=swing_short,
        signal_strength=signal_strength,
    )
