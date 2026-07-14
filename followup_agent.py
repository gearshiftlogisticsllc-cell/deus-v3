"""
followup_agent.py — DEUS 3.0
==============================
Sends follow-up messages to leads that were contacted but haven't responded
within a cooldown window, up to a max number of attempts, then marks them
as no_response.

Campaign-aware: skips leads that are enrolled in an active campaign
(those are handled by daemon.py + campaign.py).

Integrates with deliverability infrastructure:
  - email_sender: Unified sending
  - spam_checker: Content anti-spam scoring
  - send_limiter: Rate limiting

Reads/Writes: leads (database), email_log
"""

import os
import json
import time
import logging
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health
from reply_detector import EmailTracker, scan_for_replies

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FOLLOWUP_COOLDOWN_SECONDS = 48 * 60 * 60
MAX_FOLLOWUPS = 3


class FollowupAgent(BaseAgent):
    name = "FollowupAgent"
    display_name = "Followup"
    description = "Re-engages contacted leads after 48h cooldown, max 3 attempts. Skips campaign-enrolled leads."
    requires_keys = ["GROQ_API_KEY"]

    def __init__(self):
        self.client = None
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=groq_key)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)
        self.tracker = EmailTracker()
        self._email_sender = None
        self._spam_checker = None
        self._send_limiter = None

    def _get_email_sender(self):
        if self._email_sender is None:
            from email_sender import get_email_sender
            self._email_sender = get_email_sender()
        return self._email_sender

    def _get_spam_checker(self):
        if self._spam_checker is None:
            from spam_checker import SpamChecker
            self._spam_checker = SpamChecker()
        return self._spam_checker

    def _get_send_limiter(self):
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
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Groq request failed: %s", e)
            return ""

    def run(self, **kwargs) -> AgentResult:
        start = time.time()

        # Step 1: Scan for replies first (if IMAP configured)
        reply_scan = {"replies_found": 0, "leads_marked": 0}
        try:
            reply_scan = scan_for_replies(days_back=7)
        except Exception as e:
            logger.warning("Reply scan failed (continuing anyway): %s", e)

        # Step 2: Get leads from database (not JSON)
        try:
            from app.database import get_leads
            all_leads = get_leads(status="contacted", limit=500)
        except Exception:
            all_leads = self._load_leads_from_json()

        # Step 3: Filter out leads enrolled in active campaigns
        campaign_leads = set()
        try:
            from campaign import get_campaign_manager
            cm = get_campaign_manager()
            due = cm.get_due_enrollments()
            # Campaign daemon handles these
            for d in due:
                campaign_leads.add(d.get("lead_id"))
        except Exception:
            pass

        # Step 4: Find leads that need follow-up (not in campaigns)
        leads_to_followup = []
        for lead in all_leads:
            if lead.get("id") in campaign_leads:
                continue  # Skip — campaign handles this lead
            if self.tracker.should_followup(lead):
                leads_to_followup.append(lead)

        if not leads_to_followup:
            return make_result(True,
                f"No leads due for follow-up. (Replies detected: {reply_scan.get('replies_found', 0)}, "
                f"Campaign-enrolled: {len(campaign_leads)})",
                stats={"followed_up": 0, "rejected": 0,
                       "replies_detected": reply_scan.get("replies_found", 0),
                       "leads_marked_replied": reply_scan.get("leads_marked", 0),
                       "campaign_enrolled": len(campaign_leads)},
                duration=time.time() - start)

        followed_up = 0
        rejected = 0
        limiter = self._get_send_limiter()

        for lead in leads_to_followup:
            if self.has_reached_max_followups(lead):
                self.mark_lead_as_no_response(lead)
                rejected += 1
                continue

            # Rate limit check
            check = limiter.can_send()
            if not check["allowed"]:
                logger.info("Rate limit hit, stopping follow-ups: %s", check["reason"])
                break

            followup_message = self.generate_followup_message(lead)
            sent = self.send_followup_message(lead, followup_message)

            # Update lead in database
            try:
                from app.database import update_lead
                update_lead(lead.get("id", 0), {
                    "contact_count": lead.get("contact_count", 0) + 1,
                    "last_contacted_at": time.time(),
                })
            except Exception:
                pass

            if sent:
                followed_up += 1

        duration = time.time() - start
        summary = (f"Followups sent: {followed_up} | Rejected (max reached): {rejected} | "
                   f"Replies detected: {reply_scan.get('replies_found', 0)} | "
                   f"Leads marked replied: {reply_scan.get('leads_marked', 0)} | "
                   f"Campaign-enrolled (skipped): {len(campaign_leads)}")
        logger.info(summary)
        return make_result(True, summary,
                          stats={"followed_up": followed_up, "rejected": rejected,
                                 "replies_detected": reply_scan.get("replies_found", 0),
                                 "leads_marked_replied": reply_scan.get("leads_marked", 0),
                                 "campaign_enrolled": len(campaign_leads)},
                          duration=duration)

    def _load_leads_from_json(self) -> list:
        """Fallback: load leads from JSON file."""
        try:
            with open("leads.json", "r") as f:
                leads = json.load(f)
            return [l for l in leads if l.get("status") == "contacted" and l.get("business_email")]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def is_due_for_followup(self, lead: dict) -> bool:
        last_contacted = lead.get("last_contacted_at")
        if last_contacted is None:
            return False
        return (time.time() - last_contacted) >= FOLLOWUP_COOLDOWN_SECONDS

    def has_reached_max_followups(self, lead: dict) -> bool:
        return lead.get("contact_count", 0) >= MAX_FOLLOWUPS

    def mark_lead_as_no_response(self, lead: dict) -> None:
        try:
            from app.database import update_lead
            update_lead(lead.get("id", 0), {"status": "no_response"})
        except Exception:
            pass

    def generate_followup_message(self, lead: dict) -> str:
        task = (
            f"Generate a brief, polite follow-up message for "
            f"'{lead.get('business_name', 'the business')}' "
            f"in the {lead.get('niche', 'their')} niche. "
            f"This is follow-up #{lead.get('contact_count', 0) + 1}. "
            f"Keep a professional tone, under 80 words, no invented sender name."
        )
        message = self.think(task)
        return message or (
            f"Hi {lead.get('business_name', 'there')}, just following up on my "
            f"earlier note — happy to share more details whenever convenient."
        )

    def send_followup_message(self, lead: dict, message: str) -> bool:
        if not lead.get("business_email"):
            return False

        # Spam check
        subject = f"Following up — {lead.get('business_name', 'your business')}"
        spam_checker = self._get_spam_checker()
        spam_result = spam_checker.check_before_send(subject, message)
        if not spam_result["should_send"]:
            logger.warning("Follow-up blocked by spam check for %s (score=%d)",
                          lead.get("business_email"), spam_result["score"])
            return False

        # Send via unified sender
        sender = self._get_email_sender()
        result = sender.send(
            to=lead["business_email"],
            subject=subject,
            body=message,
            lead_name=lead.get("business_name", ""),
        )

        if result["success"]:
            self.tracker.record_sent(lead, channel="email", message=message)
            limiter = self._get_send_limiter()
            limiter.record_send()
            return True

        return False

    def report(self) -> str:
        try:
            from app.database import count_leads
            contacted = count_leads(status="contacted")
            no_response = count_leads(status="no_response")
        except Exception:
            contacted = 0
            no_response = 0
        return f"Followup Agent: Contacted (awaiting): {contacted} | No response: {no_response}"

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        sender = self._get_email_sender()
        health = sender.check_health()
        email_ok = health.get("any_available", False)
        if email_ok:
            return make_health(True, "ready", "Followup agent ready.", keys)
        issues = []
        if not groq_ok:
            issues.append("GROQ_API_KEY not set")
        if not email_ok:
            issues.append("No email provider available")
        return make_health(True, "degraded", f"Degraded: {', '.join(issues)}", keys)


if __name__ == "__main__":
    agent = FollowupAgent()
    result = agent.run()
    print(result)
