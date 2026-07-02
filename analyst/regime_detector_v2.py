"""Market regime detection using pure price structure.

EXPLICIT MATHEMATICAL RULES — no black boxes.

BULL_TREND:      last 2 swing highs are ascending AND last 2 swing lows are ascending
                 i.e. H[-1] > H[-2] AND L[-1] > L[-2]

BEAR_TREND:      last 2 swing highs are descending AND last 2 swing lows are descending
                 i.e. H[-1] < H[-2] AND L[-1] < L[-2]

SIDEWAYS:        neither trend confirmed AND price contained in 2-15% range
                 AND range touched top (>max_high×0.995) ≥2 times
                 AND range touched bottom (<min_low×1.005) ≥2 times

HIGH_VOLATILITY: current_candle_range > avg_range_20 × 3.0

TRANSITIONAL:    trend signals mixed (HH but not HL, or vice versa)
                 — treated as UNKNOWN, analyst will skip

UNKNOWN:         insufficient data (<20 candles) or no pattern matched
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

from .models_v2 import MarketRegime
from .price_structure import PriceStructureAnalyzer


@dataclass
class RegimeEvidence:
    """Audit trail for regime classification decision."""
    regime: MarketRegime
    swing_highs: list[float]
    swing_lows:  list[float]
    hh: bool         # Higher High confirmed
    hl: bool         # Higher Low confirmed
    lh: bool         # Lower High confirmed
    ll: bool         # Lower Low confirmed
    range_pct: float
    high_vol_ratio: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "regime":        self.regime.value,
            "swing_highs":   [round(h, 2) for h in self.swing_highs],
            "swing_lows":    [round(l, 2) for l in self.swing_lows],
            "HH":            self.hh,
            "HL":            self.hl,
            "LH":            self.lh,
            "LL":            self.ll,
            "range_pct":     round(self.range_pct * 100, 2),
            "high_vol_ratio": round(self.high_vol_ratio, 2),
            "reason":        self.reason,
        }

    def format_proof(self) -> str:
        """Human-readable proof string — shown in Telegram alerts."""
        lines = [f"Regime: {self.regime.value}"]
        if len(self.swing_highs) >= 2 and len(self.swing_lows) >= 2:
            lines.append(
                f"Swings — H: {self.swing_highs[-2]:.0f} → {self.swing_highs[-1]:.0f} "
                f"({'HH ✅' if self.hh else 'LH ⚠️'})  "
                f"L: {self.swing_lows[-2]:.0f} → {self.swing_lows[-1]:.0f} "
                f"({'HL ✅' if self.hl else 'LL ⚠️'})"
            )
        elif self.swing_highs or self.swing_lows:
            lines.append(f"Swings recorded: H={len(self.swing_highs)}, L={len(self.swing_lows)}")
        if self.range_pct > 0:
            lines.append(f"Range: {self.range_pct*100:.1f}%")
        if self.high_vol_ratio > 0:
            lines.append(f"Vol ratio: {self.high_vol_ratio:.1f}x avg (gate: 3.0x)")
        lines.append(f"Proof: {self.reason}")
        return "\n".join(lines)


class RegimeDetectorV2:
    """Detects market regime from 4H price structure.

    Every decision is logged in RegimeEvidence — fully auditable.
    """

    HIGH_VOL_MULTIPLIER = 3.0   # candle range × this → HIGH_VOLATILITY
    SIDEWAYS_MIN_RANGE  = 0.02  # 2%  minimum to be called sideways
    SIDEWAYS_MAX_RANGE  = 0.15  # 15% maximum before it's too wide
    SWING_LOOKBACK      = 5     # candles each side for swing detection

    def __init__(self) -> None:
        self.structure_analyzer = PriceStructureAnalyzer(
            swing_lookback=self.SWING_LOOKBACK
        )

    def detect_regime(self, candles_4h: list[dict[str, Any]]) -> MarketRegime:
        """Classify regime. Returns enum only (for compatibility)."""
        return self.detect_regime_with_evidence(candles_4h).regime

    def detect_regime_with_evidence(
        self, candles_4h: list[dict[str, Any]]
    ) -> RegimeEvidence:
        """Full classification with complete mathematical audit trail."""

        # ── Insufficient data ─────────────────────────────────
        if len(candles_4h) < 20:
            return RegimeEvidence(
                regime=MarketRegime.UNKNOWN,
                swing_highs=[], swing_lows=[],
                hh=False, hl=False, lh=False, ll=False,
                range_pct=0, high_vol_ratio=0,
                reason="Insufficient data (need ≥20 candles)",
            )

        # ── Step 1: High-volatility gate ──────────────────────
        vol_ratio = self._volatility_ratio(candles_4h)
        if vol_ratio >= self.HIGH_VOL_MULTIPLIER:
            return RegimeEvidence(
                regime=MarketRegime.HIGH_VOLATILITY,
                swing_highs=[], swing_lows=[],
                hh=False, hl=False, lh=False, ll=False,
                range_pct=0, high_vol_ratio=vol_ratio,
                reason=(
                    f"Current candle range = {vol_ratio:.1f}× average "
                    f"(threshold: {self.HIGH_VOL_MULTIPLIER}×)"
                ),
            )

        # ── Step 2: Swing structure analysis ─────────────────
        swings = self.structure_analyzer.find_swing_points(candles_4h)
        swing_highs = sorted(
            [s.price for s in swings if s.is_high],
            key=lambda p: next(s.index for s in swings if s.is_high and s.price == p)
        )
        swing_lows = sorted(
            [s.price for s in swings if not s.is_high],
            key=lambda p: next(s.index for s in swings if not s.is_high and s.price == p)
        )

        hh = hl = lh = ll = False

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            hh = swing_highs[-1] > swing_highs[-2]
            hl = swing_lows[-1]  > swing_lows[-2]
            lh = swing_highs[-1] < swing_highs[-2]
            ll = swing_lows[-1]  < swing_lows[-2]

            if hh and hl:
                return RegimeEvidence(
                    regime=MarketRegime.BULL_TREND,
                    swing_highs=swing_highs[-3:], swing_lows=swing_lows[-3:],
                    hh=hh, hl=hl, lh=lh, ll=ll,
                    range_pct=0, high_vol_ratio=vol_ratio,
                    reason=(
                        f"HH confirmed ({swing_highs[-2]:.0f}→{swing_highs[-1]:.0f}) "
                        f"AND HL confirmed ({swing_lows[-2]:.0f}→{swing_lows[-1]:.0f})"
                    ),
                )

            if lh and ll:
                return RegimeEvidence(
                    regime=MarketRegime.BEAR_TREND,
                    swing_highs=swing_highs[-3:], swing_lows=swing_lows[-3:],
                    hh=hh, hl=hl, lh=lh, ll=ll,
                    range_pct=0, high_vol_ratio=vol_ratio,
                    reason=(
                        f"LH confirmed ({swing_highs[-2]:.0f}→{swing_highs[-1]:.0f}) "
                        f"AND LL confirmed ({swing_lows[-2]:.0f}→{swing_lows[-1]:.0f})"
                    ),
                )

        # ── Step 3: Sideways test ─────────────────────────────
        range_pct, high_touches, low_touches, max_h, min_l = \
            self._sideways_metrics(candles_4h)

        if (self.SIDEWAYS_MIN_RANGE <= range_pct <= self.SIDEWAYS_MAX_RANGE
                and high_touches >= 2 and low_touches >= 2):
            return RegimeEvidence(
                regime=MarketRegime.SIDEWAYS,
                swing_highs=swing_highs[-3:] if swing_highs else [],
                swing_lows=swing_lows[-3:]   if swing_lows  else [],
                hh=hh, hl=hl, lh=lh, ll=ll,
                range_pct=range_pct, high_vol_ratio=vol_ratio,
                reason=(
                    f"Range {range_pct*100:.1f}% ({min_l:.0f}–{max_h:.0f}), "
                    f"top touched {high_touches}×, bottom {low_touches}× in last 20 candles"
                ),
            )

        return RegimeEvidence(
            regime=MarketRegime.UNKNOWN,
            swing_highs=swing_highs[-3:] if swing_highs else [],
            swing_lows=swing_lows[-3:]   if swing_lows  else [],
            hh=hh, hl=hl, lh=lh, ll=ll,
            range_pct=range_pct, high_vol_ratio=vol_ratio,
            reason="Mixed signals — neither trend nor range confirmed",
        )

    # ── Internal helpers ─────────────────────────────────────────

    def _volatility_ratio(self, candles: list[dict]) -> float:
        if len(candles) < 21:
            return 0.0
        ranges = [float(c["high"]) - float(c["low"]) for c in candles[-21:-1]]
        avg    = sum(ranges) / len(ranges) if ranges else 0
        if avg == 0:
            return 0.0
        current = float(candles[-1]["high"]) - float(candles[-1]["low"])
        return current / avg

    def _sideways_metrics(
        self, candles: list[dict]
    ) -> tuple[float, int, int, float, float]:
        recent = candles[-20:]
        highs  = [float(c["high"]) for c in recent]
        lows   = [float(c["low"])  for c in recent]
        max_h  = max(highs)
        min_l  = min(lows)
        rng    = (max_h - min_l) / min_l if min_l > 0 else 0
        h_touch = sum(1 for h in highs if h > max_h * 0.995)
        l_touch = sum(1 for l in lows  if l < min_l * 1.005)
        return rng, h_touch, l_touch, max_h, min_l
