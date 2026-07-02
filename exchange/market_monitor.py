"""Market monitor that connects price updates to position management.

Bridges the exchange client with the position manager.
"""
from __future__ import annotations

from typing import Optional

from positions import PositionManager
from exchange.bitget_client import BitgetMarketClient, BitgetPriceMonitor
from signals.models import TradeStatus, ExitReason


class MarketMonitor:
    """Monitors market data and manages virtual position lifecycle.

    Coordinates between:
    - PriceMonitor (gets prices from Bitget)
    - PositionManager (tracks virtual positions)
    - Journal (records outcomes)
    """

    def __init__(
        self,
        position_manager: PositionManager,
        price_monitor: Optional[BitgetPriceMonitor] = None,
    ) -> None:
        """Initialize market monitor.

        Args:
            position_manager: Manages virtual positions
            price_monitor: Price monitoring client (created if None)
        """
        self.position_manager = position_manager
        self.client = BitgetMarketClient()
        self.price_monitor = price_monitor or BitgetPriceMonitor(self.client)

        # Register price callback
        self.price_monitor.register_callback(self._on_price_update)

        # Track which symbols need monitoring
        self._monitored_symbols: set[str] = set()

    def _on_price_update(self, symbol: str, price: float) -> None:
        """Handle price update for a symbol.

        Checks all open positions for this symbol.
        """
        # Check for open positions on this symbol
        position = self.position_manager.get_position_by_symbol(symbol)
        if not position:
            return

        if position.status == TradeStatus.OPEN:
            # Check if TP/SL hit
            closed = self.position_manager.check_and_close(
                position.trade_id, price
            )
            if closed:
                print(f"Position closed: {symbol} @ {price} | Reason: {closed.exit_reason.value} | PnL: {closed.pnl_percent}%")

        elif position.status == TradeStatus.PENDING:
            # Check if entry conditions met (fill model)
            self._check_fill(position, price)

    def _check_fill(self, position, current_price: float) -> None:
        """Check if a pending position should be filled.

        Simple fill model: if current price is within acceptable range of entry.
        """
        from config import get_config
        config = get_config()

        if position.entry_price is None or position.entry_price <= 0:
            # No entry price specified - use current price as fill
            self.position_manager.activate_position(position.trade_id, current_price)
            return

        entry = position.entry_price
        slippage = config.trading.max_slippage

        # For LONG: fill if price <= entry * (1 + slippage)
        # For SHORT: fill if price >= entry * (1 - slippage)
        if position.direction.value == "LONG":
            max_acceptable = entry * (1 + slippage)
            if current_price <= max_acceptable:
                self.position_manager.activate_position(position.trade_id, current_price)
        else:  # SHORT
            min_acceptable = entry * (1 - slippage)
            if current_price >= min_acceptable:
                self.position_manager.activate_position(position.trade_id, current_price)

    def add_symbol(self, symbol: str) -> None:
        """Add a symbol to the monitoring list."""
        self._monitored_symbols.add(symbol)

    def remove_symbol(self, symbol: str) -> None:
        """Remove a symbol from monitoring."""
        self._monitored_symbols.discard(symbol)

    async def start(self) -> None:
        """Start monitoring all tracked symbols."""
        if not self._monitored_symbols:
            print("No symbols to monitor")
            return

        symbols = list(self._monitored_symbols)
        print(f"Starting market monitor for: {symbols}")
        await self.price_monitor.monitor_symbols(symbols)

    def stop(self) -> None:
        """Stop monitoring."""
        self.price_monitor.stop()
