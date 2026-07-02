"""Market scanner - fetches data and runs analysis across multiple symbols.

Autonomous scanning engine. No human input required.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Any, Callable

from exchange.bitget_client import BitgetMarketClient
from .models import MarketScan, ScanResult, TradeCandidate
from .setup_detector import SetupDetector
from .indicators import calculate_all_indicators


class MarketScanner:
    """Scans Bitget futures markets for trade setups.

    Runs autonomously on a schedule or on-demand.
    Feeds candidates into the existing signal pipeline.
    """

    # Default symbols to scan (major USDT-M perpetual futures)
    DEFAULT_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
        "MATICUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    ]

    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        timeframe: str = "1H",
        candle_limit: int = 100,
        min_confidence: float = 55.0,
        on_candidate: Optional[Callable[[TradeCandidate], None]] = None,
    ) -> None:
        """Initialize market scanner.

        Args:
            symbols: List of symbols to scan (default: major pairs)
            timeframe: Candle granularity (1m, 5m, 15m, 1H, 4H, 1D)
            candle_limit: How many candles to fetch per symbol
            min_confidence: Minimum confidence to report a candidate
            on_candidate: Callback when a candidate is detected
        """
        self.symbols = symbols or self.DEFAULT_SYMBOLS
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.min_confidence = min_confidence
        self.on_candidate = on_candidate

        self.client = BitgetMarketClient()
        self.detector = SetupDetector()
        self._running = False

    async def scan_all(self) -> MarketScan:
        """Scan all configured symbols and return results.

        Returns:
            MarketScan with all detected candidates
        """
        scan = MarketScan()

        print(f"Starting market scan: {len(self.symbols)} symbols on {self.timeframe}")

        for symbol in self.symbols:
            try:
                result = await self._scan_symbol(symbol)
                scan.results.append(result)

                # Notify callbacks for each candidate
                if self.on_candidate:
                    for candidate in result.candidates:
                        if candidate.confidence >= self.min_confidence:
                            self.on_candidate(candidate)

                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)

            except Exception as e:
                scan.results.append(ScanResult(
                    symbol=symbol,
                    error=str(e),
                ))

        scan.finalize()

        print(f"Scan complete: {scan.total_candidates} candidates from {scan.symbols_with_candidates} symbols")

        return scan

    async def _scan_symbol(self, symbol: str) -> ScanResult:
        """Scan a single symbol for trade setups.

        Args:
            symbol: Trading pair to analyze

        Returns:
            ScanResult with detected candidates
        """
        # Fetch candle data
        candles = await self.client.get_candles(
            symbol=symbol,
            granularity=self.timeframe,
            limit=self.candle_limit,
        )

        if not candles or len(candles) < 50:
            return ScanResult(
                symbol=symbol,
                error="Insufficient candle data",
            )

        # Get current price
        ticker = await self.client.get_ticker(symbol)
        if not ticker:
            return ScanResult(
                symbol=symbol,
                error="Could not fetch ticker",
            )

        current_price = ticker.last_price

        # Get funding rate
        funding_rate = await self.client.get_funding_rate(symbol)

        # Detect setups
        candidates = self.detector.analyze_symbol(
            symbol=symbol,
            candles=candles,
            current_price=current_price,
            funding_rate=funding_rate,
        )

        # Filter by confidence
        candidates = [c for c in candidates if c.confidence >= self.min_confidence]

        # Determine regime
        indicators = calculate_all_indicators(candles)
        regime = self._classify_regime(indicators)

        return ScanResult(
            symbol=symbol,
            candidates=candidates,
            regime=regime,
        )

    def _classify_regime(self, indicators: dict[str, Any]) -> str:
        """Classify market regime from indicators."""
        adx = indicators.get("adx")
        bb_width = indicators.get("bb_width")

        if adx is None:
            return "UNKNOWN"

        if adx > 25:
            ema8 = indicators.get("ema_8")
            ema21 = indicators.get("ema_21")
            if ema8 and ema21:
                if ema8 > ema21:
                    return "TRENDING_UP"
                else:
                    return "TRENDING_DOWN"

        if bb_width and bb_width < 0.03:
            return "LOW_VOLATILITY"

        if bb_width and bb_width > 0.06:
            return "HIGH_VOLATILITY"

        return "RANGING"

    async def run_scheduled(self, interval_minutes: int = 60) -> None:
        """Run scans on a schedule.

        Args:
            interval_minutes: Minutes between scans
        """
        self._running = True

        while self._running:
            try:
                await self.scan_all()
            except Exception as e:
                print(f"Scheduled scan error: {e}")

            # Wait for next scan
            for _ in range(interval_minutes * 60):
                if not self._running:
                    break
                await asyncio.sleep(1)

    def stop(self) -> None:
        """Stop scheduled scanning."""
        self._running = False

    async def close(self) -> None:
        """Clean up resources."""
        await self.client.close()
