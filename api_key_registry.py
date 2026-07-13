"""
api_key_registry.py
--------------------
Single source of truth for every API key / credential used across the DEUS
agents, which agent(s) need it, and where to get one. Used by the GUI's
API Keys panel to display and edit them all in one place, instead of the
old behavior of silently checking just 3 hardcoded keys.
"""

from dataclasses import dataclass


@dataclass
class KeyInfo:
    env_var: str
    provider: str          # human-readable provider name
    provider_url: str      # where to get/manage the key
    used_by: list           # agent display names that depend on this key
    required: bool = True   # if False, the agent works without it but with reduced features
    is_secret: bool = True  # if False, shown in plain text instead of masked (e.g. an email address, a True/False flag)


API_KEY_REGISTRY = [
    KeyInfo(
        env_var="GROQ_API_KEY",
        provider="Groq",
        provider_url="https://console.groq.com/keys",
        used_by=[
            "Lead Scout", "Outreach", "Followup", "Appointment",
            "Deal Closer", "Report", "DEUS Bridge",
        ],
        required=True,
    ),
    KeyInfo(
        env_var="GEMINI_API_KEY",
        provider="Google AI Studio (Gemini)",
        provider_url="https://aistudio.google.com/apikey",
        used_by=["Appointment", "DEUS Bridge"],
        required=True,
    ),
    KeyInfo(
        env_var="CALENDLY_API_KEY",
        provider="Calendly",
        provider_url="https://calendly.com/integrations/api_webhooks",
        used_by=["Appointment"],
        required=True,
    ),
    KeyInfo(
        env_var="SERPER_API_KEY",
        provider="Serper.dev",
        provider_url="https://serper.dev/api-key",
        used_by=["Lead Scout (optional business search backend)"],
        required=False,
    ),
    KeyInfo(
        env_var="SMTP_EMAIL",
        provider="Your email account (e.g. Gmail)",
        provider_url="https://myaccount.google.com/apppasswords",
        used_by=["Outreach", "Followup", "Report"],
        required=False,  # Outreach can also use smtp_profiles.json instead
        is_secret=False,
    ),
    KeyInfo(
        env_var="SMTP_PASSWORD",
        provider="Your email account (app password)",
        provider_url="https://myaccount.google.com/apppasswords",
        used_by=["Outreach", "Followup", "Report"],
        required=False,
    ),
    KeyInfo(
        env_var="REPORT_RECEIVER_EMAIL",
        provider="(your own address — not a 3rd-party key)",
        provider_url="",
        used_by=["Report"],
        required=False,
        is_secret=False,
    ),
    KeyInfo(
        env_var="REPORT_EMAIL_ENABLED",
        provider="(local setting — True/False, not a key)",
        provider_url="",
        used_by=["Report"],
        required=False,
        is_secret=False,
    ),
]


def get_agents_grouped() -> dict:
    """
    Returns {agent_name: [KeyInfo, ...]} so the GUI can render:
        Agent Name
          KEY_1
          KEY_2
        Agent Name
          KEY_1
        ...
    A key used by multiple agents appears once under each agent it serves.
    """
    grouped: dict[str, list] = {}
    for key_info in API_KEY_REGISTRY:
        for agent_name in key_info.used_by:
            grouped.setdefault(agent_name, [])
            if key_info not in grouped[agent_name]:
                grouped[agent_name].append(key_info)
    return grouped


def mask_value(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]