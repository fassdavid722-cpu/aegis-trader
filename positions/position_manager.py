"""Virtual futures position manager.

Creates, tracks, and closes virtual positions.
No real orders. No live execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from config import get_config
from signals.models import (
    Signal, VirtualPosition, TradeStatus, TradeSide, ExitReason,
    TradeResult, MarginMode
)
from .state_machine import StateMachine, StateTransitionError


class DuplicatePositionError(Exception):
    """Raised when trying to open a position for a symbol that already has one."""
    pass


class PositionNotFoundError(Exception):
    """Raised when a position operation references a non-existent trade."""
    pass


class PositionManager:
    """Manages all virtual futures positions.

    In-memory tracking with database persistence.
    One open position per symbol by default.
    """

    def __init__(self) -> None:
        """Initialize position manager."""
        self.config = get_config()
        self.state_machine = StateMachine()
        self._positions: dict[str, VirtualPosition] = {}  # trade_id -> position
        self._symbol_index: dict[str, str] = {}  # symbol -> trade_id (open positions)

    def create_position(self, signal: Signal) -> Optional[VirtualPosition]:
        """Create a new virtual position from an entry signal.

        Args:
            signal: Parsed entry signal

        Returns:
            VirtualPosition if created, None if signal is not an entry

        Raises:
            DuplicatePositionError: If symbol already has open position
        """
        if not signal.is_entry_signal():
            return None

        # Check for duplicate open position
        if signal.symbol in self._symbol_index:
            existing_id = self._symbol_index[signal.symbol]
            existing = self._positions.get(existing_id)
            if existing and existing.status in (TradeStatus.PENDING, TradeStatus.OPEN):
                raise DuplicatePositionError(
                    f"Open position already exists for {signal.symbol} "
                    f"(trade_id: {existing_id})"
                )

        # Build virtual position
        position = VirtualPosition(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            contract_type=signal.contract_type,
            direction=signal.side,  # LONG or SHORT
            leverage=signal.leverage,
            margin_mode=signal.margin_mode,
            entry_price=signal.entry or 0.0,  # Will be updated on fill
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status=TradeStatus.PENDING,
            signal_raw=signal.raw_text,
            signal_source=signal.source.value,
            confidence_score=signal.confidence,
        )

        # Calculate liquidation price
        position.calculate_liquidation_price()
        position.calculate_margin()

        # Store in memory
        self._positions[position.trade_id] = position
        self._symbol_index[position.symbol] = position.trade_id

        # Persist to database
        self._persist_signal(signal)
        self._persist_position(position)

        return position

    def activate_position(self, trade_id: str, fill_price: float) -> VirtualPosition:
        """Activate a pending position (simulated fill).

        Args:
            trade_id: The trade to activate
            fill_price: The actual fill price

        Returns:
            Updated VirtualPosition
        """
        position = self._get_position(trade_id)

        # State transition: PENDING -> OPEN
        new_state, _ = self.state_machine.transition(
            trade_id, position.status, "FILL_CONFIRMED", fill_price
        )
        position.status = new_state
        position.entry_price = fill_price
        position.opened_at = datetime.now(timezone.utc)

        # Recalculate with actual fill price
        position.calculate_liquidation_price()
        position.calculate_margin()

        # Update database
        self._update_position(position)

        return position

    def check_and_close(
        self,
        trade_id: str,
        current_price: float,
    ) -> Optional[VirtualPosition]:
        """Check if position should close and execute if so.

        Args:
            trade_id: Trade to check
            current_price: Current market price

        Returns:
            Closed VirtualPosition if closed, None if still open
        """
        position = self._get_position(trade_id)

        if position.status != TradeStatus.OPEN:
            return None

        # Check for exit trigger
        exit_reason = position.check_exit(current_price)

        if exit_reason:
            return self.close_position(trade_id, current_price, exit_reason)

        # Update MFE/MAE tracking
        self._update_mfe_mae(position, current_price)

        return None

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        reason: ExitReason,
    ) -> VirtualPosition:
        """Close a position manually or by signal.

        Args:
            trade_id: Trade to close
            exit_price: Exit price
            reason: Why the position closed

        Returns:
            Closed VirtualPosition
        """
        position = self._get_position(trade_id)

        if position.status != TradeStatus.OPEN:
            raise StateTransitionError(
                f"Cannot close position {trade_id}: status is {position.status.value}"
            )

        # Determine trigger string for state machine
        trigger_map = {
            ExitReason.TP_HIT: "TP_HIT",
            ExitReason.SL_HIT: "SL_HIT",
            ExitReason.MANUAL_CLOSE: "MANUAL_CLOSE",
            ExitReason.SIGNAL_CLOSE: "SIGNAL_CLOSE",
            ExitReason.LIQUIDATED: "LIQUIDATED",
        }
        trigger = trigger_map.get(reason, "MANUAL_CLOSE")

        # State transition
        new_state, _ = self.state_machine.transition(
            trade_id, position.status, trigger, exit_price
        )
        position.status = new_state

        # Calculate final metrics
        position.close(exit_price, reason, self.config.trading.trading_fee_rate)

        # Remove from symbol index
        if position.symbol in self._symbol_index:
            del self._symbol_index[position.symbol]

        # Update database
        self._update_position(position)

        return position

    def handle_close_signal(self, signal: Signal) -> Optional[VirtualPosition]:
        """Handle a CLOSE signal by closing the matching open position.

        Args:
            signal: Close signal (side=CLOSE)

        Returns:
            Closed VirtualPosition if found, None if no open position
        """
        if not signal.is_close_signal():
            return None

        # Find open position for this symbol
        trade_id = self._symbol_index.get(signal.symbol)
        if not trade_id:
            return None

        # Use current entry price as exit price if not specified
        position = self._positions.get(trade_id)
        if not position:
            return None

        # For close signals without price, we need market data
        # This will be handled by the market monitor
        return position  # Return for external closure with market price

    def get_open_positions(self) -> list[VirtualPosition]:
        """Get all currently open positions."""
        return [
            p for p in self._positions.values()
            if p.status == TradeStatus.OPEN
        ]

    def get_pending_positions(self) -> list[VirtualPosition]:
        """Get all pending positions."""
        return [
            p for p in self._positions.values()
            if p.status == TradeStatus.PENDING
        ]

    def get_position_by_symbol(self, symbol: str) -> Optional[VirtualPosition]:
        """Get open position for a specific symbol."""
        trade_id = self._symbol_index.get(symbol)
        if trade_id:
            return self._positions.get(trade_id)
        return None

    def get_position(self, trade_id: str) -> Optional[VirtualPosition]:
        """Get any position by trade_id."""
        return self._positions.get(trade_id)

    def expire_pending(self, trade_id: str) -> Optional[VirtualPosition]:
        """Expire a pending position due to time decay.

        Args:
            trade_id: Pending trade to expire

        Returns:
            Expired VirtualPosition, or None if not pending
        """
        position = self._get_position(trade_id)

        if position.status != TradeStatus.PENDING:
            return None

        new_state, _ = self.state_machine.transition(
            trade_id, position.status, "TIME_DECAY"
        )
        position.status = new_state
        position.result = TradeResult.INVALID
        position.notes = "Expired: entry time decay"

        # Remove from symbol index
        if position.symbol in self._symbol_index:
            del self._symbol_index[position.symbol]

        self._update_position(position)

        return position

    def _get_position(self, trade_id: str) -> VirtualPosition:
        """Internal: get position or raise."""
        position = self._positions.get(trade_id)
        if not position:
            raise PositionNotFoundError(f"Position not found: {trade_id}")
        return position

    def _update_mfe_mae(self, position: VirtualPosition, current_price: float) -> None:
        """Update max favorable and adverse excursion."""
        if position.direction == TradeSide.LONG:
            favorable = current_price - position.entry_price
            adverse = position.entry_price - current_price
        else:
            favorable = position.entry_price - current_price
            adverse = current_price - position.entry_price

        position.max_favorable_excursion = max(position.max_favorable_excursion, favorable)
        position.max_adverse_excursion = max(position.max_adverse_excursion, adverse)

    def _persist_signal(self, signal: Signal) -> None:
        """Persist signal to database."""
        from database import get_db_connection

        conn = get_db_connection()
        data = signal.to_db_dict()

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))

        conn.execute(
            f"INSERT OR IGNORE INTO signals ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        conn.commit()

    def _persist_position(self, position: VirtualPosition) -> None:
        """Persist new position to database."""
        from database import get_db_connection

        conn = get_db_connection()
        data = position.to_db_dict()

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))

        conn.execute(
            f"INSERT INTO trades ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        conn.commit()

    def _update_position(self, position: VirtualPosition) -> None:
        """Update existing position in database."""
        from database import get_db_connection

        conn = get_db_connection()
        data = position.to_db_dict()

        # Build SET clause (exclude trade_id)
        set_clauses = []
        values = []
        for key, value in data.items():
            if key != "trade_id":
                set_clauses.append(f"{key} = ?")
                values.append(value)

        values.append(data["trade_id"])

        conn.execute(
            f"UPDATE trades SET {', '.join(set_clauses)} WHERE trade_id = ?",
            tuple(values),
        )
        conn.commit()
