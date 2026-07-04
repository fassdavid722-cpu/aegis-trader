"""Bitget market data client — Enhanced with full toolkit.

Read-only market data. No trading API calls.

Data sources:
- Ticker (24h stats, price)
- Candles (OHLCV, multi-timeframe)
- Funding rate
- Order book depth (buy/sell pressure)
- Open interest (position squeeze detection)
- Long/short ratio (trader positioning)
- Batch tickers (market breadth)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable, Any

import httpx

from .candle_normalizer import normalize_candles


@dataclass
class BitgetTicker:
    symbol: str
    last_price: float
    high_24h: float
    low_24h: float
    volume_24h: float
    change_24h: float
    timestamp: datetime


@dataclass
class OrderBookSnapshot:
    """Order book depth — buy/sell pressure analysis."""
    symbol: str
    bids: list[tuple[float, float]]  # [(price, size), ...] top 20
    asks: list[tuple[float, float]]
    bid_volume: float = 0.0          # total buy wall volume
    ask_volume: float = 0.0          # total sell wall volume
    imbalance: float = 0.0           # -1 (all sells) to +1 (all buys)
    spread_pct: float = 0.0          # bid-ask spread as % of price

    @property
    def pressure(self) -> str:
        if self.imbalance > 0.3:
            return "STRONG_BUY_PRESSURE"
        elif self.imbalance > 0.1:
            return "BUY_PRESSURE"
        elif self.imbalance < -0.3:
            return "STRONG_SELL_PRESSURE"
        elif self.imbalance < -0.1:
            return "SELL_PRESSURE"
        return "BALANCED"


@dataclass
class OpenInterestData:
    """Open interest — detect squeezes and positioning shifts."""
    symbol: str
    current_oi: float                # current open interest (contracts)
    oi_value_usdt: float             # OI in USDT
    timestamp: datetime


@dataclass
class TakerBuySell:
    """Real-time taker buy/sell volume — actual order flow pressure."""
    buy_volume: float
    sell_volume: float
    buy_sell_ratio: float           # >1 = more buying
    pressure: str                   # BUY_SIDE / SELL_SIDE / BALANCED


@dataclass
class LongShortRatio:
    """Trader positioning — long vs short ratio + taker flow."""
    symbol: str
    long_ratio: float                # 0-1
    short_ratio: float               # 0-1
    long_short_ratio: float          # >1 = more longs
    taker: Optional[TakerBuySell] = None  # Real order flow
    timestamp: datetime = None


class BitgetMarketClient:
    BASE_URL = "https://api.bitget.com"

    def __init__(self) -> None:
        self._rest_client: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._rest_client is None:
            self._rest_client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=30.0,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        return self._rest_client

    async def get_ticker(self, symbol: str) -> Optional[BitgetTicker]:
        c = await self._client()
        try:
            r = await c.get("/api/v2/mix/market/ticker",
                            params={"symbol": symbol, "productType": "USDT-FUTURES"})
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return None
            t = d["data"][0]
            return BitgetTicker(
                symbol=symbol,
                last_price=float(t.get("lastPr", 0)),
                high_24h=float(t.get("high24h", 0)),
                low_24h=float(t.get("low24h", 0)),
                volume_24h=float(t.get("baseVolume", 0)),
                change_24h=float(t.get("change24h", 0)),
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"Ticker error {symbol}: {e}")
            return None

    async def get_all_tickers(self) -> dict[str, BitgetTicker]:
        """Batch fetch all USDT-FUTURES tickers — for market breadth."""
        c = await self._client()
        try:
            r = await c.get("/api/v2/mix/market/tickers",
                            params={"productType": "USDT-FUTURES"})
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return {}
            result = {}
            for t in d.get("data", []):
                sym = t.get("symbol", "")
                result[sym] = BitgetTicker(
                    symbol=sym,
                    last_price=float(t.get("lastPr", 0)),
                    high_24h=float(t.get("high24h", 0)),
                    low_24h=float(t.get("low24h", 0)),
                    volume_24h=float(t.get("baseVolume", 0)),
                    change_24h=float(t.get("change24h", 0)),
                    timestamp=datetime.now(timezone.utc),
                )
            return result
        except Exception as e:
            print(f"All tickers error: {e}")
            return {}

    async def get_candles(
        self, symbol: str, granularity: str = "15m", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get normalized candles. Bitget returns arrays; we convert to dicts."""
        c = await self._client()
        try:
            r = await c.get(
                "/api/v2/mix/market/candles",
                params={
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    "granularity": granularity,
                    "limit": min(limit, 1000),
                },
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return []
            raw = d.get("data", [])
            normalized = normalize_candles(raw)
            return normalized  # API already returns oldest → newest
        except Exception as e:
            print(f"Candles error {symbol}/{granularity}: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate."""
        c = await self._client()
        try:
            r = await c.get(
                "/api/v2/mix/market/current-fund-rate",
                params={"symbol": symbol, "productType": "USDT-FUTURES"},
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return None
            data = d.get("data", [{}])
            if not data:
                return None
            return float(data[0].get("fundingRate", 0))
        except Exception as e:
            print(f"Funding error {symbol}: {e}")
            return None

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[OrderBookSnapshot]:
        """Get order book depth — buy/sell wall analysis."""
        c = await self._client()
        try:
            r = await c.get(
                "/api/v2/mix/market/merge-depth",
                params={
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    "depth": "merge0.1",
                },
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return None

            data = d.get("data", {})
            asks_raw = data.get("asks", [])[:depth]
            bids_raw = data.get("bids", [])[:depth]

            # Sort: bids descending (best bid first), asks ascending (best ask first)
            bids = [(float(p), float(s)) for p, s in bids_raw]
            asks = [(float(p), float(s)) for p, s in asks_raw]
            asks.sort(key=lambda x: x[0])
            bids.sort(key=lambda x: -x[0])

            bid_vol = sum(s for _, s in bids)
            ask_vol = sum(s for _, s in asks)
            total = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total if total > 0 else 0

            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            spread_pct = ((best_ask - best_bid) / mid_price * 100) if mid_price > 0 else 0

            return OrderBookSnapshot(
                symbol=symbol,
                bids=bids,
                asks=asks,
                bid_volume=bid_vol,
                ask_volume=ask_vol,
                imbalance=imbalance,
                spread_pct=spread_pct,
            )
        except Exception as e:
            print(f"Orderbook error {symbol}: {e}")
            return None

    async def get_open_interest(self, symbol: str) -> Optional[OpenInterestData]:
        """Get open interest — detect squeezes and position buildup."""
        c = await self._client()
        try:
            r = await c.get(
                "/api/v2/mix/market/open-interest",
                params={"symbol": symbol, "productType": "USDT-FUTURES"},
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return None
            # OI data format: {"openInterestList": [{"symbol": "BTCUSDT", "size": "35035.0873"}], "ts": "..."}
            data = d.get("data", {})
            oi_list = data.get("openInterestList", []) if isinstance(data, dict) else []
            if not oi_list:
                return None
            item = oi_list[0]
            oi = float(item.get("size", 0))
            return OpenInterestData(
                symbol=symbol,
                current_oi=oi,
                oi_value_usdt=oi,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"OI error {symbol}: {e}")
            return None

    async def get_long_short_ratio(self, symbol: str) -> Optional[LongShortRatio]:
        """Get long/short trader ratio + taker buy/sell volume — positioning data."""
        c = await self._client()
        try:
            # Account long/short ratio
            r = await c.get(
                "/api/v2/mix/market/account-long-short",
                params={"symbol": symbol, "productType": "USDT-FUTURES", "period": "5m"},
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != "00000":
                return None
            data = d.get("data", [{}])
            if not data:
                return None
            item = data[0]
            long_r = float(item.get("longAccountRatio", 0.5))
            short_r = float(item.get("shortAccountRatio", 0.5))
            ratio = long_r / short_r if short_r > 0 else 1.0

            # Also fetch taker buy/sell for real order flow
            taker = None
            try:
                r2 = await c.get(
                    "/api/v2/mix/market/taker-buy-sell",
                    params={"symbol": symbol, "productType": "USDT-FUTURES", "period": "5m"},
                )
                d2 = r2.json()
                if d2.get("code") == "00000" and d2.get("data"):
                    t = d2["data"][0]
                    buy_v = float(t.get("buyVolume", 0))
                    sell_v = float(t.get("sellVolume", 0))
                    bs_ratio = buy_v / sell_v if sell_v > 0 else 1.0
                    pressure = "BUY_SIDE" if bs_ratio > 1.2 else "SELL_SIDE" if bs_ratio < 0.8 else "BALANCED"
                    taker = TakerBuySell(
                        buy_volume=buy_v,
                        sell_volume=sell_v,
                        buy_sell_ratio=bs_ratio,
                        pressure=pressure,
                    )
            except Exception:
                pass

            return LongShortRatio(
                symbol=symbol,
                long_ratio=long_r,
                short_ratio=short_r,
                long_short_ratio=ratio,
                taker=taker,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"L/S ratio error {symbol}: {e}")
            return None

    async def close(self) -> None:
        if self._rest_client:
            await self._rest_client.aclose()
            self._rest_client = None


class BitgetPriceMonitor:
    def __init__(self, client: BitgetMarketClient, check_interval: float = 5.0) -> None:
        self.client = client
        self.check_interval = check_interval
        self._running = False
        self._callbacks: list[Callable[[str, float], None]] = []

    def register_callback(self, callback: Callable[[str, float], None]) -> None:
        self._callbacks.append(callback)

    async def monitor_symbols(self, symbols: list[str]) -> None:
        self._running = True
        while self._running:
            for symbol in symbols:
                if not self._running:
                    break
                ticker = await self.client.get_ticker(symbol)
                if ticker:
                    for cb in self._callbacks:
                        try:
                            cb(symbol, ticker.last_price)
                        except Exception as e:
                            print(f"Callback error {symbol}: {e}")
                await asyncio.sleep(0.5)
            await asyncio.sleep(self.check_interval)

    def stop(self) -> None:
        self._running = False
