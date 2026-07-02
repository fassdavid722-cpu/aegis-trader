"""Bridge between Analyst Engine and existing signal pipeline.

Converts TradeCandidates into canonical Signal objects that the existing
PositionManager and MarketMonitor can process.
"""
from __future__ import annotations

from typing import Optional, Any

from signals.models import Signal, SignalSource, TradeSide, MarginMode
from signals.parser import SignalParser
from positions import PositionManager
from .models import TradeCandidate


class AnalystSignalBridge:
    """Bridges analyst candidates into the existing trading pipeline.

    This allows the analyst to feed into the same:
    - PositionManager (virtual positions)
    - MarketMonitor (price tracking)
    - Journal (trade recording)
    - Coach (post-trade analysis)

    As manually received Telegram signals.
    """

    def __init__(self, position_manager: PositionManager) -> None:
        """Initialize bridge.

        Args:
            position_manager: Existing position manager instance
        """
        self.position_manager = position_manager
        self.parser = SignalParser()

    def submit_candidate(self, candidate: TradeCandidate) -> Optional[Signal]:
        """Submit a trade candidate to the existing pipeline.

        Converts TradeCandidate -> Signal -> VirtualPosition.

        Args:
            candidate: Detected trade setup

        Returns:
            The created Signal, or None if rejected
        """
        # Convert to signal dict
        signal_dict = candidate.to_signal()

        # Build canonical Signal object
        signal = Signal(
            source=SignalSource.WEBHOOK,  # Analyst is treated as automated webhook
            raw_text=signal_dict["raw_text"],
            symbol=signal_dict["symbol"],
            side=TradeSide(signal_dict["side"]),
            entry=signal_dict.get("entry"),
            stop_loss=signal_dict.get("stop_loss"),
            take_profit=signal_dict.get("take_profit"),
            leverage=signal_dict.get("leverage", 10),
            margin_mode=MarginMode(signal_dict.get("margin_mode", "ISOLATED")),
            confidence=signal_dict.get("confidence"),
            metadata=signal_dict.get("metadata", {}),
        )

        # Submit to position manager (same path as Telegram signals)
        try:
            position = self.position_manager.create_position(signal)
            if position:
                print(f"Analyst position created: {position.trade_id} | {position.symbol} {position.direction.value} @ {position.entry_price}")
                return signal
        except Exception as e:
            print(f"Analyst signal rejected: {e}")
            return None

        return None

    def submit_candidates(self, candidates: list[TradeCandidate]) -> list[Signal]:
        """Submit multiple candidates.

        Args:
            candidates: List of detected setups

        Returns:
            List of successfully created Signals
        """
        created = []
        for candidate in candidates:
            signal = self.submit_candidate(candidate)
            if signal:
                created.append(signal)
        return created
