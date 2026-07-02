"""Bitget market data client.

Read-only market data. No trading API calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
            # Normalize: oldest first (Bitget returns newest first)
            raw = d.get("data", [])
            normalized = normalize_candles(raw)
            return list(reversed(normalized))  # oldest → newest
        except Exception as e:
            print(f"Candles error {symbol}/{granularity}: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate. Uses correct v2 endpoint."""
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
