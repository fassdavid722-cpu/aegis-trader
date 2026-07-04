"""Advanced Technical Indicators — The trader's toolkit.

These are the tools a real scalper uses:
- VWAP: Volume Weighted Average Price (intraday fair value)
- RSI: Overbought/oversold momentum
- EMA: Trend direction with dynamic support/resistance
- Bollinger Bands: Volatility squeeze detection
- Volume Profile: POC, VAH, VAL (where real volume traded)
- Divergence: Price vs RSI disagreement

All calculated from candle data. No external dependencies.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass, field


@dataclass
class VWAPData:
    """Volume Weighted Average Price — intraday fair value."""
    vwap: float
    vwap_upper: float       # +1 std
    vwap_lower: float       # -1 std
    price_vs_vwap: str  # "ABOVE" / "BELOW" / "AT"
    distance_pct: float     # how far price is from VWAP


@dataclass
class RSIData:
    """Relative Strength Index — momentum oscillator."""
    rsi: float              # 0-100
    signal: str  # OVERBOUGHT / OVERSOLD / NEUTRAL / etc
    description: str


@dataclass
class EMAData:
    """Exponential Moving Average confluence."""
    ema_9: float
    ema_21: float
    ema_50: float
    alignment: str  # BULLISH / BEARISH / MIXED
    price_vs_ema9: str  # ABOVE / BELOW


@dataclass
class BollingerData:
    """Bollinger Bands — volatility squeeze detection."""
    upper: float
    middle: float
    lower: float
    bandwidth: float        # (upper-lower)/middle * 100 — squeeze when low
    is_squeezed: bool       # bandwidth below threshold
    position: str  # UPPER / LOWER / MIDDLE / EXPANDING


@dataclass
class VolumeProfileData:
    """Volume Profile — where real volume traded."""
    poc: float              # Point of Control — price with most volume
    vah: float              # Value Area High (70% of volume)
    val: float              # Value Area Low (70% of volume)
    current_zone: str  # ABOVE_VAH / IN_VALUE_AREA / BELOW_VAL / AT_POC


@dataclass
class AdvancedIndicators:
    """Complete indicator suite for a single symbol."""
    vwap: Optional[VWAPData] = None
    rsi_5m: Optional[RSIData] = None
    rsi_15m: Optional[RSIData] = None
    ema: Optional[EMAData] = None
    bollinger: Optional[BollingerData] = None
    volume_profile: Optional[VolumeProfileData] = None

    def to_briefing(self) -> str:
        """Format as concise briefing for the LLM."""
        lines = []

        if self.vwap:
            lines.append(
                f"VWAP: {self.vwap.vwap:.4f} | Price {self.vwap.price_vs_vwap} "
                f"({self.vwap.distance_pct:+.2f}%) — {'buyers in control' if self.vwap.price_vs_vwap == 'ABOVE' else 'sellers in control' if self.vwap.price_vs_vwap == 'BELOW' else 'at fair value'}"
            )

        if self.rsi_5m:
            lines.append(
                f"RSI 5m: {self.rsi_5m.rsi:.0f} ({self.rsi_5m.signal}) — {self.rsi_5m.description}"
            )

        if self.rsi_15m:
            lines.append(
                f"RSI 15m: {self.rsi_15m.rsi:.0f} ({self.rsi_15m.signal})"
            )

        if self.ema:
            lines.append(
                f"EMA: 9={self.ema.ema_9:.4f} 21={self.ema.ema_21:.4f} 50={self.ema.ema_50:.4f} "
                f"| {self.ema.alignment} | Price {'above' if self.ema.price_vs_ema9 == 'ABOVE' else 'below'} EMA9"
            )

        if self.bollinger:
            squeeze = " 🔒 SQUEEZE" if self.bollinger.is_squeezed else ""
            lines.append(
                f"BB: {self.bollinger.position} | BW={self.bollinger.bandwidth:.3f}{squeeze}"
            )

        if self.volume_profile:
            lines.append(
                f"VolProfile: POC={self.volume_profile.poc:.4f} VAH={self.volume_profile.vah:.4f} "
                f"VAL={self.volume_profile.val:.4f} | Price {self.volume_profile.current_zone}"
            )

        return "\n".join(lines) if lines else "No advanced indicators available"


def calculate_vwap(candles: list[dict], period: int = 50) -> Optional[VWAPData]:
    """Calculate VWAP from candle data.
    
    VWAP = Sum(Price * Volume) / Sum(Volume)
    where Price = (High + Low + Close) / 3
    """
    if len(candles) < 10:
        return None

    use = candles[-period:] if len(candles) >= period else candles

    cum_pv = 0.0
    cum_v = 0.0
    cum_pv_sq = 0.0  # for std dev

    for c in use:
        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0) or 1  # avoid div by zero
        cum_pv += typical_price * vol
        cum_v += vol
        cum_pv_sq += (typical_price ** 2) * vol

    if cum_v == 0:
        return None

    vwap = cum_pv / cum_v
    # Variance = E[x²] - E[x]²
    variance = (cum_pv_sq / cum_v) - (vwap ** 2)
    std = max(variance ** 0.5, 0)

    current_price = use[-1]["close"]
    distance_pct = ((current_price - vwap) / vwap) * 100 if vwap > 0 else 0

    if abs(distance_pct) < 0.05:
        pos = "AT"
    elif current_price > vwap:
        pos = "ABOVE"
    else:
        pos = "BELOW"

    return VWAPData(
        vwap=vwap,
        vwap_upper=vwap + std,
        vwap_lower=vwap - std,
        price_vs_vwap=pos,
        distance_pct=distance_pct,
    )


def calculate_rsi(candles: list[dict], period: int = 14) -> Optional[RSIData]:
    """Calculate RSI (Relative Strength Index)."""
    if len(candles) < period + 1:
        return None

    closes = [c["close"] for c in candles[-(period + 1):]]
    gains = []
    losses = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Smooth with Wilder's method
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    # Signal classification
    if rsi > 70:
        signal = "OVERBOUGHT"
        desc = "Overbought — potential reversal or strong trend. Don't chase."
    elif rsi < 30:
        signal = "OVERSOLD"
        desc = "Oversold — potential bounce. Look for reversal confirmation."
    elif rsi > 55:
        signal = "BULLISH"
        desc = "Bullish momentum zone."
    elif rsi < 45:
        signal = "BEARISH"
        desc = "Bearish momentum zone."
    else:
        signal = "NEUTRAL"
        desc = "Neutral — no strong momentum signal."

    # Simple divergence detection
    if len(candles) >= period * 3:
        recent = candles[-period:]
        older = candles[-(period * 2):-period]
        recent_trend = recent[-1]["close"] - recent[0]["close"]
        older_trend = older[-1]["close"] - older[0]["close"]

        if recent_trend < 0 and older_trend > 0 and rsi > 50:
            signal = "BEARISH_DIVERGENCE"
            desc = "Price making lower highs but RSI rising — bearish divergence."
        elif recent_trend > 0 and older_trend < 0 and rsi < 50:
            signal = "BULLISH_DIVERGENCE"
            desc = "Price making higher lows but RSI falling — bullish divergence."

    return RSIData(rsi=rsi, signal=signal, description=desc)


def calculate_ema(values: list[float], period: int) -> float:
    """Calculate EMA for a list of values."""
    if len(values) < period:
        return values[-1] if values else 0

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA seed

    for v in values[period:]:
        ema = (v - ema) * multiplier + ema

    return ema


def calculate_ema_data(candles: list[dict]) -> Optional[EMAData]:
    """Calculate EMA confluence (9, 21, 50)."""
    if len(candles) < 50:
        return None

    closes = [c["close"] for c in candles]

    ema9 = calculate_ema(closes[-30:] if len(closes) >= 30 else closes, 9)
    ema21 = calculate_ema(closes[-50:] if len(closes) >= 50 else closes, 21)
    ema50 = calculate_ema(closes, 50)

    current = closes[-1]

    if ema9 > ema21 > ema50:
        alignment = "BULLISH"
    elif ema9 < ema21 < ema50:
        alignment = "BEARISH"
    else:
        alignment = "MIXED"

    price_vs = "ABOVE" if current > ema9 else "BELOW"

    return EMAData(
        ema_9=ema9,
        ema_21=ema21,
        ema_50=ema50,
        alignment=alignment,
        price_vs_ema9=price_vs,
    )


def calculate_bollinger_bands(candles: list[dict], period: int = 20, std_mult: float = 2.0) -> Optional[BollingerData]:
    """Calculate Bollinger Bands + squeeze detection."""
    if len(candles) < period:
        return None

    closes = [c["close"] for c in candles[-period:]]
    sma = sum(closes) / period
    variance = sum((c - sma) ** 2 for c in closes) / period
    std = variance ** 0.5

    upper = sma + std_mult * std
    lower = sma - std_mult * std
    bandwidth = ((upper - lower) / sma) * 100 if sma > 0 else 0

    # Squeeze: bandwidth below 1% (for crypto, adjust per symbol)
    is_squeezed = bandwidth < 0.8

    current = candles[-1]["close"]

    if current > upper * 0.99:
        pos = "UPPER"
    elif current < lower * 1.01:
        pos = "LOWER"
    elif abs(current - sma) / sma * 100 < 0.1:
        pos = "MIDDLE"
    else:
        pos = "MIDDLE"

    # Check if bands are expanding or contracting
    if len(candles) >= period * 2:
        older_closes = [c["close"] for c in candles[-(period * 2):-period]]
        older_sma = sum(older_closes) / period
        older_var = sum((c - older_sma) ** 2 for c in older_closes) / period
        older_std = older_var ** 0.5
        older_bw = ((older_sma + std_mult * older_std - (older_sma - std_mult * older_std)) / older_sma) * 100
        if bandwidth > older_bw * 1.2:
            pos = "EXPANDING"

    return BollingerData(
        upper=upper,
        middle=sma,
        lower=lower,
        bandwidth=bandwidth,
        is_squeezed=is_squeezed,
        position=pos,
    )


def calculate_volume_profile(candles: list[dict], bins: int = 20) -> Optional[VolumeProfileData]:
    """Calculate volume profile — POC, VAH, VAL."""
    if len(candles) < 20:
        return None

    use = candles[-50:] if len(candles) >= 50 else candles

    # Find price range
    all_highs = [c["high"] for c in use]
    all_lows = [c["low"] for c in use]
    price_high = max(all_highs)
    price_low = min(all_lows)
    price_range = price_high - price_low

    if price_range <= 0:
        return None

    bin_size = price_range / bins

    # Build volume at price
    vol_at_price = {}
    for c in use:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        bin_idx = int((typical - price_low) / bin_size)
        bin_idx = min(bin_idx, bins - 1)
        bin_price = price_low + bin_idx * bin_size + bin_size / 2
        vol_at_price[bin_price] = vol_at_price.get(bin_price, 0) + (c.get("volume", 1))

    if not vol_at_price:
        return None

    # POC = price with most volume
    poc = max(vol_at_price, key=vol_at_price.get)
    total_vol = sum(vol_at_price.values())

    # Value area = 70% of volume around POC
    sorted_prices = sorted(vol_at_price.keys(), key=lambda p: -vol_at_price[p])
    cum_vol = 0
    value_area_prices = []
    for p in sorted_prices:
        cum_vol += vol_at_price[p]
        value_area_prices.append(p)
        if cum_vol >= total_vol * 0.7:
            break

    vah = max(value_area_prices) if value_area_prices else poc
    val = min(value_area_prices) if value_area_prices else poc

    current = use[-1]["close"]
    if current > vah:
        zone = "ABOVE_VAH"
    elif current < val:
        zone = "BELOW_VAL"
    elif abs(current - poc) / poc * 100 < 0.1:
        zone = "AT_POC"
    else:
        zone = "IN_VALUE_AREA"

    return VolumeProfileData(
        poc=poc,
        vah=vah,
        val=val,
        current_zone=zone,
    )


def build_advanced_indicators(candles_5m: list[dict], candles_15m: list[dict]) -> AdvancedIndicators:
    """Build the full indicator suite for a symbol."""
    return AdvancedIndicators(
        vwap=calculate_vwap(candles_5m),
        rsi_5m=calculate_rsi(candles_5m, 14),
        rsi_15m=calculate_rsi(candles_15m, 14),
        ema=calculate_ema_data(candles_5m),
        bollinger=calculate_bollinger_bands(candles_5m),
        volume_profile=calculate_volume_profile(candles_5m),
    )
