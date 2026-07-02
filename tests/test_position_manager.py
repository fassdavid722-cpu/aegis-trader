"""Tests for position manager."""
import pytest

from positions import PositionManager, DuplicatePositionError
from signals.models import Signal, TradeSide, TradeStatus, ExitReason


class TestPositionManager:
    """Test virtual position lifecycle."""

    def setup_method(self):
        self.pm = PositionManager()
        self.signal = Signal(
            source="telegram",
            raw_text="LONG BTCUSDT @ 105000",
            symbol="BTCUSDT",
            side=TradeSide.LONG,
            entry=105000,
            stop_loss=103500,
            take_profit=108000,
        )

    def test_create_position(self):
        pos = self.pm.create_position(self.signal)
        assert pos is not None
        assert pos.symbol == "BTCUSDT"
        assert pos.status == TradeStatus.PENDING
        assert pos.direction == TradeSide.LONG

    def test_duplicate_position_raises(self):
        self.pm.create_position(self.signal)

        with pytest.raises(DuplicatePositionError):
            self.pm.create_position(self.signal)

    def test_activate_position(self):
        pos = self.pm.create_position(self.signal)
        activated = self.pm.activate_position(pos.trade_id, 105100.0)

        assert activated.status == TradeStatus.OPEN
        assert activated.entry_price == 105100.0
        assert activated.opened_at is not None

    def test_close_position_tp_hit(self):
        pos = self.pm.create_position(self.signal)
        self.pm.activate_position(pos.trade_id, 105000.0)

        closed = self.pm.close_position(pos.trade_id, 108000.0, ExitReason.TP_HIT)

        assert closed.status == TradeStatus.CLOSED
        assert closed.result.value == "WIN"
        assert closed.pnl_percent is not None

    def test_close_position_sl_hit(self):
        pos = self.pm.create_position(self.signal)
        self.pm.activate_position(pos.trade_id, 105000.0)

        closed = self.pm.close_position(pos.trade_id, 103500.0, ExitReason.SL_HIT)

        assert closed.status == TradeStatus.CLOSED
        assert closed.result.value == "LOSS"
