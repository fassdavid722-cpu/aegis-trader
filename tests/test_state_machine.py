"""Tests for position state machine."""
import pytest

from positions.state_machine import StateMachine, StateTransitionError
from signals.models import TradeStatus


class TestStateMachine:
    """Test state transitions."""

    def setup_method(self):
        self.sm = StateMachine()

    def test_pending_to_open(self):
        new_state, success = self.sm.transition("test-1", TradeStatus.PENDING, "FILL_CONFIRMED", 100.0)
        assert new_state == TradeStatus.OPEN
        assert success is True

    def test_open_to_closed_tp(self):
        new_state, success = self.sm.transition("test-1", TradeStatus.OPEN, "TP_HIT", 110.0)
        assert new_state == TradeStatus.CLOSED

    def test_invalid_transition_raises(self):
        with pytest.raises(StateTransitionError):
            self.sm.transition("test-1", TradeStatus.OPEN, "FILL_CONFIRMED")

    def test_can_transition_check(self):
        assert self.sm.can_transition(TradeStatus.PENDING, "FILL_CONFIRMED") is True
        assert self.sm.can_transition(TradeStatus.PENDING, "TP_HIT") is False
