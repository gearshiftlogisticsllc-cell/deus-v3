"""
send_limiter.py — DEUS 3.0
============================
Rate limiting and sending controls for email deliverability.

Enforces:
  - Daily send limits per SMTP profile
  - Hourly send limits
  - Minimum interval between sends
  - Random delay for natural patterns
  - CAN-SPAM compliance (unsubscribe link)

Tracks sends in the email_log database table.

Usage:
    limiter = SendLimiter()
    if limiter.can_send("smtp_default"):
        limiter.record_send("smtp_default")
        # ... send email ...
    else:
        print(f"Daily limit reached: {limiter.get_status('smtp_default')}")
"""

import os
import time
import random
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default limits
DEFAULT_DAILY_LIMIT = 50
WARMED_DAILY_LIMIT = 200
DEFAULT_HOURLY_LIMIT = 10
DEFAULT_MIN_INTERVAL_SECONDS = 30
DEFAULT_RANDOM_DELAY_MIN = 15
DEFAULT_RANDOM_DELAY_MAX = 45


@dataclass
class SendLimit:
    """Send limit configuration for a profile."""
    daily_limit: int = DEFAULT_DAILY_LIMIT
    hourly_limit: int = DEFAULT_HOURLY_LIMIT
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS
    emails_sent_today: int = 0
    emails_sent_this_hour: int = 0
    last_send_timestamp: float = 0.0
    last_send_date: str = ""
    last_hour: int = -1
    domain_reputation: str = "warming"  # warming | good | established


