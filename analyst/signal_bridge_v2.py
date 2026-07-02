"""Bridge between Pure Price Action Analyst and existing pipeline.

Converts TradeCandidates into canonical Signals.
Handles TP1/TP2 logic for partial exits.
"""
from __future__ import annotations

from typing import Optional, Any

from signals.models import Signal, SignalSource, TradeSide, MarginMode
from positions import PositionManager
from .models_v2 import TradeCandidate


class AnalystSignalBridgeV2:
    """Bridges PA candidates into existing trading pipeline.

    Handles dual TP system:
    - TP1 at 1.5R (50% position close)
    - TP2 at 3R (remaining 50% close)
    """

    def __init__(self, position_manager: PositionManager) -> None:
        self.position_manager = position_manager

    def submit_candidate(self, candidate: TradeCandidate) -> Optional[Signal]:
        """Submit candidate to pipeline.

        Uses TP2 as primary system TP. TP1 is tracked in metadata
        for later partial exit logic.
        """
        signal_dict = candidate.to_signal()

        signal = Signal(
            source=SignalSource.WEBHOOK,
            raw_text=signal_dict["raw_text"],
            symbol=signal_dict["symbol"],
            side=TradeSide(signal_dict["side"]),
            entry=signal_dict.get("entry"),
            stop_loss=signal_dict.get("stop_loss"),
            take_profit=signal_dict.get("take_profit"),  # TP2
            leverage=signal_dict.get("leverage", 10),
            margin_mode=MarginMode(signal_dict.get("margin_mode", "ISOLATED")),
            confidence=signal_dict.get("confidence"),
            metadata=signal_dict.get("metadata", {}),
        )

        try:
            position = self.position_manager.create_position(signal)
            if position:
                print(f"PA position created: {position.trade_id} | {position.symbol} {position.direction.value}")
                return signal
        except Exception as e:
            print(f"PA signal rejected: {e}")
            return None

        return None

    def submit_candidates(self, candidates: list[TradeCandidate]) -> list[Signal]:
        """Submit multiple candidates."""
        created = []
        for candidate in candidates:
            signal = self.submit_candidate(candidate)
            if signal:
                created.append(signal)
        return created
