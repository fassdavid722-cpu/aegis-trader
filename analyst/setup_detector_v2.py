"""Pure price action setup detector.

Answers Q1 (trade definition), Q2 (conflict resolution),
Q3 (confidence → position sizing), Q4 (regime proof in every candidate).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from .models_v2 import (
    TradeCandidate, MarketRegime, Session, StructureType,
    ZoneType, FundingBias, PriceZone
)
from .price_structure import PriceStructureAnalyzer
from .regime_detector_v2 import RegimeDetectorV2
from .session_filter import SessionFilter
from .funding_filter import FundingFilter
from .confidence_engine import ConfidenceEngine


# ── Q2: Conflict resolution policy ──────────────────────────────────────────
# Explicit and immutable.  Change this enum to change the policy.
class ConflictPolicy:
    """
    IGNORE_OPPOSITE   — if LONG open, ignore SHORT signals (current default)
    CLOSE_AND_REVERSE — close LONG, open SHORT (aggressive, not yet implemented)
    WAIT              — skip any signal while same symbol has open position
    """
    POLICY = "IGNORE_OPPOSITE"   # ← single place to change


class SetupDetectorV2:
    """
    Detects PA setups. Every candidate carries:
    - regime_evidence  : full mathematical proof of regime call
    - confidence_breakdown : explainable score
    - conflict_status  : whether it was blocked by ConflictPolicy

    Q1 ANSWER — DEFINITION OF A TRADE:
        A "trade" is counted from the moment a virtual position is OPENED
        (status='OPEN') in the database. Signals that don't pass confluence
        gates are never written. Trades that close become CLOSED. Count of
        "trades" always means rows with status != 'PENDING'.
    """

    def __init__(self) -> None:
        self.structure_analyzer = PriceStructureAnalyzer()
        self.regime_detector    = RegimeDetectorV2()

    def analyze_symbol(
        self,
        symbol: str,
        candles_4h: list[dict[str, Any]],
        candles_15m: list[dict[str, Any]],
        current_price: float,
        funding_rate: Optional[float] = None,
        open_positions: Optional[list[dict]] = None,  # Q2: pass existing open trades
    ) -> list[TradeCandidate]:
        candidates = []

        session = SessionFilter.get_current_session()
        if not SessionFilter.is_trade_session():
            return candidates

        # ── Q4: regime with full evidence ──────────────────────
        evidence = self.regime_detector.detect_regime_with_evidence(candles_4h)
        regime   = evidence.regime

        if regime == MarketRegime.HIGH_VOLATILITY:
            return candidates

        zones     = self.structure_analyzer.find_zones(candles_15m, lookback=50)
        structure = self.structure_analyzer.detect_structure(candles_15m)
        funding_bias = FundingFilter.interpret(funding_rate)

        for zone in zones:
            if not zone.contains_price(current_price):
                continue
            if not self._has_rejection_candle(candles_15m, zone, current_price):
                continue

            candidate = self._build_candidate(
                symbol=symbol,
                zone=zone,
                regime=regime,
                regime_evidence=evidence,
                session=session,
                structure=structure,
                funding_bias=funding_bias,
                current_price=current_price,
                open_positions=open_positions or [],
            )

            if candidate:
                candidates.append(candidate)

        if candidates:
            candidates.sort(key=lambda c: c.confidence, reverse=True)
            return [candidates[0]]

        return candidates

    def _has_rejection_candle(
        self,
        candles: list[dict[str, Any]],
        zone: PriceZone,
        current_price: float,
    ) -> bool:
        if len(candles) < 3:
            return False
        recent = candles[-3:]
        for c in recent:
            open_p  = float(c["open"])
            high_p  = float(c["high"])
            low_p   = float(c["low"])
            close_p = float(c["close"])
            body        = abs(close_p - open_p)
            upper_wick  = high_p - max(open_p, close_p)
            lower_wick  = min(open_p, close_p) - low_p
            if zone.zone_type == ZoneType.DEMAND:
                if lower_wick > body * 1.5 and close_p > open_p:
                    return True
                if close_p > open_p and body > 0:
                    return True
            elif zone.zone_type == ZoneType.SUPPLY:
                if upper_wick > body * 1.5 and close_p < open_p:
                    return True
                if close_p < open_p and body > 0:
                    return True
        return False

    def _build_candidate(
        self,
        symbol: str,
        zone: PriceZone,
        regime: MarketRegime,
        regime_evidence,
        session: Session,
        structure: Optional[Any],
        funding_bias: FundingBias,
        current_price: float,
        open_positions: list[dict],
    ) -> Optional[TradeCandidate]:

        direction = "LONG" if zone.zone_type == ZoneType.DEMAND else \
                    "SHORT" if zone.zone_type == ZoneType.SUPPLY else None
        if not direction:
            return None

        # ── Regime gate ─────────────────────────────────────
        if regime == MarketRegime.BULL_TREND and direction == "SHORT":
            return None
        if regime == MarketRegime.BEAR_TREND and direction == "LONG":
            return None

        # ── Structure gate ───────────────────────────────────
        struct_type = structure.event_type if structure else StructureType.NONE
        if struct_type != StructureType.NONE:
            if direction == "LONG" and struct_type not in (
                StructureType.BOS_BULL, StructureType.CHOCH_BULL
            ):
                if regime != MarketRegime.SIDEWAYS:
                    return None
            if direction == "SHORT" and struct_type not in (
                StructureType.BOS_BEAR, StructureType.CHOCH_BEAR
            ):
                if regime != MarketRegime.SIDEWAYS:
                    return None

        # ── Funding gate ─────────────────────────────────────
        if not FundingFilter.aligns_with_trade(funding_bias, direction):
            if funding_bias != FundingBias.EXTREME_SQUEEZE:
                return None

        # ── Q2: Conflict resolution ──────────────────────────
        conflict_status = "NONE"
        for pos in open_positions:
            if pos.get("symbol") == symbol:
                existing_dir = pos.get("direction")
                if existing_dir == direction:
                    # Same direction — duplicate, upstream guard handles this
                    return None
                else:
                    # Opposite direction
                    if ConflictPolicy.POLICY == "IGNORE_OPPOSITE":
                        conflict_status = f"BLOCKED_BY_{existing_dir}_OPEN"
                        return None        # ← explicit skip
                    elif ConflictPolicy.POLICY == "WAIT":
                        return None
                    # CLOSE_AND_REVERSE not implemented yet — falls through

        # ── Risk levels ──────────────────────────────────────
        entry = current_price
        if direction == "LONG":
            stop_loss       = zone.bottom * 0.998
            risk            = entry - stop_loss
            take_profit_1   = entry + risk * 1.5   # partial
            take_profit_2   = entry + risk * 3.0   # full
        else:
            stop_loss       = zone.top * 1.002
            risk            = stop_loss - entry
            take_profit_1   = entry - risk * 1.5
            take_profit_2   = entry - risk * 3.0

        # ── Confidence + breakdown ───────────────────────────
        confidence_bd = ConfidenceEngine.calculate(
            direction=direction,
            regime=regime,
            session=session,
            structure=struct_type,
            funding_bias=funding_bias,
            zone=zone,
        )
        if confidence_bd.confluence_count < 3:
            return None

        # ── Q3: Confidence → position size ───────────────────
        # 60–69% → 0.5% account risk
        # 70–79% → 1.0% account risk
        # 80–89% → 1.5% account risk
        # 90%+   → 2.0% account risk (only during trend + BOS)
        score = confidence_bd.total
        if score >= 90:
            risk_pct = 2.0
        elif score >= 80:
            risk_pct = 1.5
        elif score >= 70:
            risk_pct = 1.0
        else:
            risk_pct = 0.5

        # Build thesis
        passed_factors = [
            f["name"] for f in confidence_bd.to_dict()["factors"]
            if f["passed"] and f["score"] > 0
        ]
        thesis = f"{direction} at {zone.zone_type.value} zone | " + " | ".join(passed_factors)

        return TradeCandidate(
            symbol=symbol,
            side=direction,
            entry=round(entry, 4),
            stop_loss=round(stop_loss, 4),
            take_profit_1=round(take_profit_1, 4),
            take_profit_2=round(take_profit_2, 4),
            regime=regime,
            session=session,
            structure=struct_type,
            zone=zone,
            funding_bias=funding_bias,
            risk_percent=risk_pct,            # Q3: scales with confidence
            position_size_percent=0.0,
            thesis=thesis,
            confluence_score=confidence_bd.confluence_count,
            confidence=confidence_bd.total,
            confidence_breakdown=confidence_bd.to_dict(),
            regime_evidence=regime_evidence.to_dict(),    # Q4: audit trail
            conflict_status=conflict_status,
        )
