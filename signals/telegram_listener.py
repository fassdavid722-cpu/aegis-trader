"""Telegram bot for receiving signals and sending notifications.

Uses python-telegram-bot v20+ async interface.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Callable, Any

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes,
    CommandHandler,
)

from config import get_config, validate_telegram_token
from signals.models import Signal
from .parser import SignalParser


class TelegramSignalListener:
    """Listens for trading signals via Telegram messages."""

    def __init__(
        self,
        on_signal: Callable[[Signal], None],
        on_close_signal: Callable[[Signal], None],
    ) -> None:
        """Initialize Telegram listener.

        Args:
            on_signal: Callback for entry signals
            on_close_signal: Callback for close signals
        """
        self.config = get_config()
        self.parser = SignalParser()
        self.on_signal = on_signal
        self.on_close_signal = on_close_signal
        self.application: Optional[Application] = None

    async def start(self) -> None:
        """Start the Telegram bot."""
        token = self.config.telegram.bot_token

        if not token or not validate_telegram_token(token):
            raise ValueError("Invalid or missing Telegram bot token")

        self.application = (
            Application.builder()
            .token(token)
            .concurrent_updates(True)
            .build()
        )

        # Handlers
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self.application.add_handler(
            CommandHandler("status", self._handle_status)
        )

        await self.application.initialize()
        await self.application.start()

        # Start polling
        await self.application.updater.start_polling()

        print("Telegram signal listener started")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text message."""
        if not update.message or not update.message.text:
            return

        text = update.message.text

        # Parse the signal
        signal = self.parser.parse_telegram(text)

        if not signal:
            await update.message.reply_text("Could not parse signal")
            return

        # Route to appropriate handler
        if signal.is_entry_signal():
            self.on_signal(signal)
            await update.message.reply_text(
                f"Signal received: {signal.side.value} {signal.symbol}\n"
                f"Entry: {signal.entry or 'Market'} | SL: {signal.stop_loss or 'None'} | TP: {signal.take_profit or 'None'}"
            )
        elif signal.is_close_signal():
            self.on_close_signal(signal)
            await update.message.reply_text(
                f"Close signal: {signal.symbol}"
            )

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        await update.message.reply_text("Aegis Trader is running")

    async def send_notification(self, chat_id: str, message: str) -> None:
        """Send notification message."""
        if self.application:
            await self.application.bot.send_message(chat_id=chat_id, text=message)
