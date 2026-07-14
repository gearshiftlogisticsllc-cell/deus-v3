"""
outreach_config.py
-------------------
Persisted configuration for OutreachAgent:

  1. Email style/template settings (tone, signature, custom template text,
     subject line pattern) — saved to outreach_style_config.json
  2. Named SMTP server profiles, so you can configure multiple sending
     accounts and pick one per run — saved to smtp_profiles.json
     (passwords are stored locally in this file; treat it like a secret,
     same as your .env)

Both are simple JSON-backed dataclasses with load/save helpers, used by
outreach_agent.py and the GUI configuration panel.
"""

import json
import os
import logging
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)

STYLE_CONFIG_FILE = "outreach_style_config.json"
SMTP_PROFILES_FILE = "smtp_profiles.json"


# ---------------------------------------------------------------------------
# Email style / template configuration
# ---------------------------------------------------------------------------

@dataclass
class StyleConfig:
    tone: str = "professional"                 # professional | friendly | direct | formal
    max_words: int = 120
    subject_template: str = "Quick proposal for {business_name}"
    signature: str = ""                         # appended to every message
    custom_template: str = ""                   # if set, used INSTEAD of LLM generation
    use_custom_template: bool = False
    call_to_action: str = "Would you be open to a short call this week?"
    ai_email_enabled: bool = True               # Use LLM to generate emails (vs template only)


def load_style_config() -> StyleConfig:
    if not os.path.exists(STYLE_CONFIG_FILE):
        return StyleConfig()
    try:
        with open(STYLE_CONFIG_FILE, "r") as f:
            data = json.load(f)
        return StyleConfig(**{**asdict(StyleConfig()), **data})
    except Exception as e:
        logger.warning("Failed to load %s (%s) — using defaults.", STYLE_CONFIG_FILE, e)
        return StyleConfig()


def save_style_config(config: StyleConfig) -> None:
    with open(STYLE_CONFIG_FILE, "w") as f:
        json.dump(asdict(config), f, indent=2)


def render_custom_template(template: str, lead: dict, signature: str) -> str:
    """Fill {placeholders} in a user-provided template with lead fields."""
    try:
        body = template.format(
            business_name=lead.get("business_name", "there"),
            niche=lead.get("niche", "your industry"),
            services_offered=lead.get("services_offered", ""),
            address=lead.get("address", ""),
            website=lead.get("website", ""),
        )
    except (KeyError, IndexError) as e:
        logger.warning("Template placeholder error (%s) — using template as-is.", e)
        body = template
    if signature:
        body = f"{body}\n\n{signature}"
    return body


# ---------------------------------------------------------------------------
# SMTP profiles (multiple sending accounts)
# ---------------------------------------------------------------------------

@dataclass
class SmtpProfile:
    profile_name: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_email: str = ""
    smtp_password: str = ""
    is_default: bool = False


def load_smtp_profiles() -> list[SmtpProfile]:
    if not os.path.exists(SMTP_PROFILES_FILE):
        return []
    try:
        with open(SMTP_PROFILES_FILE, "r") as f:
            data = json.load(f)
        return [SmtpProfile(**p) for p in data]
    except Exception as e:
        logger.warning("Failed to load %s (%s).", SMTP_PROFILES_FILE, e)
        return []


def save_smtp_profiles(profiles: list[SmtpProfile]) -> None:
    with open(SMTP_PROFILES_FILE, "w") as f:
        json.dump([asdict(p) for p in profiles], f, indent=2)


def upsert_smtp_profile(profile: SmtpProfile) -> list[SmtpProfile]:
    """Add or replace a profile by name, then save. Returns the updated list."""
    profiles = load_smtp_profiles()
    profiles = [p for p in profiles if p.profile_name != profile.profile_name]

    if profile.is_default:
        for p in profiles:
            p.is_default = False

    profiles.append(profile)
    save_smtp_profiles(profiles)
    return profiles


def delete_smtp_profile(profile_name: str) -> list[SmtpProfile]:
    profiles = [p for p in load_smtp_profiles() if p.profile_name != profile_name]
    save_smtp_profiles(profiles)
    return profiles


def get_default_smtp_profile() -> SmtpProfile | None:
    profiles = load_smtp_profiles()
    for p in profiles:
        if p.is_default:
            return p
    return profiles[0] if profiles else None


def get_smtp_profile(profile_name: str) -> SmtpProfile | None:
    for p in load_smtp_profiles():
        if p.profile_name == profile_name:
            return p
    return None