"""Explainable Confidence Engine.

Every score has a breakdown — not a black box.
Each factor contributes a named, weighted score.
The Coach can later learn which factors actually predict winners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models_v2 import MarketRegime, Session, StructureType, FundingBias, ZoneType, PriceZone


@dataclass
class ConfidenceFactor:
    """A single explainable factor in the confidence score."""
    name: str
    score: float          # Points added (can be negative)
    reason: str           # Human-readable explanation
    passed: bool = True   # Did this factor contribute positively?


@dataclass
class ConfidenceBreakdown:
    """Full explainable confidence breakdown for a trade candidate."""
    factors: list[ConfidenceFactor] = field(default_factory=list)

    @property
    def total(self) -> float:
        base = 50.0
        total = base + sum(f.score for f in self.factors)
        return round(min(max(total, 0), 95), 1)

    @property
    def confluence_count(self) -> int:
        return sum(1 for f in self.factors if f.passed and f.score > 0)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "base": 50,
            "factors": [
                {"name": f.name, "score": f.score, "reason": f.reason, "passed": f.passed}
                for f in self.factors
            ],
            "confluence": self.confluence_count,
        }

    def format_telegram(self) -> str:
        """Format as Telegram-ready breakdown string."""
        lines = [f"Confidence: {self.total:.0f}%\n"]
        for f in self.factors:
            icon = "✅" if f.score > 0 else ("⚠️" if f.score < 0 else "➖")
            sign = "+" if f.score >= 0 else ""
            lines.append(f"{icon} {f.name}: {sign}{f.score:.0f} — {f.reason}")
        lines.append(f"\nConfluence: {self.confluence_count}/5 factors")
        return "\n".join(lines)


class ConfidenceEngine:
    """Calculates explainable confidence scores for trade candidates."""

    # Factor weights
    WEIGHTS = {
        "demand_zone":     {"base": 20, "fresh_bonus": 5},
        "supply_zone":     {"base": 20, "fresh_bonus": 5},
        "session_london":  15,
        "session_ny":      15,
        "session_futures": 5,
        "session_off":     -10,
        "trend_alignment": 15,  # Regime matches direction
        "sideways_regime": 5,   # Sideways = range trade (lower quality)
        "unknown_regime":  -5,
        "structure_bos":   15,  # Break of Structure confirmed
        "structure_choch": 10,  # Change of Character (early signal)
        "no_structure":    0,
        "funding_aligned": 10,  # Funding favours our direction
        "funding_extreme": 15,  # Extreme funding = squeeze likely
        "funding_against": -15, # Funding fights our direction
        "zone_touches":    5,   # Zone has been tested and held before
        "high_volatility": -20, # High vol = noise, skip
    }

    @classmethod
    def calculate(
        cls,
        direction: str,
        regime: MarketRegime,
        session: Session,
        structure: StructureType,
        funding_bias: FundingBias,
        zone: Optional[PriceZone],
    ) -> ConfidenceBreakdown:
        bd = ConfidenceBreakdown()

        # ─── ZONE ──────────────────────────────────────────
        if zone:
            if zone.zone_type == ZoneType.DEMAND and direction == "LONG":
                pts = cls.WEIGHTS["demand_zone"]["base"]
                if zone.is_fresh:
                    pts += cls.WEIGHTS["demand_zone"]["fresh_bonus"]
                    reason = "Fresh demand zone — untested, price pumped strongly from here"
                else:
                    reason = "Demand zone — tested before, showing some respect"
                bd.factors.append(ConfidenceFactor("Demand Zone", pts, reason))

            elif zone.zone_type == ZoneType.SUPPLY and direction == "SHORT":
                pts = cls.WEIGHTS["supply_zone"]["base"]
                if zone.is_fresh:
                    pts += cls.WEIGHTS["supply_zone"]["fresh_bonus"]
                    reason = "Fresh supply zone — untested, price dropped sharply from here"
                else:
                    reason = "Supply zone — tested before, showing some respect"
                bd.factors.append(ConfidenceFactor("Supply Zone", pts, reason))
        else:
            bd.factors.append(ConfidenceFactor("Zone", 0, "No zone identified", passed=False))

        # ─── SESSION ───────────────────────────────────────
        if session == Session.LONDON:
            bd.factors.append(ConfidenceFactor(
                "London Session", cls.WEIGHTS["session_london"],
                "London open — highest institutional liquidity, best for entries"
            ))
        elif session == Session.NY:
            bd.factors.append(ConfidenceFactor(
                "NY Session", cls.WEIGHTS["session_ny"],
                "New York open — high volume, overlaps with London until 09:00 UTC"
            ))
        elif session == Session.FUTURES:
            bd.factors.append(ConfidenceFactor(
                "Futures Session", cls.WEIGHTS["session_futures"],
                "Futures open — moderate volume, valid but less ideal"
            ))
        else:
            bd.factors.append(ConfidenceFactor(
                "Off-Hours", cls.WEIGHTS["session_off"],
                "Low volume session — higher risk of manipulation and noise", passed=False
            ))

        # ─── REGIME ────────────────────────────────────────
        if regime == MarketRegime.HIGH_VOLATILITY:
            bd.factors.append(ConfidenceFactor(
                "High Volatility", cls.WEIGHTS["high_volatility"],
                "Candle range 3x average — erratic price, no clean structure", passed=False
            ))
        elif regime == MarketRegime.BULL_TREND and direction == "LONG":
            bd.factors.append(ConfidenceFactor(
                "Trend Alignment", cls.WEIGHTS["trend_alignment"],
                "Bull trend (HH+HL) confirmed on 4H — trading with momentum"
            ))
        elif regime == MarketRegime.BEAR_TREND and direction == "SHORT":
            bd.factors.append(ConfidenceFactor(
                "Trend Alignment", cls.WEIGHTS["trend_alignment"],
                "Bear trend (LH+LL) confirmed on 4H — trading with momentum"
            ))
        elif regime == MarketRegime.SIDEWAYS:
            bd.factors.append(ConfidenceFactor(
                "Range Regime", cls.WEIGHTS["sideways_regime"],
                "Sideways market — zone trades valid but lower probability than trend"
            ))
        else:
            bd.factors.append(ConfidenceFactor(
                "Unknown Regime", cls.WEIGHTS["unknown_regime"],
                "Regime unclear — no clear HH/HL or LH/LL pattern on 4H", passed=False
            ))

        # ─── STRUCTURE ─────────────────────────────────────
        if structure in (StructureType.BOS_BULL, StructureType.BOS_BEAR):
            bd.factors.append(ConfidenceFactor(
                "Break of Structure", cls.WEIGHTS["structure_bos"],
                f"{structure.value} — price broke a key level, confirming directional intent"
            ))
        elif structure in (StructureType.CHOCH_BULL, StructureType.CHOCH_BEAR):
            bd.factors.append(ConfidenceFactor(
                "Change of Character", cls.WEIGHTS["structure_choch"],
                f"{structure.value} — early reversal signal, less confirmed than BOS"
            ))
        else:
            bd.factors.append(ConfidenceFactor(
                "No Structure Event", cls.WEIGHTS["no_structure"],
                "No BOS or CHOCH detected on 15min — entry is zone-only", passed=False
            ))

        # ─── FUNDING ───────────────────────────────────────
        if funding_bias == FundingBias.EXTREME_SQUEEZE:
            bd.factors.append(ConfidenceFactor(
                "Extreme Funding Squeeze", cls.WEIGHTS["funding_extreme"],
                "Extreme funding — forced liquidations likely to push price in our direction"
            ))
        elif funding_bias == FundingBias.OVERLEVERAGED_LONG and direction == "SHORT":
            bd.factors.append(ConfidenceFactor(
                "Funding Alignment", cls.WEIGHTS["funding_aligned"],
                "Market overleveraged long — longs will be squeezed, shorts benefit"
            ))
        elif funding_bias == FundingBias.OVERLEVERAGED_SHORT and direction == "LONG":
            bd.factors.append(ConfidenceFactor(
                "Funding Alignment", cls.WEIGHTS["funding_aligned"],
                "Market overleveraged short — shorts will be squeezed, longs benefit"
            ))
        elif funding_bias == FundingBias.NEUTRAL:
            bd.factors.append(ConfidenceFactor(
                "Funding Neutral", 0,
                "Funding rate neutral — no leverage squeeze in either direction"
            ))
        elif (funding_bias == FundingBias.OVERLEVERAGED_LONG and direction == "LONG") or \
             (funding_bias == FundingBias.OVERLEVERAGED_SHORT and direction == "SHORT"):
            bd.factors.append(ConfidenceFactor(
                "Funding Against Trade", cls.WEIGHTS["funding_against"],
                "Funding rate fights our direction — increased reversal risk", passed=False
            ))

        return bd
