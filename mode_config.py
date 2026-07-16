"""
mode_config.py — DEUS 3.0
=========================
Production-only mode configuration for email outreach.
Testing mode has been removed — system always runs in production.

Controls:
  - get_delay_range(): returns (min_seconds, max_seconds) for send delays
  - get_daily_limit(): returns daily send limit
  - get_hourly_limit(): returns hourly send limit
"""

import logging

logger = logging.getLogger(__name__)

_CONFIG = {
    "delay_min": 90,
    "delay_max": 180,
    "daily_limit": 100,
    "hourly_limit": 15,
}


def get_mode_config() -> dict:
    return dict(_CONFIG)


def get_delay_range() -> tuple:
    return (_CONFIG["delay_min"], _CONFIG["delay_max"])


def get_daily_limit() -> int:
    return _CONFIG["daily_limit"]


def get_hourly_limit() -> int:
    return _CONFIG["hourly_limit"]


def is_production() -> bool:
    return True


def mode_name() -> str:
    return "Production"


def mode_info() -> dict:
    return {
        "production": True,
        "mode_name": "Production",
        "delay_min": _CONFIG["delay_min"],
        "delay_max": _CONFIG["delay_max"],
        "daily_limit": _CONFIG["daily_limit"],
        "hourly_limit": _CONFIG["hourly_limit"],
    }
