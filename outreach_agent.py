"""
outreach_agent.py — DEUS 3.0
==============================
Sends the first outreach message to leads via email or other channels.

Integrates with the deliverability infrastructure:
  - email_sender: Unified SMTP/Gmail API/Resend fallback
  - email_verifier: Pre-send email verification
  - spam_checker: Content anti-spam scoring
  - send_limiter: Rate limiting and daily caps
  - outreach_config: AI email toggle and style settings

Two modes:
  - preview: Returns leads ready for outreach, asks user to confirm
  - send:    Sends emails to the confirmed lead IDs only

Reads:  leads (database), outreach_style_config.json
Writes: leads (database), email_log (database)
"""

import os
import json
import logging
import time

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health
from reply_detector import EmailTracker

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from outreach_config import (
    load_style_config, render_custom_template,
    get_default_smtp_profile, get_smtp_profile, SmtpProfile,
)

OUTREACH_LOG_FILE = "outreach_log.json"


class OutreachAgent(BaseAgent):
    name = "OutreachAgent"
    display_name = "Outreach"
    description = "Sends cold outreach messages to leads via email or social channels."
    requires_keys = ["GROQ_API_KEY"]

    def __init__(self, smtp_profile_name: str = None):
        self.client = None
        self.style = load_style_config()

        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=groq_key)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)

        # Initialize deliverability modules
        self._email_sender = None
        self._email_verifier = None
        self._spam_checker = None
        self._send_limiter = None

        profile = None
        if smtp_profile_name:
            profile = get_smtp_profile(smtp_profile_name)
            if profile is None:
                logger.warning("SMTP profile '%s' not found — falling back to default.", smtp_profile_name)

        if profile is None:
            profile = get_default_smtp_profile()

        if profile is None and os.getenv("SMTP_EMAIL") and os.getenv("SMTP_PASSWORD"):
            profile = SmtpProfile(
                profile_name="env_default",
                smtp_email=os.getenv("SMTP_EMAIL"),
                smtp_password=os.getenv("SMTP_PASSWORD"),
            )

        self.smtp_profile = profile
        self.tracker = EmailTracker()

    def _get_email_sender(self):
        """Lazy-load email sender."""
        if self._email_sender is None:
            from email_sender import get_email_sender
            self._email_sender = get_email_sender(smtp_profile=self.smtp_profile)
        return self._email_sender

    def _get_email_verifier(self):
        """Lazy-load email verifier."""
        if self._email_verifier is None:
            from email_verifier import EmailVerifier
            self._email_verifier = EmailVerifier(check_smtp=False)
        return self._email_verifier

    def _get_spam_checker(self):
        """Lazy-load spam checker."""
        if self._spam_checker is None:
            from spam_checker import SpamChecker
            self._spam_checker = SpamChecker()
        return self._spam_checker

    def _get_send_limiter(self):
        """Lazy-load send limiter."""
        if self._send_limiter is None:
            from send_limiter import get_send_limiter
            self._send_limiter = get_send_limiter()
        return self._send_limiter

    def think(self, prompt: str) -> str:
        if not self.client:
            return ""
        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Groq request failed: %s", e)
            return ""

    def generate_outreach_message(self, lead: dict) -> (str, bool):
        """Returns (message_body, is_html) tuple."""
        # If AI emails disabled or custom template set, use template
        if not self.style.ai_email_enabled or (self.style.use_custom_template and self.style.custom_template):
            if self.style.use_custom_template and self.style.custom_template:
                is_html = self.style.use_html_template and bool(self.style.custom_template_html)
                template = self.style.custom_template_html if is_html else self.style.custom_template
                body = render_custom_template(template, lead, self.style.signature, is_html=is_html)
                return body, is_html
            return self._fallback_template(lead), False

        niche = lead.get("niche", "their industry")
        business_name = lead.get("business_name", "there")
        services = lead.get("services_offered", "")

        task = (
            f"Write a short, {self.style.tone} cold outreach email to "
            f"'{business_name}', a business in the {niche} niche "
            f"(services: {services or 'unspecified'}). "
            f"The goal is to introduce a business proposal and request a "
            f"brief appointment/call to discuss it — specifically: "
            f"\"{self.style.call_to_action}\" "
            f"No word limit. "
            f"Sign off generically without inventing a sender name. "
            f"Do not include a subject line, just the email body."
        )
        message = self.think(task)
        if not message:
            message, _ = self._fallback_template(lead)

        if self.style.signature:
            message = f"{message}\n\n{self.style.signature}"

        return message, False

    def _fallback_template(self, lead: dict) -> (str, bool):
        """Generate a fallback template when AI is disabled or fails."""
        business_name = lead.get("business_name", "there")
        niche = lead.get("niche", "your industry")
        message = (
            f"Hi {business_name} team,\n\n"
            f"I'd like to share a quick business proposal relevant to your "
            f"{niche} work. {self.style.call_to_action}\n\n"
            f"Best regards"
        )
        if self.style.signature:
            message = f"{message}\n\n{self.style.signature}"
        return message, False

    def render_subject(self, lead: dict) -> str:
        try:
            return self.style.subject_template.format(
                business_name=lead.get("business_name", "your business"),
                niche=lead.get("niche", ""),
            )
        except (KeyError, IndexError):
            return f"Quick proposal for {lead.get('business_name', 'your business')}"

    def send_email(self, lead: dict, message: str, is_html: bool = False) -> bool:
        """Send email via the unified email sender with all checks."""
        to_email = lead.get("business_email", "")
        if not to_email:
            return False

        subject = self.render_subject(lead)
        limiter = self._get_send_limiter()

        # Rate limit check
        profile_name = self.smtp_profile.profile_name if self.smtp_profile else "default"
        check = limiter.can_send(profile_name)
        if not check["allowed"]:
            logger.warning("Rate limit: %s", check["reason"])
            return False

        # Spam check
        spam_checker = self._get_spam_checker()
        spam_result = spam_checker.check_before_send(subject, message)
        if not spam_result["should_send"]:
            logger.warning("Spam check blocked email to %s (score=%d): %s",
                          to_email, spam_result["score"], spam_result["issues"])
            return False

        # Send via unified sender
        sender = self._get_email_sender()
        result = sender.send(
            to=to_email,
            subject=subject,
            body=message,
            html=is_html,
            lead_name=lead.get("business_name", ""),
        )

        if result["success"]:
            limiter.record_send(profile_name)
            logger.info("Email sent to %s via %s", to_email, result["method"])
            # Track delivery analytics
            try:
                from app.database import db_conn
                with db_conn() as conn:
                    conn.execute(
                        """INSERT INTO analytics_delivery (email_log_id, inbox_status, domain)
                           VALUES (?, 'unknown', ?)""",
                        (0, to_email.split("@")[-1] if "@" in to_email else ""),
                    )
            except Exception:
                pass
            return True

        logger.warning("Email failed to %s: %s", to_email, result["message"])
        return False

    def send_linkedin_message(self, lead: dict, message: str) -> bool:
        return False

    def send_instagram_message(self, lead: dict, message: str) -> bool:
        return False

    def send_facebook_message(self, lead: dict, message: str) -> bool:
        return False

    def send_phone_sms(self, lead: dict, message: str) -> bool:
        return False

    CHANNEL_REQUIREMENTS = {
        "email": "business_email",
        "linkedin": "linkedin_url",
        "instagram": "instagram_handle",
        "facebook": "facebook_url",
        "phone/sms": "phone",
    }

    AVAILABLE_CHANNELS = ["email", "linkedin", "instagram", "facebook", "phone/sms"]

    def _senders(self) -> dict:
        return {
            "email": self.send_email,
            "linkedin": self.send_linkedin_message,
            "instagram": self.send_instagram_message,
            "facebook": self.send_facebook_message,
            "phone/sms": self.send_phone_sms,
        }

    def resolve_channel(self, lead: dict, requested_channel: str) -> str | None:
        if requested_channel == "auto":
            for channel in self.AVAILABLE_CHANNELS:
                required_field = self.CHANNEL_REQUIREMENTS[channel]
                if lead.get(required_field):
                    return channel
            return None

        required_field = self.CHANNEL_REQUIREMENTS.get(requested_channel)
        if required_field is None:
            return None
        if not lead.get(required_field):
            return None
        return requested_channel

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        mode = kwargs.get("mode", "preview")
        channel = kwargs.get("channel", "auto")
        limit = int(kwargs.get("limit", 25))
        lead_type_filter = kwargs.get("lead_type", None)  # 'scraped', 'imported', or None for all

        if channel not in ("auto", *self.AVAILABLE_CHANNELS):
            channel = "auto"

        if mode == "send":
            return self._send_confirmed(kwargs, start)

        if mode == "auto_scout":
            return self._send_auto_scout(kwargs, start)

        return self._preview(kwargs, start, lead_type_filter=lead_type_filter)

    def _preview(self, kwargs: dict, start: float, lead_type_filter: str = None) -> AgentResult:
        """Preview leads ready for outreach. Returns list for user to confirm."""
        limit = int(kwargs.get("limit", 25))
        channel = kwargs.get("channel", "auto")

        try:
            from app.database import get_outreach_candidates
            candidates = get_outreach_candidates(limit=limit)
            # Filter by lead_type if specified
            if lead_type_filter:
                candidates = [c for c in candidates if c.get("lead_type") == lead_type_filter]
        except Exception:
            candidates = self._get_candidates_from_json(limit)

        if not candidates:
            return make_result(True, "No leads ready for outreach. Import leads with email addresses first.",
                              stats={"candidates": 0}, duration=time.time() - start)

        preview_data = []
        for lead in candidates:
            resolved = self.resolve_channel(lead, lead.get("preferred_channel") or channel)

            deliverability = {}
            if resolved == "email" and lead.get("business_email"):
                verifier = self._get_email_verifier()
                verify_result = verifier.verify(lead["business_email"])
                deliverability = {
                    "email_valid": verify_result["valid"],
                    "email_score": verify_result["score"],
                    "email_warnings": verify_result["warnings"],
                }

            preview_data.append({
                "id": lead.get("id"),
                "business_name": lead.get("business_name", "Unknown"),
                "business_email": lead.get("business_email", ""),
                "phone": lead.get("phone", ""),
                "niche": lead.get("niche", ""),
                "channel": resolved or "none",
                "has_email": bool(lead.get("business_email")),
                "lead_type": lead.get("lead_type", "unknown"),
                **deliverability,
            })

        limiter = self._get_send_limiter()
        rate_status = limiter.get_status(self.smtp_profile.profile_name if self.smtp_profile else "default")

        summary = f"Found {len(preview_data)} leads ready for outreach. Review and confirm to send."
        return make_result(True, summary, data=preview_data,
                          stats={"candidates": len(preview_data), "rate_status": rate_status},
                          duration=time.time() - start)

    def _send_confirmed(self, kwargs: dict, start: float) -> AgentResult:
        """Send emails to confirmed lead IDs only."""
        lead_ids = kwargs.get("lead_ids", [])
        channel = kwargs.get("channel", "email")

        if not lead_ids:
            return make_result(False, "No lead IDs provided. Use preview first, then send with lead_ids.",
                              stats={}, duration=time.time() - start)

        try:
            from app.database import get_lead, mark_leads_contacted
            leads = [get_lead(lid) for lid in lead_ids]
            leads = [l for l in leads if l]
        except Exception:
            leads = self._get_leads_by_ids(lead_ids)

        if not leads:
            return make_result(False, "No valid leads found for the given IDs.",
                              stats={}, duration=time.time() - start)

        sent_count = 0
        failed_count = 0
        skipped_count = 0
        log_entries = []

        limiter = self._get_send_limiter()

        for lead in leads:
            resolved = self.resolve_channel(lead, channel)
            if resolved != "email":
                failed_count += 1
                continue

            # Rate limit check
            profile_name = self.smtp_profile.profile_name if self.smtp_profile else "default"
            check = limiter.can_send(profile_name)
            if not check["allowed"]:
                logger.info("Rate limit reached, stopping send batch: %s", check["reason"])
                skipped_count += len(leads) - (sent_count + failed_count)
                break

            message, is_html = self.generate_outreach_message(lead)
            sent = self.send_email(lead, message, is_html=is_html)
            if sent:
                sent_count += 1
                log_entries.append({
                    "lead_id": lead.get("id"),
                    "business_name": lead.get("business_name"),
                    "business_email": lead.get("business_email"),
                    "channel": resolved,
                    "sent": True,
                    "message": message[:200],
                    "smtp_profile": self.smtp_profile.profile_name if self.smtp_profile else None,
                    "lead_type": lead.get("lead_type", "unknown"),
                })
            else:
                failed_count += 1

        if log_entries:
            try:
                from app.database import mark_leads_contacted, log_email
                ids = [e["lead_id"] for e in log_entries if e.get("lead_id")]
                mark_leads_contacted(ids, channel)
                for e in log_entries:
                    if e.get("business_email"):
                        log_email(
                            lead_email=e["business_email"],
                            lead_name=e.get("business_name", ""),
                            subject="Outreach",
                            status="sent",
                            agent="OutreachAgent",
                        )
            except Exception:
                pass

            self._append_outreach_log(log_entries)

        summary = f"Sent: {sent_count} | Failed: {failed_count} | Skipped (rate limit): {skipped_count} | Total: {len(lead_ids)}"
        stats = {
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total": len(lead_ids),
            "rate_status": limiter.get_status(profile_name),
        }
        return make_result(True, summary, stats=stats, duration=time.time() - start)

    def _send_auto_scout(self, kwargs: dict, start: float) -> AgentResult:
        """Auto-send to scout-found leads only (lead_type='scraped'). Skips imported leads."""
        limit = int(kwargs.get("limit", 25))
        channel = kwargs.get("channel", "email")

        try:
            from app.database import db_conn
            with db_conn() as conn:
                rows = conn.execute(
                    """SELECT * FROM leads
                       WHERE lead_type = 'scraped'
                       AND business_email IS NOT NULL AND business_email != ''
                       AND status != 'contacted'
                       ORDER BY score DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
                scout_leads = [dict(r) for r in rows]
        except Exception:
            scout_leads = []

        if not scout_leads:
            return make_result(True, "No scout leads ready for auto-outreach.",
                              stats={"auto_sent": 0}, duration=time.time() - start)

        sent_count = 0
        failed_count = 0
        limiter = self._get_send_limiter()

        for lead in scout_leads:
            resolved = self.resolve_channel(lead, channel)
            if resolved != "email":
                failed_count += 1
                continue

            profile_name = self.smtp_profile.profile_name if self.smtp_profile else "default"
            check = limiter.can_send(profile_name)
            if not check["allowed"]:
                logger.info("Rate limit hit, stopping auto-scout: %s", check["reason"])
                break

            message, is_html = self.generate_outreach_message(lead)
            sent = self.send_email(lead, message, is_html=is_html)
            if sent:
                sent_count += 1
                try:
                    from app.database import mark_leads_contacted
                    mark_leads_contacted([lead.get("id")], channel)
                except Exception:
                    pass

        summary = f"Auto-scout sent: {sent_count} | Failed: {failed_count} | Scout leads processed: {len(scout_leads)}"
        return make_result(True, summary,
                          stats={"auto_sent": sent_count, "failed": failed_count, "total_scout": len(scout_leads)},
                          duration=time.time() - start)

    def _get_candidates_from_json(self, limit: int) -> list:
        """Fallback: read from leads.json if database unavailable."""
        leads = self._load_leads()
        candidates = []
        for lead in leads:
            if lead.get("status") == "contacted":
                continue
            if not lead.get("business_email"):
                continue
            if len(candidates) >= limit:
                break
            candidates.append(lead)
        return candidates

    def _get_leads_by_ids(self, ids: list) -> list:
        """Fallback: find leads in leads.json by business_email match."""
        leads = self._load_leads()
        return [l for l in leads if l.get("business_email") and id(l) in ids][:len(ids)]

    def _load_leads(self) -> list:
        try:
            with open("leads.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _append_outreach_log(self, entries: list) -> None:
        if not entries:
            return
        try:
            existing = []
            try:
                with open(OUTREACH_LOG_FILE, "r") as f:
                    existing = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                existing = []
            existing.extend(entries)
            with open(OUTREACH_LOG_FILE, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.error("Failed to write %s: %s", OUTREACH_LOG_FILE, e)

    def report(self) -> str:
        profile_name = self.smtp_profile.profile_name if self.smtp_profile else "none configured"
        limiter = self._get_send_limiter()
        status = limiter.get_status(self.smtp_profile.profile_name if self.smtp_profile else "default")
        html_mode = "HTML" if self.style.use_html_template else "Text"
        return (f"Outreach Agent (SMTP: {profile_name}). "
                f"Today: {status['daily_sent']}/{status['daily_limit']} sent | "
                f"AI emails: {'ON' if self.style.ai_email_enabled else 'OFF'} | "
                f"Mode: {html_mode} | "
                f"Auto-scout: {'ON' if self.style.ai_email_enabled else 'OFF'}")

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        sender = self._get_email_sender()
        health = sender.check_health()
        email_ok = health.get("any_available", False)

        if groq_ok and email_ok:
            return make_health(True, "ready",
                             f"Outreach agent ready. Email: {', '.join(k for k,v in health.items() if k != 'any_available' and isinstance(v, dict) and v.get('available'))}",
                             {**keys, **{k: v.get("available", False) for k, v in health.items() if isinstance(v, dict)}})
        issues = []
        if not groq_ok:
            issues.append("GROQ_API_KEY not set")
        if not email_ok:
            issues.append("No email provider available (SMTP/Gmail API/Resend)")
        return make_health(True, "degraded", f"Degraded: {', '.join(issues)}", keys)


if __name__ == "__main__":
    agent = OutreachAgent()
    print(agent.report())
    result = agent.run(channel="auto")
    print(result)
