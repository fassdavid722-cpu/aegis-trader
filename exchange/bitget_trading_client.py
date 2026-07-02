"""Bitget v2 Trading Client — actual order execution.

Handles authenticated endpoints with HMAC-SHA256 signing:
- Place market/limit orders
- Place plan orders (TP/SL triggers)
- Get open positions
- Close positions
- Set leverage & margin mode
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Optional, Any
import httpx

from .bitget_client import BitgetMarketClient


class BitgetTradingClient(BitgetMarketClient):
    """Extends market data client with authenticated trading endpoints."""

    def __init__(self, api_key: str, secret_key: str, passphrase: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self._trading_client: Optional[httpx.AsyncClient] = None

    async def _trading_http(self) -> httpx.AsyncClient:
        if self._trading_client is None:
            self._trading_client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=30.0,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "locale": "en-US",
                },
            )
        return self._trading_client

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate HMAC-SHA256 signature for Bitget v2 API."""
        message = f"{timestamp}{method}{path}{body}"
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """Build authenticated headers."""
        ts = str(int(time.time() * 1000))
        sign = self._sign(ts, method, path, body)
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    async def _auth_get(self, path: str, params: dict = None) -> dict:
        """Authenticated GET request."""
        client = await self._trading_http()
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + query
        headers = self._auth_headers("GET", path, query)
        r = await client.get(full_path, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _auth_post(self, path: str, payload: dict) -> dict:
        """Authenticated POST request."""
        client = await self._trading_http()
        body = json.dumps(payload)
        headers = self._auth_headers("POST", path, body)
        r = await client.post(path, content=body, headers=headers)
        r.raise_for_status()
        return r.json()

    # ─── Account & Position ──────────────────────────────────────────

    async def get_positions(self, product_type: str = "USDT-FUTURES") -> list[dict]:
        """Get all open positions."""
        try:
            d = await self._auth_get(
                "/api/v2/mix/position/all",
                {"productType": product_type},
            )
            if d.get("code") != "00000":
                print(f"[Trading] get_positions error: {d.get('msg')}")
                return []
            return d.get("data", [])
        except Exception as e:
            print(f"[Trading] get_positions exception: {e}")
            return []

    async def get_account_balance(self, product_type: str = "USDT-FUTURES") -> Optional[dict]:
        """Get futures account balance."""
        try:
            d = await self._auth_get(
                "/api/v2/mix/account/accounts",
                {"productType": product_type},
            )
            if d.get("code") != "00000":
                return None
            data = d.get("data", [])
            return data[0] if data else None
        except Exception as e:
            print(f"[Trading] get_balance error: {e}")
            return None

    async def set_leverage(self, symbol: str, leverage: int, margin_mode: str = "ISOLATED") -> bool:
        """Set leverage for a symbol."""
        try:
            d = await self._auth_post("/api/v2/mix/account/set-leverage", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": margin_mode,
                "leverage": str(leverage),
            })
            return d.get("code") == "00000"
        except Exception as e:
            print(f"[Trading] set_leverage error: {e}")
            return False

    async def set_margin_mode(self, symbol: str, margin_mode: str = "ISOLATED") -> bool:
        """Set margin mode for a symbol."""
        try:
            d = await self._auth_post("/api/v2/mix/account/set-margin-mode", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": margin_mode,
            })
            return d.get("code") == "00000"
        except Exception as e:
            print(f"[Trading] set_margin_mode error: {e}")
            return False

    # ─── Order Placement ─────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: str,           # "buy" or "sell" (Bitget format)
        size: str,            # quantity in contracts
        leverage: int = 10,
        reduce_only: bool = False,
    ) -> Optional[dict]:
        """Place a market order."""
        try:
            # Set leverage first
            await self.set_leverage(symbol, leverage)

            payload = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "ISOLATED",
                "marginCoin": "USDT",
                "side": side,
                "orderType": "market",
                "size": size,
                "tradeSide": "open" if not reduce_only else "close",
                "force": "gtc",
            }
            d = await self._auth_post("/api/v2/mix/order/placeOrder", payload)
            if d.get("code") != "00000":
                print(f"[Trading] order rejected: {d.get('msg')}")
                return None
            return d.get("data")
        except Exception as e:
            print(f"[Trading] place_market_order error: {e}")
            return None

    async def place_plan_order(
        self,
        symbol: str,
        side: str,           # "buy" or "sell"
        size: str,
        trigger_price: float,
        plan_type: str = "normal_plan",  # normal_plan or profit_loss
        reduce_only: bool = True,
    ) -> Optional[dict]:
        """Place a conditional/plan order (for TP/SL)."""
        try:
            payload = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "ISOLATED",
                "marginCoin": "USDT",
                "side": side,
                "orderType": "market",
                "size": size,
                "triggerPrice": str(trigger_price),
                "triggerType": "mark_price",
                "planType": plan_type,
                "tradeSide": "close" if reduce_only else "open",
                "force": "gtc",
            }
            d = await self._auth_post("/api/v2/mix/order/placePlan", payload)
            if d.get("code") != "00000":
                print(f"[Trading] plan order rejected: {d.get('msg')}")
                return None
            return d.get("data")
        except Exception as e:
            print(f"[Trading] place_plan_order error: {e}")
            return None

    async def close_position(self, symbol: str, side: str = "") -> bool:
        """Close an entire position."""
        try:
            payload = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "ISOLATED",
                "marginCoin": "USDT",
            }
            d = await self._auth_post("/api/v2/mix/order/close-position", payload)
            return d.get("code") == "00000"
        except Exception as e:
            print(f"[Trading] close_position error: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel all pending orders for a symbol."""
        try:
            d = await self._auth_post("/api/v2/mix/order/cancel-batch-orders", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "ISOLATED",
                "marginCoin": "USDT",
            })
            return d.get("code") == "00000"
        except Exception as e:
            print(f"[Trading] cancel_orders error: {e}")
            return False

    async def close(self) -> None:
        await super().close()
        if self._trading_client:
            await self._trading_client.aclose()
            self._trading_client = None


# ─── Helper: calculate position size ───────────────────────────────

def calculate_position_size(
    account_equity: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
    leverage: int = 10,
) -> str:
    """Calculate order size in USDT contracts for Bitget futures.

    Bitget USDT-FUTURES uses 1 contract = 1 USDT for most symbols.
    Returns size as string (Bitget expects string).
    """
    risk_amount = account_equity * (risk_percent / 100.0)
    sl_distance = abs(entry_price - stop_loss)

    if sl_distance <= 0:
        return "0"

    # Position size in base currency = risk_amount / sl_distance
    # Convert to contracts (1 contract = 1 USDT for USDT-FUTURES)
    position_value = risk_amount / sl_distance * entry_price  # total position value

    # Ensure we don't exceed leverage * equity
    max_position = account_equity * leverage
    if position_value > max_position:
        position_value = max_position

    # Minimum order size on Bitget is typically 1 USDT
    contracts = max(int(position_value), 1)
    return str(contracts)
