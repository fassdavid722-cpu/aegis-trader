"""Secrets management - thin wrapper around environment variables.

Never hardcode secrets. Always use environment variables.
This module exists to centralize secret access and provide validation.
"""
from __future__ import annotations

import os
from typing import Optional


def get_telegram_token() -> Optional[str]:
    """Get Telegram bot token from environment."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token and token != "your_bot_token_here":
        return token
    return None


def get_bitget_credentials() -> dict[str, Optional[str]]:
    """Get Bitget API credentials from environment.

    Returns empty dict if not configured (market data works without auth).
    """
    return {
        "api_key": os.getenv("BITGET_API_KEY"),
        "secret_key": os.getenv("BITGET_SECRET_KEY"),
        "passphrase": os.getenv("BITGET_PASSPHRASE"),
    }


def validate_telegram_token(token: str) -> bool:
    """Basic validation of Telegram bot token format."""
    if not token or ":" not in token:
        return False
    parts = token.split(":")
    return len(parts) == 2 and parts[0].isdigit() and len(parts[1]) > 20
