"""
mode_config.py — DEUS 3.0
=========================
Testing/Production mode toggle for email outreach.

Controls:
  - IS_PRODUCTION_MODE: boolean — set via env var, state file, or set_mode()
  - get_mode_config(): returns the active mode's settings dict
  - set_mode(production: bool): persist toggle to mode_state.json
  - get_delay_range(), get_daily_limit(), get_hourly_limit()

Priority (highest to lowest):
  1. IS_PRODUCTION_MODE env var (overrides everything)
  2. mode_state.json file (set by the UI toggle)
  3. Default: testing mode (False)
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

MODE_STATE_FILE = os.path.join(os.path.dirname(__file__), "mode_state.json")

_MODE_CONFIGS = {
    "testing": {
        "delay_min": 180,
        "delay_max": 300,
        "daily_limit": 15,
        "hourly_limit": 5,
    },
    "production": {
        "delay_min": 90,
        "delay_max": 180,
        "daily_limit": 100,
        "hourly_limit": 15,
    },
}


def _load_state() -> bool:
    """Load mode from state file. Returns True if production, False if testing."""
    try:
        with open(MODE_STATE_FILE, "r") as f:
            data = json.load(f)
            return bool(data.get("production", False))
    except Exception:
        return False


def _resolve_mode() -> bool:
    """Resolve mode with priority: env var > state file > default (testing)."""
    env_val = os.getenv("IS_PRODUCTION_MODE", "").strip().lower()
    if env_val == "true":
        return True
    if env_val == "false":
        return False
    return _load_state()


IS_PRODUCTION_MODE = _resolve_mode()


def set_mode(production: bool) -> dict:
    """Set the mode at runtime and persist to mode_state.json.
    Returns the current mode config dict."""
    global IS_PRODUCTION_MODE
    IS_PRODUCTION_MODE = production
    try:
        with open(MODE_STATE_FILE, "w") as f:
            json.dump({"production": production}, f)
        logger.info("Mode set to: %s", "PRODUCTION" if production else "TESTING")
    except Exception as e:
        logger.warning("Failed to persist mode state: %s", e)
    return get_mode_config()


def _active_mode() -> str:
    return "production" if IS_PRODUCTION_MODE else "testing"


def get_mode_config() -> dict:
    """Return the full config dict for the active mode."""
    return _MODE_CONFIGS[_active_mode()]


def get_delay_range() -> tuple:
    """Return (min_seconds, max_seconds) for random send delay."""
    cfg = get_mode_config()
    return (cfg["delay_min"], cfg["delay_max"])


def get_daily_limit() -> int:
    """Return the daily send limit for the active mode."""
    return get_mode_config()["daily_limit"]


def get_hourly_limit() -> int:
    """Return the hourly send limit for the active mode."""
    return get_mode_config()["hourly_limit"]


def is_production() -> bool:
    """Check if we're in production mode."""
    return IS_PRODUCTION_MODE


def mode_name() -> str:
    """Return human-readable mode name."""
    return _active_mode().title()


def mode_info() -> dict:
    """Return full mode info dict for API responses."""
    cfg = get_mode_config()
    return {
        "production": IS_PRODUCTION_MODE,
        "mode_name": mode_name(),
        "delay_min": cfg["delay_min"],
        "delay_max": cfg["delay_max"],
        "daily_limit": cfg["daily_limit"],
        "hourly_limit": cfg["hourly_limit"],
    }
