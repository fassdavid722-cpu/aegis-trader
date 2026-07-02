"""Tests for journal writer and reader."""
import pytest

from database import init_database, get_db_connection
from journal.models import JournalWriter, JournalReader


class TestJournal:
    """Test journal operations."""

    def setup_method(self):
        init_database()
        self.writer = JournalWriter()
        self.reader = JournalReader()

    def test_log_system_event(self):
        self.writer.log_system_event("INFO", "TEST", "Test message")

        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM system_log WHERE component = 'TEST'"
        ).fetchone()

        assert row is not None
        assert row["message"] == "Test message"
        assert row["level"] == "INFO"

    def test_record_market_context(self):
        self.writer.record_market_context(
            trade_id="test-trade-1",
            price_at_entry=105000.0,
            volatility=0.02,
            regime_tag="TRENDING_UP",
        )

        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM market_context WHERE trade_id = 'test-trade-1'"
        ).fetchone()

        assert row is not None
        assert row["price_at_entry"] == 105000.0
        assert row["regime_tag"] == "TRENDING_UP"
