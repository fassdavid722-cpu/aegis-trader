"""Session timing filter.

Only trade during high-probability sessions.
Avoid Asia session (manipulation, low volume).
"""
from __future__ import annotations

from datetime import datetime, timezone, time
from typing import Optional

from .models_v2 import Session


class SessionFilter:
    """Determines if current time is a valid trading session."""

    # Session boundaries (UTC)
    ASIA_START = time(0, 0)
    ASIA_END = time(3, 0)

    LONDON_START = time(7, 0)
    LONDON_END = time(9, 0)

    NY_START = time(13, 0)
    NY_END = time(15, 0)

    FUTURES_START = time(20, 0)
    FUTURES_END = time(21, 0)

    @classmethod
    def get_current_session(cls, dt: Optional[datetime] = None) -> Session:
        """Determine which session we're in.

        Args:
            dt: Datetime to check (default: now UTC)

        Returns:
            Session enum
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        t = dt.time()

        if cls.LONDON_START <= t <= cls.LONDON_END:
            return Session.LONDON
        elif cls.NY_START <= t <= cls.NY_END:
            return Session.NY
        elif cls.FUTURES_START <= t <= cls.FUTURES_END:
            return Session.FUTURES
        elif cls.ASIA_START <= t <= cls.ASIA_END:
            return Session.ASIA
        else:
            return Session.OFF_HOURS

    @classmethod
    def is_trade_session(cls, dt: Optional[datetime] = None) -> bool:
        """Check if current time is a valid trading session.

        Only London and NY opens are valid for entries.
        """
        session = cls.get_current_session(dt)
        return session in (Session.LONDON, Session.NY)

    @classmethod
    def get_session_name(cls, dt: Optional[datetime] = None) -> str:
        """Get human-readable session name."""
        session = cls.get_current_session(dt)
        return session.value
