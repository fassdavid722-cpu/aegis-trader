"""Market Context Module — The macro view a scalper needs.

A scalper doesn't trade in isolation. They need to know:
- What is BTC doing? (all alts follow BTC)
- Market breadth: are most alts up or down?
- Funding rate extremes across the market
- Order book pressure from live data
- Open interest changes (squeeze potential)
- Long/short positioning (crowded trades)

This module aggregates all cross-symbol data into a single context.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from exchange.bitget_client import (
    BitgetMarketClient,
    BitgetTicker,
    OrderBookSnapshot,
    OpenInterestData,
    LongShortRatio,
)


@dataclass
class SymbolContext:
    """Per-symbol enriched data beyond candles."""
    orderbook: Optional[OrderBookSnapshot] = None
    open_interest: Optional[OpenInterestData] = None
    long_short: Optional[LongShortRatio] = None

    @property
    def orderbook_pressure(self) -> str:
        if self.orderbook:
            return self.orderbook.pressure
        return "UNKNOWN"

    @property
    def positioning(self) -> str:
        """Are traders too long or too short? Combines L/S ratio + taker flow."""
        if not self.long_short:
            return "UNKNOWN"
        r = self.long_short.long_short_ratio
        
        # Base on account ratio
        if r > 2.0:
            base = "EXTREMELY_LONG"
        elif r > 1.4:
            base = "CROWDED_LONG"
        elif r < 0.5:
            base = "EXTREMELY_SHORT"
        elif r < 0.7:
            base = "CROWDED_SHORT"
        else:
            base = "BALANCED"
        
        # Confirm with taker flow if available
        if self.long_short.taker:
            if base in ("CROWDED_LONG", "EXTREMELY_LONG") and self.long_short.taker.pressure == "SELL_SIDE":
                return f"{base}_UNWINDING"  # Longs exiting — bearish signal
            if base in ("CROWDED_SHORT", "EXTREMELY_SHORT") and self.long_short.taker.pressure == "BUY_SIDE":
                return f"{base}_UNWINDING"  # Shorts covering — bullish signal
        
        return base

    @property
    def composite_bias(self) -> tuple[str, int]:
        """Combine all order flow signals into a single directional bias.
        
        Returns (bias, strength 0-10):
        - STRONG_LONG: 7-10
        - LONG: 4-6
        - NEUTRAL: 0-3 (no edge)
        - SHORT: 4-6
        - STRONG_SHORT: 7-10
        """
        score = 0  # positive = long bias, negative = short bias

        # Order book imbalance (max ±3)
        if self.orderbook:
            imb = self.orderbook.imbalance
            if imb > 0.3:
                score += 3
            elif imb > 0.1:
                score += 1
            elif imb < -0.3:
                score -= 3
            elif imb < -0.1:
                score -= 1

        # Taker flow (max ±4) — most important
        if self.long_short and self.long_short.taker:
            ratio = self.long_short.taker.buy_sell_ratio
            if ratio > 1.5:
                score += 4
            elif ratio > 1.2:
                score += 2
            elif ratio < 0.67:
                score -= 4
            elif ratio < 0.83:
                score -= 2

        # L/S ratio — contrarian signal (max ±2)
        if self.long_short:
            ls = self.long_short.long_short_ratio
            if ls > 2.0:
                score -= 2  # Too many longs → squeeze risk
            elif ls > 1.5:
                score -= 1
            elif ls < 0.5:
                score += 2  # Too many shorts → short squeeze risk
            elif ls < 0.7:
                score += 1

        # Convert to bias + strength
        abs_score = abs(score)
        strength = min(abs_score, 10)

        if score >= 7:
            return "STRONG_LONG", strength
        elif score >= 4:
            return "LONG", strength
        elif score <= -7:
            return "STRONG_SHORT", strength
        elif score <= -4:
            return "SHORT", strength
        return "NEUTRAL", strength

    def to_briefing(self) -> str:
        lines = []

        # Composite bias first — this is the headline
        bias, strength = self.composite_bias
        lines.append(f"⚡ COMPOSITE BIAS: {bias} ({strength}/10)")

        if self.orderbook:
            imb = self.orderbook.imbalance
            lines.append(
                f"  OrderBook: {self.orderbook.pressure} (imbalance {imb:+.2f}) "
                f"| Bid vol {self.orderbook.bid_volume:.0f} vs Ask vol {self.orderbook.ask_volume:.0f}"
            )
        if self.long_short:
            ls = self.long_short
            lines.append(
                f"  L/S Ratio: {ls.long_short_ratio:.2f} ({self.positioning}) "
                f"| Longs {ls.long_ratio*100:.0f}% Shorts {ls.short_ratio*100:.0f}%"
            )
            if ls.taker:
                t = ls.taker
                lines.append(
                    f"  Taker Flow: {t.pressure} (buy {t.buy_volume:.1f} vs sell {t.sell_volume:.1f}, ratio {t.buy_sell_ratio:.2f})"
                )
        if self.open_interest and self.open_interest.oi_value_usdt > 0:
            lines.append(
                f"  Open Interest: ${self.open_interest.oi_value_usdt:,.0f}"
            )
        return "\n".join(lines) if lines else "No order flow data"


@dataclass
class MarketContext:
    """Macro market context — what's happening across ALL symbols."""
    btc_price: float = 0
    btc_change_24h: float = 0
    btc_trend: str = "UNKNOWN"       # UP / DOWN / FLAT (from 24h change)

    # Market breadth
    breadth_up: int = 0              # how many alts are up 24h
    breadth_down: int = 0
    breadth_signal: str = "NEUTRAL"  # RISK_ON / RISK_OFF / NEUTRAL

    # Funding environment
    avg_funding: float = 0
    funding_extreme: str = "NONE"    # LONG_SQUEEZE / SHORT_SQUEEZE / NONE

    # Time
    timestamp: str = ""

    def to_briefing(self) -> str:
        lines = []
        lines.append(f"BTC: ${self.btc_price:,.0f} ({self.btc_change_24h:+.2f}% 24h) — {self.btc_trend}")
        lines.append(f"Market Breadth: {self.breadth_up} up / {self.breadth_down} down — {self.breadth_signal}")
        if self.funding_extreme != "NONE":
            lines.append(f"⚠️ Funding Extreme: {self.funding_extreme}")
        return "\n".join(lines)


