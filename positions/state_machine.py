"""Futures position state machine.

Strict state transitions with audit logging.
Every transition is logged immutably.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable

from signals.models import TradeStatus, ExitReason


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class StateMachine:
    """Manages valid state transitions for virtual futures positions.

    Valid transitions:
        IDLE + entry signal     -> PENDING
        PENDING + fill confirm    -> OPEN
        PENDING + time decay      -> EXPIRED
        PENDING + cancel          -> INVALID
        OPEN + TP hit             -> CLOSED
        OPEN + SL hit             -> CLOSED
        OPEN + manual close       -> CLOSED
        OPEN + signal close       -> CLOSED
        OPEN + liquidation        -> CLOSED
    """

    # Valid transitions: (from_state, trigger) -> to_state
    TRANSITIONS: dict[tuple[TradeStatus, str], TradeStatus] = {
        (TradeStatus.PENDING, "SIGNAL_RECEIVED"): TradeStatus.PENDING,
        (TradeStatus.PENDING, "FILL_CONFIRMED"): TradeStatus.OPEN,
        (TradeStatus.PENDING, "TIME_DECAY"): TradeStatus.EXPIRED,
        (TradeStatus.PENDING, "CANCELLED"): TradeStatus.INVALID,
        (TradeStatus.OPEN, "TP_HIT"): TradeStatus.CLOSED,
        (TradeStatus.OPEN, "SL_HIT"): TradeStatus.CLOSED,
        (TradeStatus.OPEN, "MANUAL_CLOSE"): TradeStatus.CLOSED,
        (TradeStatus.OPEN, "SIGNAL_CLOSE"): TradeStatus.CLOSED,
        (TradeStatus.OPEN, "LIQUIDATED"): TradeStatus.CLOSED,
    }

    def __init__(self, on_transition: Optional[Callable] = None) -> None:
        """Initialize state machine.

        Args:
            on_transition: Optional callback for transition events.
                          Called with (trade_id, from_state, to_state, trigger, price)
        """
        self.on_transition = on_transition

    def can_transition(self, current: TradeStatus, trigger: str) -> bool:
        """Check if a transition is valid without executing it."""
        return (current, trigger) in self.TRANSITIONS

    def transition(
        self,
        trade_id: str,
        current: TradeStatus,
        trigger: str,
        price: Optional[float] = None,
    ) -> tuple[TradeStatus, bool]:
        """Attempt a state transition.

        Args:
            trade_id: Unique trade identifier
            current: Current state
            trigger: What caused the transition
            price: Optional price at time of transition

        Returns:
            Tuple of (new_state, success)

        Raises:
            StateTransitionError: If transition is invalid
        """
        key = (current, trigger)

        if key not in self.TRANSITIONS:
            valid_triggers = [
                t for (s, t) in self.TRANSITIONS.keys() if s == current
            ]
            raise StateTransitionError(
                f"Invalid transition from {current.value} via '{trigger}'. "
                f"Valid triggers: {valid_triggers}"
            )

        new_state = self.TRANSITIONS[key]

        # Log the transition
        self._log_transition(trade_id, current, new_state, trigger, price)

        # Call callback if registered
        if self.on_transition:
            self.on_transition(trade_id, current, new_state, trigger, price)

        return new_state, True

    def _log_transition(
        self,
        trade_id: str,
        from_state: TradeStatus,
        to_state: TradeStatus,
        trigger: str,
        price: Optional[float],
    ) -> None:
        """Log state transition to database."""
        from database import get_db_connection

        conn = get_db_connection()
        conn.execute(
            """INSERT INTO state_transitions 
               (trade_id, from_state, to_state, trigger, price_at_transition)
               VALUES (?, ?, ?, ?, ?)""",
            (trade_id, from_state.value, to_state.value, trigger, price),
        )
        conn.commit()

    def get_valid_triggers(self, current: TradeStatus) -> list[str]:
        """Get list of valid triggers from current state."""
        return [t for (s, t) in self.TRANSITIONS.keys() if s == current]
