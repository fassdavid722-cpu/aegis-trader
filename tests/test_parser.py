"""Tests for signal parser."""
import pytest

from signals.parser import SignalParser
from signals.models import TradeSide, MarginMode


class TestSignalParser:
    """Test signal parsing from various formats."""

    def setup_method(self):
        self.parser = SignalParser()

    def test_parse_long_signal(self):
        text = "LONG BTCUSDT\nEntry: 105000\nSL: 103500\nTP: 108000\nLeverage: 10x"
        signal = self.parser.parse_telegram(text)

        assert signal is not None
        assert signal.side == TradeSide.LONG
        assert signal.symbol == "BTCUSDT"
        assert signal.entry == 105000.0
        assert signal.stop_loss == 103500.0
        assert signal.take_profit == 108000.0
        assert signal.leverage == 10
        assert signal.margin_mode == MarginMode.ISOLATED

    def test_parse_short_signal(self):
        text = "SHORT ETHUSDT @ 4000\nSL: 4100\nTP: 3800\nCross margin"
        signal = self.parser.parse_telegram(text)

        assert signal is not None
        assert signal.side == TradeSide.SHORT
        assert signal.symbol == "ETHUSDT"
        assert signal.entry == 4000.0
        assert signal.margin_mode == MarginMode.CROSS

    def test_parse_close_signal(self):
        text = "CLOSE BTCUSDT"
        signal = self.parser.parse_telegram(text)

        assert signal is not None
        assert signal.side == TradeSide.CLOSE
        assert signal.symbol == "BTCUSDT"

    def test_parse_webhook(self):
        payload = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry": 105000,
            "sl": 103500,
            "tp": 108000,
            "leverage": 10,
        }
        signal = self.parser.parse_webhook(payload)

        assert signal is not None
        assert signal.side == TradeSide.LONG
        assert signal.entry == 105000.0

    def test_parse_invalid_returns_none(self):
        text = "Hello, this is not a signal"
        signal = self.parser.parse_telegram(text)
        assert signal is None

    def test_parse_missing_fields_defaults(self):
        text = "LONG BTCUSDT"
        signal = self.parser.parse_telegram(text)

        assert signal is not None
        assert signal.leverage == 10  # Default
        assert signal.margin_mode == MarginMode.ISOLATED  # Default
        assert signal.entry is None
        assert signal.stop_loss is None