async def fetch_symbol_context(client: BitgetMarketClient, symbol: str) -> SymbolContext:
    """Fetch order book + OI + L/S ratio for a single symbol."""
    orderbook, oi, ls = await asyncio.gather(
        client.get_orderbook(symbol),
        client.get_open_interest(symbol),
        client.get_long_short_ratio(symbol),
        return_exceptions=True,
    )
    return SymbolContext(
        orderbook=orderbook if isinstance(orderbook, OrderBookSnapshot) else None,
        open_interest=oi if isinstance(oi, OpenInterestData) else None,
        long_short=ls if isinstance(ls, LongShortRatio) else None,
    )


async def fetch_market_context(
    client: BitgetMarketClient,
    symbols: list[str],
    funding_rates: dict[str, Optional[float]],
) -> tuple[MarketContext, dict[str, SymbolContext]]:
    """Fetch full market context + per-symbol order flow data.

    Returns:
        market_context: Macro view (BTC, breadth, funding)
        symbol_contexts: Per-symbol order book, OI, L/S ratio
    """
    # 1. Get all tickers for breadth
    all_tickers = await client.get_all_tickers()

    # 2. BTC price
    btc_ticker = all_tickers.get("BTCUSDT")
    btc_price = btc_ticker.last_price if btc_ticker else 0
    btc_change = btc_ticker.change_24h if btc_ticker else 0

    btc_trend = "UP" if btc_change > 1.0 else "DOWN" if btc_change < -1.0 else "FLAT"

    # 3. Market breadth (exclude BTC from alt count)
    alt_tickers = {k: v for k, v in all_tickers.items() if k != "BTCUSDT" and k in symbols}
    breadth_up = sum(1 for t in alt_tickers.values() if t.change_24h > 0)
    breadth_down = sum(1 for t in alt_tickers.values() if t.change_24h <= 0)

    if breadth_up > breadth_down * 2:
        breadth_signal = "RISK_ON"
    elif breadth_down > breadth_up * 2:
        breadth_signal = "RISK_OFF"
    else:
        breadth_signal = "NEUTRAL"

    # 4. Funding extremes
    valid_funding = [f for f in funding_rates.values() if f is not None]
    avg_funding = sum(valid_funding) / len(valid_funding) if valid_funding else 0

    funding_extreme = "NONE"
    if avg_funding > 0.0003:  # 0.03% per 8h = very high
        funding_extreme = "LONG_SQUEEZE_RISK"  # Longs paying too much, could dump
    elif avg_funding < -0.0003:
        funding_extreme = "SHORT_SQUEEZE_RISK"  # Shorts paying, could pump

    market_ctx = MarketContext(
        btc_price=btc_price,
        btc_change_24h=btc_change,
        btc_trend=btc_trend,
        breadth_up=breadth_up,
        breadth_down=breadth_down,
        breadth_signal=breadth_signal,
        avg_funding=avg_funding,
        funding_extreme=funding_extreme,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # 5. Fetch per-symbol order flow (order book, OI, L/S ratio)
    # Do this in parallel for speed
    symbol_contexts = {}
    tasks = {}
    for sym in symbols:
        tasks[sym] = asyncio.create_task(fetch_symbol_context(client, sym))

    for sym, task in tasks.items():
        try:
            symbol_contexts[sym] = await task
        except Exception as e:
            print(f"[MarketContext] {sym} fetch failed: {e}")
            symbol_contexts[sym] = SymbolContext()

    return market_ctx, symbol_contexts
