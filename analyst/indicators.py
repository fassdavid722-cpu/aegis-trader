"""Technical indicator calculations.

Pure functions. No side effects. No API calls.
"""
from __future__ import annotations

from typing import Optional, Any


def calculate_ema(prices: list[float], period: int) -> Optional[float]:
    """Calculate exponential moving average.

    Args:
        prices: List of closing prices, oldest first
        period: EMA period

    Returns:
        EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_sma(prices: list[float], period: int) -> Optional[float]:
    """Calculate simple moving average."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Calculate Relative Strength Index.

    Args:
        prices: Closing prices
        period: RSI period (default 14)

    Returns:
        RSI value 0-100, or None
    """
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed RSI
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def calculate_atr(candles: list[dict[str, Any]], period: int = 14) -> Optional[float]:
    """Calculate Average True Range.

    Args:
        candles: List of candle dicts with 'high', 'low', 'close'
        period: ATR period

    Returns:
        ATR value or None
    """
    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i-1]["close"])

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)

        true_ranges.append(max(tr1, tr2, tr3))

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period

    # Smoothed ATR
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return round(atr, 2)


def calculate_bollinger_bands(
    prices: list[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, Optional[float]]:
    """Calculate Bollinger Bands.

    Returns:
        Dict with 'upper', 'middle', 'lower', 'width'
    """
    if len(prices) < period:
        return {"upper": None, "middle": None, "lower": None, "width": None}

    sma = sum(prices[-period:]) / period
    variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
    std = variance ** 0.5

    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    width = (upper - lower) / sma if sma > 0 else 0

    return {
        "upper": round(upper, 2),
        "middle": round(sma, 2),
        "lower": round(lower, 2),
        "width": round(width, 4),
    }


def calculate_macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, Optional[float]]:
    """Calculate MACD.

    Returns:
        Dict with 'macd_line', 'signal_line', 'histogram'
    """
    if len(prices) < slow + signal:
        return {"macd_line": None, "signal_line": None, "histogram": None}

    # Calculate EMAs
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)

    if ema_fast is None or ema_slow is None:
        return {"macd_line": None, "signal_line": None, "histogram": None}

    macd_line = ema_fast - ema_slow

    # Signal line is EMA of MACD line
    # We need MACD history - approximate using last N values
    macd_values = []
    for i in range(slow, len(prices)):
        ef = calculate_ema(prices[:i+1], fast)
        es = calculate_ema(prices[:i+1], slow)
        if ef and es:
            macd_values.append(ef - es)

    signal_line = calculate_ema(macd_values, signal) if len(macd_values) >= signal else None

    histogram = macd_line - signal_line if signal_line else None

    return {
        "macd_line": round(macd_line, 4) if macd_line else None,
        "signal_line": round(signal_line, 4) if signal_line else None,
        "histogram": round(histogram, 4) if histogram else None,
    }


def calculate_adx(candles: list[dict[str, Any]], period: int = 14) -> dict[str, Optional[float]]:
    """Calculate ADX, +DI, -DI.

    Returns:
        Dict with 'adx', 'plus_di', 'minus_di'
    """
    if len(candles) < period * 2 + 1:
        return {"adx": None, "plus_di": None, "minus_di": None}

    plus_dm = []
    minus_dm = []
    tr_values = []

    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_high = float(candles[i-1]["high"])
        prev_low = float(candles[i-1]["low"])
        prev_close = float(candles[i-1]["close"])

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(max(up_move, 0) if up_move > down_move else 0)
        minus_dm.append(max(down_move, 0) if down_move > up_move else 0)

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        tr_values.append(max(tr1, tr2, tr3))

    if len(plus_dm) < period * 2:
        return {"adx": None, "plus_di": None, "minus_di": None}

    # Smooth DM and TR
    smoothed_plus_dm = sum(plus_dm[:period])
    smoothed_minus_dm = sum(minus_dm[:period])
    smoothed_tr = sum(tr_values[:period])

    plus_di = (smoothed_plus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
    minus_di = (smoothed_minus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0

    # ADX is smoothed DX
    adx_values = [dx]
    for i in range(period, min(period * 2, len(plus_dm))):
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_values[i]

        pdi = (smoothed_plus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
        mdi = (smoothed_minus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
        adx_values.append(dx)

    adx = sum(adx_values) / len(adx_values) if adx_values else None

    return {
        "adx": round(adx, 2) if adx else None,
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
    }


def calculate_volume_ratio(candles: list[dict[str, Any]], period: int = 20) -> Optional[float]:
    """Calculate current volume vs SMA ratio.

    Returns:
        Ratio > 1 means above average volume
    """
    if len(candles) < period + 1:
        return None

    volumes = [float(c.get("baseVolume", c.get("volume", 0))) for c in candles]
    current_vol = volumes[-1]
    avg_vol = sum(volumes[-period-1:-1]) / period

    if avg_vol == 0:
        return None

    return round(current_vol / avg_vol, 2)


def calculate_all_indicators(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate all indicators from candle data.

    Args:
        candles: List of candle dicts with 'open', 'high', 'low', 'close', 'volume'

    Returns:
        Dict with all indicator values
    """
    prices = [float(c["close"]) for c in candles if c.get("close")]

    if len(prices) < 50:
        return {}

    bb = calculate_bollinger_bands(prices)
    macd = calculate_macd(prices)
    adx_data = calculate_adx(candles)

    return {
        "ema_8": calculate_ema(prices, 8),
        "ema_21": calculate_ema(prices, 21),
        "ema_50": calculate_ema(prices, 50),
        "ema_200": calculate_ema(prices, 200),
        "rsi_14": calculate_rsi(prices, 14),
        "rsi_7": calculate_rsi(prices, 7),
        "atr_14": calculate_atr(candles, 14),
        "volume_sma_ratio": calculate_volume_ratio(candles),
        "bb_upper": bb["upper"],
        "bb_lower": bb["lower"],
        "bb_width": bb["width"],
        "macd_line": macd["macd_line"],
        "macd_signal": macd["signal_line"],
        "macd_histogram": macd["histogram"],
        "adx": adx_data["adx"],
        "plus_di": adx_data["plus_di"],
        "minus_di": adx_data["minus_di"],
    }
