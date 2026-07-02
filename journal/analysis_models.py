"""Analysis output models for the coach layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from pydantic import BaseModel, Field


class TradeAnalysis(BaseModel):
    """Structured post-trade analysis output."""
    trade_id: str
    summary: str
    trade_quality: str = Field(..., pattern=r"^(valid|invalid|mixed)$")
    regime_quality: str = Field(..., pattern=r"^(favorable|unfavorable|mixed)$")
    execution_quality: str = Field(..., pattern=r"^(good|bad|unknown)$")
    lessons: list[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
