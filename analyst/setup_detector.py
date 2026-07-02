"""Setup detection engine.

Analyzes indicator values and detects specific trade setups.
Returns TradeCandidate objects with entry, SL, TP, and reasoning.
"""
from __future__ import annotations

from typing import Optional, Any

from .models import TradeCandidate, SetupType, IndicatorSnapshot
from .indicators import calculate_all_indicators


class SetupDetector:
    """Detects trade setups from technical indicators.

    Each detection method returns a TradeCandidate or None.
    Multiple setups can fire on the same symbol.
    """

    def __init__(self, default_leverage: int = 10, default_risk_percent: float = 1.0) -> None:
        """Initialize detector.

        Args:
            default_leverage: Default leverage for generated signals
            default_risk_percent: Risk per trade as % of notional (for SL calculation)
        """
        self.default_leverage = default_leverage
        self.default_risk_percent = default_risk_percent

    def analyze_symbol(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        funding_rate: Optional[float] = None,
    ) -> list[TradeCandidate]:
        """Analyze a symbol and return all detected trade candidates.

        Args:
            symbol: Trading pair
            candles: Recent candle data
            current_price: Current market price
            funding_rate: Current funding rate (optional)

        Returns:
            List of TradeCandidate objects
        """
        if len(candles) < 50:
            return []

        indicators = calculate_all_indicators(candles)
        if not indicators:
            return []

        indicator_snapshot = IndicatorSnapshot(**indicators)
        indicator_snapshot.funding_rate = funding_rate

        candidates = []

        # Run all detection methods
        detectors = [
            self._detect_ema_pullback,
            self._detect_ema_breakout,
            self._detect_range_play,
            self._detect_momentum_burst,
            self._detect_volume_spike,
        ]

        for detector in detectors:
            candidate = detector(
                symbol=symbol,
                candles=candles,
                current_price=current_price,
                indicators=indicator_snapshot,
            )
            if candidate:
                candidates.append(candidate)

        return candidates

    def _detect_ema_pullback(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        indicators: IndicatorSnapshot,
    ) -> Optional[TradeCandidate]:
        """Detect EMA pullback setup.

        LONG: Price pulls back to EMA21 in uptrend (EMA8 > EMA21 > EMA50)
        SHORT: Price pulls back to EMA21 in downtrend (EMA8 < EMA21 < EMA50)
        """
        if not all([indicators.ema_8, indicators.ema_21, indicators.ema_50]):
            return None

        ema8, ema21, ema50 = indicators.ema_8, indicators.ema_21, indicators.ema_50

        # LONG setup: uptrend, price near EMA21
        if ema8 > ema21 > ema50:
            # Price within 0.5% of EMA21 (pullback zone)
            if abs(current_price - ema21) / ema21 < 0.005:
                # RSI not overbought
                if indicators.rsi_14 and indicators.rsi_14 < 65:
                    entry = current_price
                    stop_loss = min(ema21 * 0.995, ema50 * 0.998)
                    take_profit = entry + (entry - stop_loss) * 2  # 1:2 R:R

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.EMA_PULLBACK,
                        side="LONG",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=65,
                        indicators=indicators,
                        thesis=f"Pullback to EMA21 in uptrend. EMA alignment: {ema8:.0f} > {ema21:.0f} > {ema50:.0f}",
                        risk_reward=2.0,
                    )

        # SHORT setup: downtrend, price near EMA21
        if ema8 < ema21 < ema50:
            if abs(current_price - ema21) / ema21 < 0.005:
                if indicators.rsi_14 and indicators.rsi_14 > 35:
                    entry = current_price
                    stop_loss = max(ema21 * 1.005, ema50 * 1.002)
                    take_profit = entry - (stop_loss - entry) * 2

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.EMA_PULLBACK,
                        side="SHORT",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=65,
                        indicators=indicators,
                        thesis=f"Pullback to EMA21 in downtrend. EMA alignment: {ema8:.0f} < {ema21:.0f} < {ema50:.0f}",
                        risk_reward=2.0,
                    )

        return None

    def _detect_ema_breakout(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        indicators: IndicatorSnapshot,
    ) -> Optional[TradeCandidate]:
        """Detect EMA breakout setup.

        LONG: Price breaks above EMA200 with volume
        SHORT: Price breaks below EMA200 with volume
        """
        if not indicators.ema_200:
            return None

        ema200 = indicators.ema_200
        prev_candles = candles[-5:-1]  # Last 4 candles before current
        if len(prev_candles) < 4:
            return None

        prev_prices = [float(c["close"]) for c in prev_candles]

        # LONG: Was below EMA200, now above
        if all(p < ema200 for p in prev_prices) and current_price > ema200 * 1.005:
            if indicators.volume_sma_ratio and indicators.volume_sma_ratio > 1.5:
                if indicators.rsi_14 and 40 < indicators.rsi_14 < 70:
                    entry = current_price
                    stop_loss = ema200 * 0.995
                    take_profit = entry + (entry - stop_loss) * 2.5

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.EMA_BREAKOUT,
                        side="LONG",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=70,
                        indicators=indicators,
                        thesis=f"Breakout above EMA200 with volume spike ({indicators.volume_sma_ratio:.1f}x)",
                        risk_reward=2.5,
                    )

        # SHORT: Was above EMA200, now below
        if all(p > ema200 for p in prev_prices) and current_price < ema200 * 0.995:
            if indicators.volume_sma_ratio and indicators.volume_sma_ratio > 1.5:
                if indicators.rsi_14 and 30 < indicators.rsi_14 < 60:
                    entry = current_price
                    stop_loss = ema200 * 1.005
                    take_profit = entry - (stop_loss - entry) * 2.5

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.EMA_BREAKOUT,
                        side="SHORT",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=70,
                        indicators=indicators,
                        thesis=f"Breakdown below EMA200 with volume spike ({indicators.volume_sma_ratio:.1f}x)",
                        risk_reward=2.5,
                    )

        return None

    def _detect_range_play(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        indicators: IndicatorSnapshot,
    ) -> Optional[TradeCandidate]:
        """Detect range-bound market setup.

        LONG at lower BB, SHORT at upper BB in ranging market.
        """
        if not all([indicators.bb_upper, indicators.bb_lower, indicators.bb_width, indicators.adx]):
            return None

        # Must be ranging (ADX < 20, BB width moderate)
        if indicators.adx > 20 or indicators.bb_width < 0.02 or indicators.bb_width > 0.08:
            return None

        bb_upper = indicators.bb_upper
        bb_lower = indicators.bb_lower

        # LONG at lower band
        if current_price <= bb_lower * 1.005:
            if indicators.rsi_14 and indicators.rsi_14 < 35:
                entry = current_price
                stop_loss = bb_lower * 0.99
                take_profit = bb_upper * 0.995

                rr = (take_profit - entry) / (entry - stop_loss) if (entry - stop_loss) > 0 else 0

                return TradeCandidate(
                    symbol=symbol,
                    setup_type=SetupType.RANGE_PLAY,
                    side="LONG",
                    entry=round(entry, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    leverage=self.default_leverage,
                    confidence=55,
                    indicators=indicators,
                    thesis=f"Range play: price at lower BB in ranging market (ADX={indicators.adx:.1f})",
                    risk_reward=round(rr, 1),
                )

        # SHORT at upper band
        if current_price >= bb_upper * 0.995:
            if indicators.rsi_14 and indicators.rsi_14 > 65:
                entry = current_price
                stop_loss = bb_upper * 1.01
                take_profit = bb_lower * 1.005

                rr = (entry - take_profit) / (stop_loss - entry) if (stop_loss - entry) > 0 else 0

                return TradeCandidate(
                    symbol=symbol,
                    setup_type=SetupType.RANGE_PLAY,
                    side="SHORT",
                    entry=round(entry, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    leverage=self.default_leverage,
                    confidence=55,
                    indicators=indicators,
                    thesis=f"Range play: price at upper BB in ranging market (ADX={indicators.adx:.1f})",
                    risk_reward=round(rr, 1),
                )

        return None

    def _detect_momentum_burst(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        indicators: IndicatorSnapshot,
    ) -> Optional[TradeCandidate]:
        """Detect momentum burst setup.

        MACD histogram expansion + ADX rising + volume spike.
        """
        if not all([indicators.macd_histogram, indicators.adx, indicators.volume_sma_ratio]):
            return None

        # Need at least 3 candles for histogram trend
        if len(candles) < 3:
            return None

        hist = indicators.macd_histogram
        prev_hist = self._calculate_macd_histogram(candles[-2])

        if hist is None or prev_hist is None:
            return None

        # Histogram expanding (momentum increasing)
        hist_expanding = hist > prev_hist * 1.1
        adx_rising = indicators.adx > 20
        volume_spike = indicators.volume_sma_ratio > 2.0

        if hist_expanding and adx_rising and volume_spike:
            # Determine direction from MACD
            if indicators.macd_line and indicators.macd_signal:
                if indicators.macd_line > indicators.macd_signal and hist > 0:
                    # Bullish momentum
                    entry = current_price
                    atr = indicators.atr_14 or (current_price * 0.01)
                    stop_loss = entry - atr * 1.5
                    take_profit = entry + atr * 3

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.MOMENTUM_BURST,
                        side="LONG",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=60,
                        indicators=indicators,
                        thesis=f"Bullish momentum burst: MACD hist expanding, ADX={indicators.adx:.1f}, vol={indicators.volume_sma_ratio:.1f}x",
                        risk_reward=2.0,
                    )

                elif indicators.macd_line < indicators.macd_signal and hist < 0:
                    # Bearish momentum
                    entry = current_price
                    atr = indicators.atr_14 or (current_price * 0.01)
                    stop_loss = entry + atr * 1.5
                    take_profit = entry - atr * 3

                    return TradeCandidate(
                        symbol=symbol,
                        setup_type=SetupType.MOMENTUM_BURST,
                        side="SHORT",
                        entry=round(entry, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        leverage=self.default_leverage,
                        confidence=60,
                        indicators=indicators,
                        thesis=f"Bearish momentum burst: MACD hist expanding, ADX={indicators.adx:.1f}, vol={indicators.volume_sma_ratio:.1f}x",
                        risk_reward=2.0,
                    )

        return None

    def _detect_volume_spike(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        current_price: float,
        indicators: IndicatorSnapshot,
    ) -> Optional[TradeCandidate]:
        """Detect volume spike with price confirmation.

        Volume > 3x average + price moving in direction of volume.
        """
        if not indicators.volume_sma_ratio or indicators.volume_sma_ratio < 3.0:
            return None

        if len(candles) < 3:
            return None

        # Check price direction of last 3 candles
        recent_closes = [float(c["close"]) for c in candles[-3:]]

        if len(recent_closes) < 3:
            return None

        # Uptrend with volume
        if recent_closes[0] < recent_closes[1] < recent_closes[2]:
            if indicators.rsi_14 and indicators.rsi_14 < 70:
                entry = current_price
                atr = indicators.atr_14 or (current_price * 0.01)
                stop_loss = entry - atr * 2
                take_profit = entry + atr * 4

                return TradeCandidate(
                    symbol=symbol,
                    setup_type=SetupType.VOLUME_SPIKE,
                    side="LONG",
                    entry=round(entry, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    leverage=self.default_leverage,
                    confidence=58,
                    indicators=indicators,
                    thesis=f"Volume spike ({indicators.volume_sma_ratio:.1f}x) with bullish price action",
                    risk_reward=2.0,
                )

        # Downtrend with volume
        if recent_closes[0] > recent_closes[1] > recent_closes[2]:
            if indicators.rsi_14 and indicators.rsi_14 > 30:
                entry = current_price
                atr = indicators.atr_14 or (current_price * 0.01)
                stop_loss = entry + atr * 2
                take_profit = entry - atr * 4

                return TradeCandidate(
                    symbol=symbol,
                    setup_type=SetupType.VOLUME_SPIKE,
                    side="SHORT",
                    entry=round(entry, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    leverage=self.default_leverage,
                    confidence=58,
                    indicators=indicators,
                    thesis=f"Volume spike ({indicators.volume_sma_ratio:.1f}x) with bearish price action",
                    risk_reward=2.0,
                )

        return None

    def _calculate_macd_histogram(self, candle: dict[str, Any]) -> Optional[float]:
        """Calculate MACD histogram for a single candle (approximate)."""
        # This is a simplified approximation - full calc needs price history
        return None
