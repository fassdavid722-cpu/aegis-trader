"""Analyst package - Pure Price Action Engine.

No indicators. No lag. Just price structure, funding, and session timing.

Modules:
    models_v2: Price action data models
    price_structure: Swing points, BOS/CHOCH, Supply/Demand zones
    regime_detector_v2: 4H regime classification (HH/HL, LH/LL, range, vol)
    session_filter: London/NY/Asia/Futures session detection
    funding_filter: Funding rate interpretation and alignment
    setup_detector_v2: Full confluence logic (structure + zone + funding + session)
    market_scanner_v2: Dual timeframe scanning (4H + 15min)
    signal_bridge_v2: Converts candidates to canonical Signals
"""
from .models_v2 import (
    TradeCandidate, MarketRegime, Session, StructureType,
    ZoneType, FundingBias, PriceZone, SwingPoint, StructureEvent,
    ScanResult, MarketScan,
)
from .price_structure import PriceStructureAnalyzer
from .regime_detector_v2 import detect_regime, MarketRegime
from .session_filter import SessionFilter
from .funding_filter import FundingFilter
from .setup_detector_v2 import SetupDetectorV2
from .market_scanner_v2 import MarketScannerV2
from .signal_bridge_v2 import AnalystSignalBridgeV2

__all__ = [
    "TradeCandidate", "MarketRegime", "Session", "StructureType",
    "ZoneType", "FundingBias", "PriceZone", "SwingPoint", "StructureEvent",
    "ScanResult", "MarketScan",
    "PriceStructureAnalyzer",
    "RegimeDetectorV2",
    "SessionFilter",
    "FundingFilter",
    "SetupDetectorV2",
    "MarketScannerV2",
    "AnalystSignalBridgeV2",
]
