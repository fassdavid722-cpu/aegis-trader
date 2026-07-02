"""Normalize Bitget candle arrays to dict format.

Bitget returns: [timestamp, open, high, low, close, baseVolume, quoteVolume]
We need: {"timestamp": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


def normalize_candles(raw: list[list]) -> list[dict[str, Any]]:
    """Convert Bitget raw candle arrays to dicts."""
    result = []
    for c in raw:
        if len(c) < 5:
            continue
        ts_ms = int(c[0])
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        result.append({
            "timestamp": dt.isoformat(),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]) if len(c) > 5 else 0.0,
        })
    return result
