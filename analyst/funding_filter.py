"""Funding rate filter and interpretation.

The only external data point. Hard to manipulate.
"""
from __future__ import annotations

from typing import Optional

from .models_v2 import FundingBias


class FundingFilter:
    """Interprets funding rate as a trade filter."""

    # Thresholds (as decimals)
    OVERLEVERAGED_LONG = 0.001    # +0.1%
    OVERLEVERAGED_SHORT = -0.001  # -0.1%
    NEUTRAL_UPPER = 0.0005        # +0.05%
    NEUTRAL_LOWER = -0.0005       # -0.05%
    EXTREME_UPPER = 0.003         # +0.3%
    EXTREME_LOWER = -0.003        # -0.3%

    @classmethod
    def interpret(cls, funding_rate: Optional[float]) -> FundingBias:
        """Interpret funding rate value.

        Args:
            funding_rate: Current funding rate as decimal

        Returns:
            FundingBias classification
        """
        if funding_rate is None:
            return FundingBias.NEUTRAL

        # Extreme squeeze
        if funding_rate >= cls.EXTREME_UPPER or funding_rate <= cls.EXTREME_LOWER:
            return FundingBias.EXTREME_SQUEEZE

        # Overleveraged long
        if funding_rate >= cls.OVERLEVERAGED_LONG:
            return FundingBias.OVERLEVERAGED_LONG

        # Overleveraged short
        if funding_rate <= cls.OVERLEVERAGED_SHORT:
            return FundingBias.OVERLEVERAGED_SHORT

        # Neutral
        return FundingBias.NEUTRAL

    @classmethod
    def aligns_with_trade(cls, funding_bias: FundingBias, trade_direction: str) -> bool:
        """Check if funding bias aligns with trade direction.

        Args:
            funding_bias: Current funding interpretation
            trade_direction: "LONG" or "SHORT"

        Returns:
            True if aligned or neutral, False if against
        """
        if funding_bias == FundingBias.NEUTRAL:
            return True  # Neutral doesn't block

        if funding_bias == FundingBias.EXTREME_SQUEEZE:
            # Extreme funding = counter-trend opportunity
            # If funding is extremely positive (overleveraged longs), favor SHORT
            # If funding is extremely negative (overleveraged shorts), favor LONG
            return True  # Extreme is always a signal, but direction matters

        if trade_direction == "LONG":
            # Favor longs when shorts are overleveraged (negative funding)
            return funding_bias in (FundingBias.OVERLEVERAGED_SHORT,)

        if trade_direction == "SHORT":
            # Favor shorts when longs are overleveraged (positive funding)
            return funding_bias in (FundingBias.OVERLEVERAGED_LONG,)

        return False

    @classmethod
    def get_bias_direction(cls, funding_bias: FundingBias) -> str:
        """Get the directional bias from funding.

        Returns:
            "LONG", "SHORT", or "NEUTRAL"
        """
        if funding_bias in (FundingBias.OVERLEVERAGED_LONG,):
            return "SHORT"  # Longs overleveraged = squeeze shorts
        elif funding_bias in (FundingBias.OVERLEVERAGED_SHORT, FundingBias.EXTREME_SQUEEZE):
            return "LONG"   # Shorts overleveraged = squeeze longs
        else:
            return "NEUTRAL"
