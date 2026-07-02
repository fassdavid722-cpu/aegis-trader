"""Central configuration for Aegis Trader.

All settings are loaded from environment variables with sensible defaults.
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# Load .env file if present
load_dotenv()


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot and channel configuration."""
    bot_token: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))
    webhook_url: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_URL"))

    @property
    def enabled(self) -> bool:
        return self.bot_token is not None and self.bot_token != "your_bot_token_here"


@dataclass(frozen=True)
class BitgetConfig:
    """Bitget API configuration (read-only market data in V1)."""
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("BITGET_API_KEY"))
    secret_key: Optional[str] = field(default_factory=lambda: os.getenv("BITGET_SECRET_KEY"))
    passphrase: Optional[str] = field(default_factory=lambda: os.getenv("BITGET_PASSPHRASE"))
    base_url: str = "https://api.bitget.com"
    ws_url: str = "wss://ws.bitget.com/mix/v1/stream"

    @property
    def has_credentials(self) -> bool:
        return all([self.api_key, self.secret_key, self.passphrase])


@dataclass(frozen=True)
class SystemConfig:
    """System-level configuration."""
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    database_path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH", "./data/journal.db")))

    def __post_init__(self):
        # Ensure data directory exists
        if isinstance(self.data_dir, str):
            object.__setattr__(self, 'data_dir', Path(self.data_dir))
        if isinstance(self.database_path, str):
            object.__setattr__(self, 'database_path', Path(self.database_path))
        self.data_dir.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TradingConfig:
    """Virtual trading parameters."""
    # Fill model: max slippage allowed before skipping signal (as decimal)
    max_slippage: float = 0.005  # 0.5%
    # Time decay: seconds before a pending entry expires
    entry_time_decay: int = 300  # 5 minutes
    # Default leverage for signals that don't specify
    default_leverage: int = 10
    # Default margin mode
    default_margin_mode: str = "ISOLATED"
    # Trading fee (taker) as decimal - Bitget USDT-M futures
    trading_fee_rate: float = 0.0006  # 0.06%
    # Funding fee check interval (seconds)
    funding_check_interval: int = 3600


@dataclass(frozen=True)
class AppConfig:
    """Root configuration container."""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    bitget: BitgetConfig = field(default_factory=BitgetConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


# Singleton instance
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def reset_config() -> None:
    """Reset configuration (useful for testing)."""
    global _config
    _config = None
