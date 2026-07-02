"""Pure Price Action Analyst Models.

No indicators. No lag. Just price structure, funding, and session timing.
Built for manipulation resistance across all market conditions.
"""
from __future__ import annotations

from datetime import datetime, timezone, time
from typing import Optional, Any
from enum import Enum

from pydantic import BaseModel, Field


class MarketRegime(str, Enum):
    """4H regime classification."""
    BULL_TREND = "BULL_TREND"           # HH + HL
    BEAR_TREND = "BEAR_TREND"           # LH + LL
    SIDEWAYS = "SIDEWAYS"               # Range between clear high/low
    HIGH_VOLATILITY = "HIGH_VOLATILITY" # Candle size 3x avg
    UNKNOWN = "UNKNOWN"


class Session(str, Enum):
    """Trading sessions (UTC)."""
    ASIA = "ASIA"           # 00:00 - 03:00 UTC
    LONDON = "LONDON"       # 07:00 - 09:00 UTC
    NY = "NY"               # 13:00 - 15:00 UTC
    FUTURES = "FUTURES"     # 20:00 - 21:00 UTC
    OFF_HOURS = "OFF_HOURS" # Everything else


class StructureType(str, Enum):
    """Price structure events."""
    BOS_BULL = "BOS_BULL"       # Break of Structure bullish
    BOS_BEAR = "BOS_BEAR"       # Break of Structure bearish
    CHOCH_BULL = "CHOCH_BULL"   # Change of Character to bullish
    CHOCH_BEAR = "CHOCH_BEAR"   # Change of Character to bearish
    NONE = "NONE"


class ZoneType(str, Enum):
    """Supply/Demand zone types."""
    DEMAND = "DEMAND"   # Price pumped sharply from here
    SUPPLY = "SUPPLY"   # Price dropped sharply from here
    NONE = "NONE"


class FundingBias(str, Enum):
    """Funding rate interpretation."""
    OVERLEVERAGED_LONG = "OVERLEVERAGED_LONG"    # > +0.1%
    OVERLEVERAGED_SHORT = "OVERLEVERAGED_SHORT"  # < -0.1%
    NEUTRAL = "NEUTRAL"                          # -0.05% to +0.05%
    EXTREME_SQUEEZE = "EXTREME_SQUEEZE"          # > +0.3% or < -0.3%


class PriceZone(BaseModel):
    """A supply or demand zone."""
    zone_type: ZoneType
    top: float                    # Zone upper boundary
    bottom: float                 # Zone lower boundary
    created_at: datetime          # When zone was formed
    touches: int = 0              # How many times price returned
    is_fresh: bool = True         # Has not been violated yet
    source_candle_high: float     # The candle that created this zone
    source_candle_low: float
    source_candle_close: float

    def contains_price(self, price: float) -> bool:
        """Check if price is within this zone."""
        return self.bottom <= price <= self.top

    def is_respected(self, price: float, direction: str) -> bool:
        """Check if price respected this zone (bounced off it)."""
        if self.zone_type == ZoneType.DEMAND and direction == "LONG":
            return price >= self.bottom  # Bounced up from demand
        elif self.zone_type == ZoneType.SUPPLY and direction == "SHORT":
            return price <= self.top     # Bounced down from supply
        return False


class SwingPoint(BaseModel):
    """A significant swing high or low."""
    is_high: bool
    price: float
    timestamp: datetime
    index: int                    # Position in candle series


class StructureEvent(BaseModel):
    """BOS or CHOCH detection."""
    event_type: StructureType
    trigger_price: float
    reference_price: float        # The swing point that was broken
    timestamp: datetime
    confirmed: bool = False       # Wait for next candle close to confirm


class TradeCandidate(BaseModel):
    """A trade setup from pure price action analysis."""
    candidate_id: str = Field(default_factory=lambda: f"pa-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{id(object())}")
    symbol: str
    side: str                     # LONG or SHORT
    entry: float
    stop_loss: float
    take_profit_1: float          # 1.5R — take 50% off
    take_profit_2: float          # 3R — close remaining

    # Confluence
    regime: MarketRegime
    session: Session
    structure: StructureType
    zone: Optional[PriceZone] = None
    funding_bias: FundingBias

    # Risk
    risk_percent: float = 1.5     # Account risk %
    position_size_percent: float  # Calculated from risk and SL distance

    # Reasoning
    thesis: str = ""
    confluence_score: int = 0     # How many factors aligned (0-5)
    confidence: float = Field(..., ge=0, le=100)
    confidence_breakdown: Optional[dict] = None  # Explainable score breakdown
    regime_evidence: Optional[dict] = None       # Q4: full mathematical regime proof
    conflict_status: str = "NONE"                # Q2: conflict resolution outcome

    # Metadata
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    analyst_version: str = "2.0-pa"

    def to_signal(self) -> dict[str, Any]:
        """Convert to Signal-compatible dict."""
        return {
            "source": "analyst",
            "raw_text": (
                f"ANALYST {self.side} {self.symbol} @ {self.entry}\n"
                f"SL: {self.stop_loss} | TP1: {self.take_profit_1} | TP2: {self.take_profit_2}\n"
                f"Regime: {self.regime.value} | Session: {self.session.value}\n"
                f"Structure: {self.structure.value} | Funding: {self.funding_bias.value}\n"
                f"Thesis: {self.thesis}"
            ),
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit_2,  # Primary TP for system
            "leverage": 10,
            "margin_mode": "ISOLATED",
            "confidence": self.confidence,
            "metadata": {
                "setup_type": "PRICE_ACTION",
                "regime": self.regime.value,
                "session": self.session.value,
                "structure": self.structure.value,
                "funding_bias": self.funding_bias.value,
                "thesis": self.thesis,
                "confluence_score": self.confluence_score,
                "tp1": self.take_profit_1,
                "tp2": self.take_profit_2,
                "risk_percent": self.risk_percent,
            }
        }


class ScanResult(BaseModel):
    """Result of scanning a single symbol."""
    symbol: str
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    regime: MarketRegime
    session: Session
    funding_rate: Optional[float] = None
    funding_bias: FundingBias
    active_zones: list[PriceZone] = Field(default_factory=list)
    recent_structure: Optional[StructureEvent] = None
    candidates: list[TradeCandidate] = Field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_candidates(self) -> bool:
        return len(self.candidates) > 0


class MarketScan(BaseModel):
    """Result of scanning multiple symbols."""
    scan_id: str = Field(default_factory=lambda: f"scan-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    symbols_scanned: int = 0
    symbols_with_candidates: int = 0
    total_candidates: int = 0
    results: list[ScanResult] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    daily_pnl_percent: float = 0.0  # Track for daily loss limit
    trading_halted: bool = False      # True if daily loss limit hit

    def finalize(self) -> None:
        self.completed_at = datetime.now(timezone.utc)
        self.symbols_scanned = len(self.results)
        self.symbols_with_candidates = sum(1 for r in self.results if r.has_candidates)
        self.total_candidates = sum(len(r.candidates) for r in self.results)
