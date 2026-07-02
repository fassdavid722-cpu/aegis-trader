"""Coach package for Aegis Trader."""
from .coach_engine import CoachEngine
from .trade_analyzer import TradeAnalyzer
from .regime_detector import RegimeDetector

__all__ = ["CoachEngine", "TradeAnalyzer", "RegimeDetector"]
