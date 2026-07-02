"""Data models for signals and trades.

Pydantic models with strict validation for futures trading.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator, ConfigDict


class SignalSource(str, Enum):
    """Source of the trading signal."""
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"


class TradeSide(str, Enum):
    """Direction of the trade."""
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"


class MarginMode(str, Enum):
    """Futures margin mode."""
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class TradeStatus(str, Enum):
    """Virtual position status."""
    PENDING = "PENDING"    # Signal received, waiting for fill conditions
    OPEN = "OPEN"          # Virtual position active
    CLOSED = "CLOSED"      # Position closed (TP, SL, manual)
    EXPIRED = "EXPIRED"    # Entry time decay expired
    INVALID = "INVALID"    # Signal could not be parsed or processed


class ExitReason(str, Enum):
    """Reason for position closure."""
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    SIGNAL_CLOSE = "SIGNAL_CLOSE"
    LIQUIDATED = "LIQUIDATED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class TradeResult(str, Enum):
    """Outcome classification."""
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    INVALID = "INVALID"


class MarketRegime(str, Enum):
    """Market regime classification."""
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class Signal(BaseModel):
    """Canonical signal model - normalized from any input source."""
    model_config = ConfigDict(frozen=False)

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: SignalSource
    raw_text: str
    symbol: str = Field(..., pattern=r"^[A-Z0-9]+USDT$")  # e.g. BTCUSDT
    contract_type: str = Field(default="PERPETUAL")
    side: TradeSide
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: int = Field(default=10, ge=1, le=125)
    margin_mode: MarginMode = Field(default=MarginMode.ISOLATED)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: Optional[float] = Field(default=None, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase, no spaces."""
        return v.strip().upper().replace(" ", "")

    @field_validator("entry", "stop_loss", "take_profit")
    @classmethod
    def validate_prices(cls, v: Optional[float]) -> Optional[float]:
        """Ensure prices are positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Price must be positive")
        return v

    def is_entry_signal(self) -> bool:
        """Check if this is an entry signal (not a close)."""
        return self.side in (TradeSide.LONG, TradeSide.SHORT)

    def is_close_signal(self) -> bool:
        """Check if this is a close signal."""
        return self.side == TradeSide.CLOSE

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "raw_text": self.raw_text,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "side": self.side.value,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "leverage": self.leverage,
            "margin_mode": self.margin_mode.value,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "metadata": str(self.metadata) if self.metadata else None,
        }


class VirtualPosition(BaseModel):
    """Virtual futures position tracked against live market data."""
    model_config = ConfigDict(frozen=False)

    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    symbol: str
    contract_type: str = Field(default="PERPETUAL")
    direction: TradeSide  # LONG or SHORT only
    leverage: int = Field(..., ge=1, le=125)
    margin_mode: MarginMode
    entry_price: float = Field(..., gt=0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    liquidation_price: Optional[float] = None
    margin_used: Optional[float] = None
    status: TradeStatus = Field(default=TradeStatus.PENDING)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    result: Optional[TradeResult] = None
    pnl_percent: Optional[float] = None
    pnl_absolute: Optional[float] = None
    roi_percent: Optional[float] = None
    trading_fee: Optional[float] = None
    funding_fee: Optional[float] = None
    market_regime: Optional[MarketRegime] = None
    signal_raw: Optional[str] = None
    signal_source: Optional[str] = None
    setup_type: Optional[str] = None
    confidence_score: Optional[float] = None
    notes: Optional[str] = None

    # Runtime tracking (not persisted)
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0

    def calculate_liquidation_price(self) -> Optional[float]:
        """Estimate liquidation price for the position.

        Simplified calculation for USDT-M perpetual futures.
        Does not account for maintenance margin or funding.
        """
        if self.entry_price is None or self.leverage is None:
            return None

        mm_rate = 0.004  # Approximate maintenance margin rate

        if self.direction == TradeSide.LONG:
            liq = self.entry_price * (1 - 1/self.leverage + mm_rate)
        else:  # SHORT
            liq = self.entry_price * (1 + 1/self.leverage - mm_rate)

        self.liquidation_price = round(liq, 2)
        return self.liquidation_price

    def calculate_margin(self, notional_value: float = 100.0) -> float:
        """Calculate margin required for the position.

        Args:
            notional_value: Position size in USDT (default 100 for standardized tracking)
        """
        margin = notional_value / self.leverage
        self.margin_used = round(margin, 4)
        return self.margin_used

    def check_exit(self, current_price: float) -> Optional[ExitReason]:
        """Check if TP or SL has been hit at current price.

        Returns the exit reason if triggered, None otherwise.
        """
        if self.status != TradeStatus.OPEN:
            return None

        if self.direction == TradeSide.LONG:
            if self.take_profit is not None and current_price >= self.take_profit:
                return ExitReason.TP_HIT
            if self.stop_loss is not None and current_price <= self.stop_loss:
                return ExitReason.SL_HIT
        else:  # SHORT
            if self.take_profit is not None and current_price <= self.take_profit:
                return ExitReason.TP_HIT
            if self.stop_loss is not None and current_price >= self.stop_loss:
                return ExitReason.SL_HIT

        # Check liquidation
        if self.liquidation_price is not None:
            if self.direction == TradeSide.LONG and current_price <= self.liquidation_price:
                return ExitReason.LIQUIDATED
            if self.direction == TradeSide.SHORT and current_price >= self.liquidation_price:
                return ExitReason.LIQUIDATED

        return None

    def calculate_pnl(self, exit_price: float, fee_rate: float = 0.0006) -> dict[str, float]:
        """Calculate PnL, ROI, and fees for the position.

        Args:
            exit_price: The price at which position closed
            fee_rate: Trading fee as decimal (default 0.06% taker)

        Returns:
            Dict with pnl_percent, pnl_absolute, roi_percent, trading_fee
        """
        if self.entry_price is None or exit_price is None:
            return {}

        # Notional value (standardized to 100 USDT for comparison)
        notional = 100.0

        # Price change
        if self.direction == TradeSide.LONG:
            price_change = exit_price - self.entry_price
            pnl_percent = (price_change / self.entry_price) * 100
        else:
            price_change = self.entry_price - exit_price
            pnl_percent = (price_change / self.entry_price) * 100

        # Leveraged PnL
        leveraged_pnl_percent = pnl_percent * self.leverage

        # Fees (entry + exit)
        trading_fee = notional * fee_rate * 2

        # Net PnL
        pnl_absolute = (leveraged_pnl_percent / 100) * notional - trading_fee
        roi_percent = (pnl_absolute / notional) * 100

        return {
            "pnl_percent": round(pnl_percent, 4),
            "pnl_absolute": round(pnl_absolute, 4),
            "roi_percent": round(roi_percent, 4),
            "trading_fee": round(trading_fee, 4),
        }

    def close(self, exit_price: float, reason: ExitReason, fee_rate: float = 0.0006) -> None:
        """Close the virtual position and calculate final metrics."""
        self.status = TradeStatus.CLOSED
        self.closed_at = datetime.now(timezone.utc)
        self.exit_price = exit_price
        self.exit_reason = reason

        # Calculate PnL
        pnl_data = self.calculate_pnl(exit_price, fee_rate)
        self.pnl_percent = pnl_data.get("pnl_percent")
        self.pnl_absolute = pnl_data.get("pnl_absolute")
        self.roi_percent = pnl_data.get("roi_percent")
        self.trading_fee = pnl_data.get("trading_fee")

        # Determine result
        if self.pnl_absolute is not None:
            if self.pnl_absolute > 0:
                self.result = TradeResult.WIN
            elif self.pnl_absolute < 0:
                self.result = TradeResult.LOSS
            else:
                self.result = TradeResult.BREAKEVEN

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "direction": self.direction.value,
            "leverage": self.leverage,
            "margin_mode": self.margin_mode.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "liquidation_price": self.liquidation_price,
            "margin_used": self.margin_used,
            "status": self.status.value,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "result": self.result.value if self.result else None,
            "pnl_percent": self.pnl_percent,
            "pnl_absolute": self.pnl_absolute,
            "roi_percent": self.roi_percent,
            "trading_fee": self.trading_fee,
            "funding_fee": self.funding_fee,
            "market_regime": self.market_regime.value if self.market_regime else None,
            "signal_raw": self.signal_raw,
            "signal_source": self.signal_source,
            "setup_type": self.setup_type,
            "confidence_score": self.confidence_score,
            "notes": self.notes,
        }


class MarketSnapshot(BaseModel):
    """Market data snapshot at a point in time."""
    symbol: str
    price: float
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
