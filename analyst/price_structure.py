"""Pure price structure analysis.

No indicators. Just what price actually does.
- Swing highs/lows
- Break of Structure (BOS)
- Change of Character (CHOCH)
- Supply & Demand zones
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from .models_v2 import SwingPoint, StructureEvent, StructureType, PriceZone, ZoneType


class PriceStructureAnalyzer:
    """Analyzes raw price data for structure events.

    Uses only OHLC data. No indicators. No lag.
    """

    def __init__(self, swing_lookback: int = 5) -> None:
        """Initialize analyzer.

        Args:
            swing_lookback: How many candles to look back for swing confirmation
        """
        self.swing_lookback = swing_lookback

    def find_swing_points(self, candles: list[dict[str, Any]]) -> list[SwingPoint]:
        """Find significant swing highs and lows.

        A swing high: higher than N candles before AND after
        A swing low: lower than N candles before AND after

        Args:
            candles: List of candle dicts with 'high', 'low', 'close', 'timestamp'

        Returns:
            List of SwingPoint objects
        """
        if len(candles) < self.swing_lookback * 2 + 1:
            return []

        swings = []
        n = self.swing_lookback

        for i in range(n, len(candles) - n):
            current_high = float(candles[i]["high"])
            current_low = float(candles[i]["low"])

            # Check left side
            left_highs = [float(candles[j]["high"]) for j in range(i - n, i)]
            left_lows = [float(candles[j]["low"]) for j in range(i - n, i)]

            # Check right side
            right_highs = [float(candles[j]["high"]) for j in range(i + 1, i + n + 1)]
            right_lows = [float(candles[j]["low"]) for j in range(i + 1, i + n + 1)]

            # Swing high
            if all(current_high > h for h in left_highs) and all(current_high > h for h in right_highs):
                swings.append(SwingPoint(
                    is_high=True,
                    price=current_high,
                    timestamp=datetime.fromisoformat(candles[i].get("timestamp", "").replace("Z", "+00:00")) if candles[i].get("timestamp") else datetime.now(timezone.utc),
                    index=i,
                ))

            # Swing low
            elif all(current_low < l for l in left_lows) and all(current_low < l for l in right_lows):
                swings.append(SwingPoint(
                    is_high=False,
                    price=current_low,
                    timestamp=datetime.fromisoformat(candles[i].get("timestamp", "").replace("Z", "+00:00")) if candles[i].get("timestamp") else datetime.now(timezone.utc),
                    index=i,
                ))

        return swings

    def detect_structure(self, candles: list[dict[str, Any]]) -> Optional[StructureEvent]:
        """Detect BOS or CHOCH from recent price action.

        Args:
            candles: Recent candle data (at least 20 candles)

        Returns:
            StructureEvent if detected, None otherwise
        """
        if len(candles) < 20:
            return None

        swings = self.find_swing_points(candles)
        if len(swings) < 4:
            return None

        # Get recent swings (last 4)
        recent = swings[-4:]
        current_price = float(candles[-1]["close"])

        # Identify pattern: High-Low-High-Low or Low-High-Low-High
        # For BOS/CHOCH we need the last significant swing point

        last_swing = recent[-1]
        prev_swing = recent[-2]
        prev_prev = recent[-3]

        # BOS BULL: In uptrend, price breaks above last swing high
        if self._is_uptrend(recent):
            last_high = max(s.price for s in recent if s.is_high)
            if current_price > last_high * 1.002:  # 0.2% break
                return StructureEvent(
                    event_type=StructureType.BOS_BULL,
                    trigger_price=current_price,
                    reference_price=last_high,
                    timestamp=datetime.now(timezone.utc),
                    confirmed=True,
                )

            # CHOCH BEAR: In uptrend, price breaks below last swing low
            last_low = max((s.price for s in recent if not s.is_high), default=0)
            if last_low > 0 and current_price < last_low * 0.998:
                return StructureEvent(
                    event_type=StructureType.CHOCH_BEAR,
                    trigger_price=current_price,
                    reference_price=last_low,
                    timestamp=datetime.now(timezone.utc),
                    confirmed=True,
                )

        # BOS BEAR: In downtrend, price breaks below last swing low
        if self._is_downtrend(recent):
            last_low = min(s.price for s in recent if not s.is_high) if any(not s.is_high for s in recent) else 0
            if last_low > 0 and current_price < last_low * 0.998:
                return StructureEvent(
                    event_type=StructureType.BOS_BEAR,
                    trigger_price=current_price,
                    reference_price=last_low,
                    timestamp=datetime.now(timezone.utc),
                    confirmed=True,
                )

            # CHOCH BULL: In downtrend, price breaks above last swing high
            last_high = min((s.price for s in recent if s.is_high), default=0)
            if last_high > 0 and current_price > last_high * 1.002:
                return StructureEvent(
                    event_type=StructureType.CHOCH_BULL,
                    trigger_price=current_price,
                    reference_price=last_high,
                    timestamp=datetime.now(timezone.utc),
                    confirmed=True,
                )

        return None

    def find_zones(self, candles: list[dict[str, Any]], lookback: int = 50) -> list[PriceZone]:
        """Find supply and demand zones from recent price action.

        Demand zone: area where price pumped sharply (strong bullish candle)
        Supply zone: area where price dropped sharply (strong bearish candle)

        Args:
            candles: Candle data
            lookback: How many candles to analyze for zones

        Returns:
            List of active PriceZone objects
        """
        if len(candles) < lookback + 10:
            return []

        zones = []
        recent_candles = candles[-lookback:]

        for i in range(len(recent_candles) - 1):
            c = recent_candles[i]
            open_p = float(c["open"])
            high_p = float(c["high"])
            low_p = float(c["low"])
            close_p = float(c["close"])

            body = abs(close_p - open_p)
            range_p = high_p - low_p

            if range_p == 0:
                continue

            body_ratio = body / range_p

            # Strong bullish candle → creates demand zone
            if close_p > open_p and body_ratio > 0.6 and body > 0:
                # Zone is the body of the candle + wick
                zone_bottom = min(open_p, close_p)
                zone_top = high_p

                zones.append(PriceZone(
                    zone_type=ZoneType.DEMAND,
                    top=round(zone_top, 2),
                    bottom=round(zone_bottom, 2),
                    created_at=datetime.now(timezone.utc),
                    source_candle_high=high_p,
                    source_candle_low=low_p,
                    source_candle_close=close_p,
                ))

            # Strong bearish candle → creates supply zone
            elif close_p < open_p and body_ratio > 0.6 and body > 0:
                zone_top = max(open_p, close_p)
                zone_bottom = low_p

                zones.append(PriceZone(
                    zone_type=ZoneType.SUPPLY,
                    top=round(zone_top, 2),
                    bottom=round(zone_bottom, 2),
                    created_at=datetime.now(timezone.utc),
                    source_candle_high=high_p,
                    source_candle_low=low_p,
                    source_candle_close=close_p,
                ))

        # Filter: only keep zones that haven't been violated
        # A zone is violated if price closed through it after creation
        active_zones = []
        for zone in zones:
            violated = False
            for c in candles[-20:]:  # Check last 20 candles
                close_p = float(c["close"])
                if zone.zone_type == ZoneType.DEMAND and close_p < zone.bottom:
                    violated = True
                    break
                elif zone.zone_type == ZoneType.SUPPLY and close_p > zone.top:
                    violated = True
                    break

            if not violated:
                zone.is_fresh = True
                active_zones.append(zone)

        # Sort by freshness (most recent first) and limit
        active_zones.sort(key=lambda z: z.created_at, reverse=True)
        return active_zones[:5]  # Keep top 5 most recent zones

    def _is_uptrend(self, swings: list[SwingPoint]) -> bool:
        """Check if recent swings show uptrend (HH + HL)."""
        if len(swings) < 4:
            return False

        highs = [s.price for s in swings if s.is_high]
        lows = [s.price for s in swings if not s.is_high]

        if len(highs) < 2 or len(lows) < 2:
            return False

        # Higher highs and higher lows
        return highs[-1] > highs[-2] and lows[-1] > lows[-2]

    def _is_downtrend(self, swings: list[SwingPoint]) -> bool:
        """Check if recent swings show downtrend (LH + LL)."""
        if len(swings) < 4:
            return False

        highs = [s.price for s in swings if s.is_high]
        lows = [s.price for s in swings if not s.is_high]

        if len(highs) < 2 or len(lows) < 2:
            return False

        # Lower highs and lower lows
        return highs[-1] < highs[-2] and lows[-1] < lows[-2]
