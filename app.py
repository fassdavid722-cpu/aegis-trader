"""Aegis Trader - Pure Price Action Futures Intelligence Engine.

Entry point. Orchestrates all modules.
Strategy: No indicators. Pure price structure + funding + session timing.
"""
from __future__ import annotations

import asyncio
import signal as sys_signal
from datetime import datetime, timezone
from typing import Optional

from config import get_config
from database import init_database
from signals import Signal, TelegramSignalListener
from positions import PositionManager
from exchange import BitgetMarketClient, MarketMonitor
from coach import CoachEngine
from journal.models import JournalWriter
from analyst import (
    MarketScannerV2, AnalystSignalBridgeV2, TradeCandidate,
    SessionFilter, MarketRegime, FundingBias,
)


class AegisTrader:
    """Main orchestrator - Pure Price Action version."""

    def __init__(self) -> None:
        self.config = get_config()

        # Core
        self.position_manager = PositionManager()
        self.market_client = BitgetMarketClient()
        self.market_monitor: Optional[MarketMonitor] = None
        self.coach = CoachEngine()
        self.journal = JournalWriter()

        # Pure Price Action Analyst
        self.analyst_bridge = AnalystSignalBridgeV2(self.position_manager)
        self.market_scanner: Optional[MarketScannerV2] = None
        self._analyst_task: Optional[asyncio.Task] = None

        # Telegram
        self.telegram: Optional[TelegramSignalListener] = None

        # State
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._daily_reset_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the engine."""
        print("="*60)
        print("AEGIS TRADER - Pure Price Action Engine")
        print("Strategy: Structure + Funding + Session | No Indicators")
        print("="*60)

        init_database()

        self.market_monitor = MarketMonitor(self.position_manager)

        # Telegram
        if self.config.telegram.enabled:
            self.telegram = TelegramSignalListener(
                on_signal=self._handle_entry_signal,
                on_close_signal=self._handle_close_signal,
            )
            await self.telegram.start()

        # Pure Price Action Scanner
        self.market_scanner = MarketScannerV2(
            on_candidate=self._handle_analyst_candidate,
        )

        # Background tasks
        self._running = True
        self._monitor_task = asyncio.create_task(self._run_monitor())
        self._analyst_task = asyncio.create_task(self._run_analyst())
        self._daily_reset_task = asyncio.create_task(self._run_daily_reset())

        print(f"Session: {SessionFilter.get_session_name()}")
        print("Aegis Trader is running")

    async def stop(self) -> None:
        """Graceful shutdown."""
        print("Shutting down...")
        self._running = False

        for task in [self._monitor_task, self._analyst_task, self._daily_reset_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self.market_monitor:
            self.market_monitor.stop()
        if self.market_scanner:
            self.market_scanner.stop()
        if self.telegram:
            await self.telegram.stop()

        await self.market_client.close()
        if self.market_scanner:
            await self.market_scanner.close()

        print("Shutdown complete")

    def _handle_entry_signal(self, signal: Signal) -> None:
        """Process manual/Telegram entry signal."""
        try:
            position = self.position_manager.create_position(signal)
            if position:
                self.market_monitor.add_symbol(position.symbol)
                self.journal.log_system_event(
                    "INFO", "MANUAL_SIGNAL",
                    f"Position: {position.trade_id} | {position.symbol}"
                )
        except Exception as e:
            self.journal.log_system_event("ERROR", "MANUAL_SIGNAL", str(e))

    def _handle_close_signal(self, signal: Signal) -> None:
        """Process close signal."""
        position = self.position_manager.handle_close_signal(signal)
        if position:
            pass

    def _handle_analyst_candidate(self, candidate: TradeCandidate) -> None:
        """Handle PA candidate from Analyst Engine."""
        print(f"\n{'='*50}")
        print(f"ANALYST CANDIDATE DETECTED")
        print(f"{'='*50}")
        print(f"{candidate.side} {candidate.symbol}")
        print(f"Entry: {candidate.entry} | SL: {candidate.stop_loss}")
        print(f"TP1: {candidate.take_profit_1} (1.5R) | TP2: {candidate.take_profit_2} (3R)")
        print(f"Regime: {candidate.regime.value} | Session: {candidate.session.value}")
        print(f"Structure: {candidate.structure.value}")
        print(f"Funding: {candidate.funding_bias.value}")
        print(f"Confluence: {candidate.confluence_score}/5 | Confidence: {candidate.confidence}%")
        print(f"Thesis: {candidate.thesis}")
        print(f"{'='*50}\n")

        # Submit to bridge
        signal = self.analyst_bridge.submit_candidate(candidate)

        if signal:
            self.market_monitor.add_symbol(signal.symbol)

            # Log
            self.journal.log_system_event(
                "INFO", "ANALYST",
                f"PA Setup: {candidate.side} {candidate.symbol} | "
                f"Regime: {candidate.regime.value} | "
                f"Structure: {candidate.structure.value} | "
                f"Funding: {candidate.funding_bias.value} | "
                f"Confidence: {candidate.confidence}%"
            )

            # Telegram notification
            if self.telegram:
                zone_info = f"Zone: {candidate.zone.zone_type.value} {candidate.zone.bottom:.0f}-{candidate.zone.top:.0f}" if candidate.zone else "No zone"

                msg = (
                    f"Analyst Signal: {candidate.side} {candidate.symbol}\n"
                    f"Entry: {candidate.entry:.2f}\n"
                    f"SL: {candidate.stop_loss:.2f} | TP1: {candidate.take_profit_1:.2f} | TP2: {candidate.take_profit_2:.2f}\n"
                    f"Regime: {candidate.regime.value}\n"
                    f"Session: {candidate.session.value}\n"
                    f"Structure: {candidate.structure.value}\n"
                    f"Funding: {candidate.funding_bias.value}\n"
                    f"{zone_info}\n"
                    f"Confluence: {candidate.confluence_score}/5 | Confidence: {candidate.confidence}%\n"
                    f"Thesis: {candidate.thesis}"
                )

                asyncio.create_task(
                    self.telegram.send_notification(self.config.telegram.chat_id, msg)
                )

    async def _run_monitor(self) -> None:
        """Monitor markets and manage positions."""
        while self._running:
            try:
                open_positions = self.position_manager.get_open_positions()
                pending_positions = self.position_manager.get_pending_positions()

                all_symbols = list(set([p.symbol for p in open_positions + pending_positions]))

                if not all_symbols:
                    await asyncio.sleep(5)
                    continue

                for symbol in all_symbols:
                    self.market_monitor.add_symbol(symbol)

                await self.market_monitor.start()

            except Exception as e:
                self.journal.log_system_event("ERROR", "MONITOR", str(e))
                await asyncio.sleep(10)

    async def _run_analyst(self) -> None:
        """Run PA analyst scans every 5 minutes."""
        await asyncio.sleep(10)  # Initial delay

        while self._running:
            try:
                if SessionFilter.is_trade_session():
                    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Running PA scan...")
                    scan = await self.market_scanner.scan_all()

                    self.journal.log_system_event(
                        "INFO", "ANALYST",
                        f"PA Scan: {scan.total_candidates} candidates from {scan.symbols_with_candidates} symbols"
                    )

                    # Notify summary
                    if self.telegram and scan.total_candidates > 0:
                        candidates_text = "\n".join([
                            f"- {c.side} {c.symbol} ({c.structure.value}, {c.confidence}%)"
                            for r in scan.results for c in r.candidates
                        ])

                        asyncio.create_task(
                            self.telegram.send_notification(
                                self.config.telegram.chat_id,
                                f"PA Scan Complete\n"
                                f"Session: {SessionFilter.get_session_name()}\n"
                                f"Scanned: {scan.symbols_scanned}\n"
                                f"Candidates: {scan.total_candidates}\n"
                                f"Setups:\n{candidates_text[:800]}"
                            )
                        )
                else:
                    print(f"Waiting for trade session. Current: {SessionFilter.get_session_name()}")

            except Exception as e:
                self.journal.log_system_event("ERROR", "ANALYST", str(e))

            # 5-minute interval
            for _ in range(300):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _run_daily_reset(self) -> None:
        """Reset daily stats at midnight UTC."""
        while self._running:
            now = datetime.now(timezone.utc)

            # Check if it's midnight
            if now.hour == 0 and now.minute == 0:
                if self.market_scanner:
                    self.market_scanner.reset_daily_stats()
                    print("Daily stats reset")
                await asyncio.sleep(60)  # Wait a minute to avoid double-trigger

            await asyncio.sleep(30)


async def main() -> None:
    app = AegisTrader()

    loop = asyncio.get_event_loop()
    for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))

    await app.start()

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
