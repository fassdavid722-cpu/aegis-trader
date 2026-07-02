"""Webhook listener for external signal sources.

Minimal FastAPI endpoint (optional, Phase 5).
In V1, this can be a simple HTTP handler.
"""
from __future__ import annotations

from typing import Callable, Any

from signals.models import Signal
from .parser import SignalParser


class WebhookListener:
    """Accepts structured JSON alerts from external sources.

    V1: Simple handler class. Can be wrapped in FastAPI later.
    """

    def __init__(self, on_signal: Callable[[Signal], None]) -> None:
        """Initialize webhook listener.

        Args:
            on_signal: Callback for parsed signals
        """
        self.parser = SignalParser()
        self.on_signal = on_signal

    def handle_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process incoming webhook payload.

        Args:
            payload: JSON dict from external source

        Returns:
            Response dict with status and trade_id
        """
        signal = self.parser.parse_webhook(payload)

        if not signal:
            return {
                "status": "error",
                "message": "Could not parse webhook payload",
            }

        self.on_signal(signal)

        return {
            "status": "success",
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "side": signal.side.value,
        }
