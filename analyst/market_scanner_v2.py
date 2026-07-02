"""Market scanner for pure price action strategy.

Scans symbols using 4H regime + 15min structure + funding.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Any, Callable

from exchange.bitget_client import BitgetMarketClient
from .models_v2 import MarketScan, ScanResult, TradeCandidate, MarketRegime
from .setup_detector_v2 import SetupDetectorV2
from .session_filter import SessionFilter


class MarketScannerV2:
    """Scans Bitget futures for pure price action setups.

    Uses dual timeframe analysis:
    - 4H candles for regime detection
    - 15min candles for zones and structure
    """

    DEFAULT_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    ]

    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        min_confidence: float = 60.0,
        on_candidate: Optional[Callable[[TradeCandidate], None]] = None,
    ) -> None:
        """Initialize scanner.

        Args:
            symbols: Symbols to scan
            min_confidence: Minimum confidence to report
            on_candidate: Callback for detected candidates
        """
        self.symbols = symbols or self.DEFAULT_SYMBOLS
        self.min_confidence = min_confidence
        self.on_candidate = on_candidate

        self.client = BitgetMarketClient()
        self.detector = SetupDetectorV2()
        self._running = False

        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_loss_limit = -3.0  # -3% daily stop
        self.trades_today = 0
        self.max_trades_per_day = 3

    async def scan_all(self) -> MarketScan:
        """Scan all symbols."""
        scan = MarketScan()
        scan.daily_pnl_percent = self.daily_pnl
        scan.trading_halted = self._is_trading_halted()

        if scan.trading_halted:
            print(f"Trading halted. Daily PnL: {self.daily_pnl}%")
            return scan

        # Check session first
        if not SessionFilter.is_trade_session():
            print(f"Not in trade session. Current: {SessionFilter.get_session_name()}")
            return scan

        print(f"Starting PA scan: {len(self.symbols)} symbols | Session: {SessionFilter.get_session_name()}")

        for symbol in self.symbols:
            try:
                result = await self._scan_symbol(symbol)
                scan.results.append(result)

                if self.on_candidate:
                    for candidate in result.candidates:
                        if candidate.confidence >= self.min_confidence:
                            self.on_candidate(candidate)

                await asyncio.sleep(0.5)

            except Exception as e:
                scan.results.append(ScanResult(
                    symbol=symbol,
                    error=str(e),
                    regime=MarketRegime.UNKNOWN,
                    session=SessionFilter.get_current_session(),
                    funding_bias="NEUTRAL",
                ))

        scan.finalize()
        print(f"PA scan complete: {scan.total_candidates} candidates")

        return scan

    async def _scan_symbol(self, symbol: str) -> ScanResult:
        """Scan single symbol with dual timeframes."""
        # Fetch 4H candles for regime
        candles_4h = await self.client.get_candles(
            symbol=symbol,
            granularity="4H",
            limit=30,
        )

        # Fetch 15min candles for structure
        candles_15m = await self.client.get_candles(
            symbol=symbol,
            granularity="15m",
            limit=100,
        )

        if not candles_4h or not candles_15m:
            return ScanResult(
                symbol=symbol,
                error="Insufficient candle data",
                regime=MarketRegime.UNKNOWN,
                session=SessionFilter.get_current_session(),
                funding_bias="NEUTRAL",
            )

        # Get current price
        ticker = await self.client.get_ticker(symbol)
        if not ticker:
            return ScanResult(
                symbol=symbol,
                error="Could not fetch ticker",
                regime=MarketRegime.UNKNOWN,
                session=SessionFilter.get_current_session(),
                funding_bias="NEUTRAL",
            )

        current_price = ticker.last_price

        # Get funding rate
        funding_rate = await self.client.get_funding_rate(symbol)

        # Detect setups — pass open positions for Q2 conflict resolution
        open_positions = await self._get_open_positions(symbol)
        candidates = self.detector.analyze_symbol(
            symbol=symbol,
            candles_4h=candles_4h,
            candles_15m=candles_15m,
            current_price=current_price,
            funding_rate=funding_rate,
            open_positions=open_positions,
        )

        # Filter by confidence
        candidates = [c for c in candidates if c.confidence >= self.min_confidence]

        # Count trades
        self.trades_today += len(candidates)

        from .models_v2 import FundingBias
        from .funding_filter import FundingFilter

        return ScanResult(
            symbol=symbol,
            regime=self.detector.regime_detector.detect_regime(candles_4h),
            session=SessionFilter.get_current_session(),
            funding_rate=funding_rate,
            funding_bias=FundingFilter.interpret(funding_rate),
            candidates=candidates,
        )

    async def _get_open_positions(self, symbol: str) -> list[dict]:
        """Fetch open positions for conflict resolution."""
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "journal.db"
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, direction, status FROM trades WHERE symbol=? AND status IN ('OPEN','PARTIAL')",
            (symbol,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _is_trading_halted(self) -> bool:
        """Check if daily loss limit hit."""
        return self.daily_pnl <= self.daily_loss_limit or self.trades_today >= self.max_trades_per_day

    def update_daily_pnl(self, pnl_percent: float) -> None:
        """Update daily PnL tracking."""
        self.daily_pnl += pnl_percent

    def reset_daily_stats(self) -> None:
        """Reset daily counters."""
        self.daily_pnl = 0.0
        self.trades_today = 0

    async def run_scheduled(self, interval_minutes: int = 5) -> None:
        """Run scans every 5 minutes during trade sessions."""
        self._running = True

        while self._running:
            try:
                # Only scan during valid sessions
                if SessionFilter.is_trade_session() and not self._is_trading_halted():
                    await self.scan_all()
                else:
                    if not SessionFilter.is_trade_session():
                        print(f"Waiting for trade session. Current: {SessionFilter.get_session_name()}")
                    elif self._is_trading_halted():
                        print(f"Trading halted. PnL: {self.daily_pnl}% | Trades: {self.trades_today}")

            except Exception as e:
                print(f"Scheduled scan error: {e}")

            # Wait 5 minutes
            for _ in range(interval_minutes * 60):
                if not self._running:
                    break
                await asyncio.sleep(1)

    def stop(self) -> None:
        """Stop scanning."""
        self._running = False

    async def close(self) -> None:
        """Clean up."""
        await self.client.close()
