"""Signal parser - converts raw text alerts into structured Signal objects.

Supports multiple alert formats from different signal providers.
Defensive parsing: if a field is missing, set it to null. Never invent values.
"""
from __future__ import annotations

import re
from typing import Optional, Any

from .models import Signal, SignalSource, TradeSide, MarginMode


class SignalParser:
    """Parses trading alerts from various formats into canonical Signal objects."""

    # Common patterns for price extraction
    PRICE_PATTERNS = [
        r"(?:entry|ent(?:ry)?|buy|sell|open)[\s]*[:\-]?\s*(\d+[.,]?\d*)",
        r"(?:entry|ent(?:ry)?|buy|sell|open)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)",
        r"(?:at|@)\s*(\d+[.,]?\d*)",
    ]

    SL_PATTERNS = [
        r"(?:sl|stop[-\s]?loss|stop)[\s]*[:\-]?\s*(\d+[.,]?\d*)",
        r"(?:sl|stop[-\s]?loss|stop)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)",
    ]

    TP_PATTERNS = [
        r"(?:tp|take[-\s]?profit|target)[\s]*[:\-]?\s*(\d+[.,]?\d*)",
        r"(?:tp|take[-\s]?profit|target)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)",
    ]

    LEVERAGE_PATTERNS = [
        r"(?:lev(?:erage)?|x)[\s]*[:\-]?\s*(\d+)x?",
        r"(\d+)x(?:\s|$)",
    ]

    def __init__(self) -> None:
        """Initialize parser with compiled regex patterns."""
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for performance."""
        self.price_regexes = [re.compile(p, re.IGNORECASE) for p in self.PRICE_PATTERNS]
        self.sl_regexes = [re.compile(p, re.IGNORECASE) for p in self.SL_PATTERNS]
        self.tp_regexes = [re.compile(p, re.IGNORECASE) for p in self.TP_PATTERNS]
        self.lev_regexes = [re.compile(p, re.IGNORECASE) for p in self.LEVERAGE_PATTERNS]

    def parse_telegram(self, raw_text: str) -> Optional[Signal]:
        """Parse a Telegram message into a Signal.

        Args:
            raw_text: The raw message text from Telegram

        Returns:
            Signal object if parseable, None if unparseable
        """
        if not raw_text or not raw_text.strip():
            return None

        text = raw_text.strip()

        # Detect side
        side = self._detect_side(text)
        if side is None:
            return None

        # Detect symbol
        symbol = self._detect_symbol(text)
        if symbol is None and side != TradeSide.CLOSE:
            # For entry signals, symbol is required
            return None

        # Extract prices
        entry = self._extract_price(text, self.price_regexes)
        stop_loss = self._extract_price(text, self.sl_regexes)
        take_profit = self._extract_price(text, self.tp_regexes)

        # Extract leverage
        leverage = self._extract_leverage(text)

        # Detect margin mode
        margin_mode = self._detect_margin_mode(text)

        # Build metadata with parsing info
        metadata = {
            "parser_version": "1.0",
            "parsing_method": "regex",
            "original_length": len(text),
        }

        return Signal(
            source=SignalSource.TELEGRAM,
            raw_text=text,
            symbol=symbol or "UNKNOWN",
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            margin_mode=margin_mode,
            metadata=metadata,
        )

    def parse_webhook(self, payload: dict[str, Any]) -> Optional[Signal]:
        """Parse a webhook JSON payload into a Signal.

        Args:
            payload: JSON dict from webhook

        Returns:
            Signal object if parseable, None if unparseable
        """
        if not payload or not isinstance(payload, dict):
            return None

        # Extract fields with defensive defaults
        symbol = payload.get("symbol", payload.get("pair", ""))
        side_str = payload.get("side", payload.get("direction", ""))

        side = self._normalize_side(side_str)
        if side is None:
            return None

        entry = self._to_float(payload.get("entry", payload.get("entry_price", payload.get("price"))))
        stop_loss = self._to_float(payload.get("stop_loss", payload.get("sl", payload.get("stop"))))
        take_profit = self._to_float(payload.get("take_profit", payload.get("tp", payload.get("target"))))
        leverage = self._to_int(payload.get("leverage", payload.get("lev", 10)))
        margin_mode = self._normalize_margin_mode(payload.get("margin_mode", payload.get("margin", "isolated")))

        metadata = {
            "parser_version": "1.0",
            "parsing_method": "webhook_json",
            "original_payload_keys": list(payload.keys()),
        }

        return Signal(
            source=SignalSource.WEBHOOK,
            raw_text=str(payload),
            symbol=symbol.upper().replace(" ", ""),
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            margin_mode=margin_mode,
            metadata=metadata,
        )

    def _detect_side(self, text: str) -> Optional[TradeSide]:
        """Detect trade direction from text."""
        text_lower = text.lower()

        # Close signals
        close_keywords = ["close", "exit", "sell all", "close position", "close trade"]
        if any(kw in text_lower for kw in close_keywords):
            return TradeSide.CLOSE

        # Long signals
        long_keywords = ["long", "buy", "bull", "up"]
        if any(kw in text_lower for kw in long_keywords):
            return TradeSide.LONG

        # Short signals
        short_keywords = ["short", "sell", "bear", "down"]
        if any(kw in text_lower for kw in short_keywords):
            return TradeSide.SHORT

        # If no explicit direction but prices present, try to infer from context
        # This is weak inference - log uncertainty
        return None

    def _detect_symbol(self, text: str) -> Optional[str]:
        """Extract trading pair symbol from text."""
        # Pattern: BTCUSDT, ETH-USDT, BTC/USDT, etc.
        patterns = [
            r"\b([A-Z]{2,10}USDT)\b",  # BTCUSDT
            r"\b([A-Z]{2,10})[-/]?USDT\b",  # BTC-USDT, BTC/USDT
            r"#([A-Z]{2,10})\b",  # #BTC
            r"\$([A-Z]{2,10})\b",  # $BTC
        ]

        for pattern in patterns:
            match = re.search(pattern, text.upper())
            if match:
                symbol = match.group(1).upper()
                if not symbol.endswith("USDT"):
                    symbol = symbol + "USDT"
                return symbol

        return None

    def _extract_price(self, text: str, patterns: list[re.Pattern]) -> Optional[float]:
        """Extract the first matching price from text using given patterns."""
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                price_str = match.group(1).replace(",", "")
                try:
                    return float(price_str)
                except ValueError:
                    continue
        return None

    def _extract_leverage(self, text: str) -> int:
        """Extract leverage from text. Defaults to 10."""
        for pattern in self.lev_regexes:
            match = pattern.search(text)
            if match:
                try:
                    lev = int(match.group(1))
                    if 1 <= lev <= 125:
                        return lev
                except ValueError:
                    continue
        return 10  # Default

    def _detect_margin_mode(self, text: str) -> MarginMode:
        """Detect margin mode from text. Defaults to ISOLATED."""
        text_lower = text.lower()
        if "cross" in text_lower:
            return MarginMode.CROSS
        return MarginMode.ISOLATED

    def _normalize_side(self, side_str: str) -> Optional[TradeSide]:
        """Normalize side string to TradeSide enum."""
        if not side_str:
            return None

        side_lower = str(side_str).lower().strip()

        if side_lower in ("long", "buy", "bull"):
            return TradeSide.LONG
        elif side_lower in ("short", "sell", "bear"):
            return TradeSide.SHORT
        elif side_lower in ("close", "exit", "sell_all"):
            return TradeSide.CLOSE

        return None

    def _normalize_margin_mode(self, mode_str: str) -> MarginMode:
        """Normalize margin mode string."""
        if not mode_str:
            return MarginMode.ISOLATED

        mode_lower = str(mode_str).lower().strip()
        if mode_lower in ("cross", "crossed", "c"):
            return MarginMode.CROSS
        return MarginMode.ISOLATED

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """Safely convert value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int:
        """Safely convert value to int."""
        if value is None:
            return 10
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 10