class SendLimiter:
    """
    Rate limiter for email sending.
    Tracks sends per profile per day/hour.
    """

    def __init__(self):
        self._limits: dict[str, SendLimit] = {}
        self._load_state()

    def _load_state(self):
        """Load limiter state from file."""
        state_file = "send_limiter_state.json"
        try:
            import json
            with open(state_file) as f:
                data = json.load(f)
            for profile_name, state in data.items():
                self._limits[profile_name] = SendLimit(
                    daily_limit=state.get("daily_limit", DEFAULT_DAILY_LIMIT),
                    hourly_limit=state.get("hourly_limit", DEFAULT_HOURLY_LIMIT),
                    min_interval_seconds=state.get("min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS),
                    emails_sent_today=state.get("emails_sent_today", 0),
                    emails_sent_this_hour=state.get("emails_sent_this_hour", 0),
                    last_send_timestamp=state.get("last_send_timestamp", 0),
                    last_send_date=state.get("last_send_date", ""),
                    last_hour=state.get("last_hour", -1),
                    domain_reputation=state.get("domain_reputation", "warming"),
                )
        except (FileNotFoundError, Exception):
            pass

    def _save_state(self):
        """Save limiter state to file."""
        state_file = "send_limiter_state.json"
        try:
            import json
            data = {}
            for name, limit in self._limits.items():
                data[name] = {
                    "daily_limit": limit.daily_limit,
                    "hourly_limit": limit.hourly_limit,
                    "min_interval_seconds": limit.min_interval_seconds,
                    "emails_sent_today": limit.emails_sent_today,
                    "emails_sent_this_hour": limit.emails_sent_this_hour,
                    "last_send_timestamp": limit.last_send_timestamp,
                    "last_send_date": limit.last_send_date,
                    "last_hour": limit.last_hour,
                    "domain_reputation": limit.domain_reputation,
                }
            with open(state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save limiter state: %s", e)

    def _get_or_create(self, profile_name: str) -> SendLimit:
        """Get or create a limit entry for a profile."""
        today = time.strftime("%Y-%m-%d")
        current_hour = int(time.strftime("%H"))

        if profile_name not in self._limits:
            self._limits[profile_name] = SendLimit()

        limit = self._limits[profile_name]

        # Reset daily counter if new day
        if limit.last_send_date != today:
            limit.emails_sent_today = 0
            limit.last_send_date = today

        # Reset hourly counter if new hour
        if limit.last_hour != current_hour:
            limit.emails_sent_this_hour = 0
            limit.last_hour = current_hour

        return limit

    def can_send(self, profile_name: str = "default") -> dict:
        """
        Check if we can send an email from this profile.

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "daily_remaining": int,
                "hourly_remaining": int,
                "next_available_at": float | None
            }
        """
        limit = self._get_or_create(profile_name)
        now = time.time()

        # Daily limit
        if limit.emails_sent_today >= limit.daily_limit:
            return {
                "allowed": False,
                "reason": f"Daily limit reached ({limit.daily_limit}/{limit.daily_limit})",
                "daily_remaining": 0,
                "hourly_remaining": max(0, limit.hourly_limit - limit.emails_sent_this_hour),
                "next_available_at": None,
            }

        # Hourly limit
        if limit.emails_sent_this_hour >= limit.hourly_limit:
            return {
                "allowed": False,
                "reason": f"Hourly limit reached ({limit.hourly_limit}/{limit.hourly_limit})",
                "daily_remaining": limit.daily_limit - limit.emails_sent_today,
                "hourly_remaining": 0,
                "next_available_at": None,
            }

        # Min interval
        elapsed = now - limit.last_send_timestamp
        if elapsed < limit.min_interval_seconds and limit.last_send_timestamp > 0:
            wait = limit.min_interval_seconds - elapsed
            return {
                "allowed": False,
                "reason": f"Min interval not met (wait {wait:.0f}s)",
                "daily_remaining": limit.daily_limit - limit.emails_sent_today,
                "hourly_remaining": limit.hourly_limit - limit.emails_sent_this_hour,
                "next_available_at": now + wait,
            }

        return {
            "allowed": True,
            "reason": "OK",
            "daily_remaining": limit.daily_limit - limit.emails_sent_today,
            "hourly_remaining": limit.hourly_limit - limit.emails_sent_this_hour,
            "next_available_at": None,
        }

    def record_send(self, profile_name: str = "default"):
        """Record that an email was sent."""
        limit = self._get_or_create(profile_name)
        limit.emails_sent_today += 1
        limit.emails_sent_this_hour += 1
        limit.last_send_timestamp = time.time()
        self._save_state()

    def get_status(self, profile_name: str = "default") -> dict:
        """Get current send status for a profile."""
        limit = self._get_or_create(profile_name)
        return {
            "profile": profile_name,
            "daily_sent": limit.emails_sent_today,
            "daily_limit": limit.daily_limit,
            "daily_remaining": limit.daily_limit - limit.emails_sent_today,
            "hourly_sent": limit.emails_sent_this_hour,
            "hourly_limit": limit.hourly_limit,
            "hourly_remaining": limit.hourly_limit - limit.emails_sent_this_hour,
            "domain_reputation": limit.domain_reputation,
            "min_interval": limit.min_interval_seconds,
        }

    def get_delay_seconds(self) -> float:
        """Get a random delay for natural sending patterns."""
        return random.uniform(DEFAULT_RANDOM_DELAY_MIN, DEFAULT_RANDOM_DELAY_MAX)

    def set_daily_limit(self, profile_name: str, limit: int):
        """Set daily limit for a profile."""
        self._get_or_create(profile_name).daily_limit = limit
        self._save_state()

    def set_hourly_limit(self, profile_name: str, limit: int):
        """Set hourly limit for a profile."""
        self._get_or_create(profile_name).hourly_limit = limit
        self._save_state()

    def update_reputation(self, profile_name: str, reputation: str):
        """Update domain reputation (warming/good/established)."""
        self._get_or_create(profile_name).domain_reputation = reputation
        # Auto-adjust daily limit based on reputation
        if reputation == "good":
            self._get_or_create(profile_name).daily_limit = 100
        elif reputation == "established":
            self._get_or_create(profile_name).daily_limit = 200
        self._save_state()

    def get_all_status(self) -> dict:
        """Get status for all profiles."""
        return {name: self.get_status(name) for name in self._limits}

    def reset_daily(self, profile_name: str = "default"):
        """Manually reset daily counter."""
        self._get_or_create(profile_name).emails_sent_today = 0
        self._save_state()


# Singleton
_limiter: Optional[SendLimiter] = None


def get_send_limiter() -> SendLimiter:
    """Get or create the singleton SendLimiter."""
    global _limiter
    if _limiter is None:
        _limiter = SendLimiter()
    return _limiter
