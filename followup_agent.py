"""
followup_agent.py — DEUS 3.0
==============================
Sends follow-up messages to leads that were contacted but haven't responded
within a cooldown window, up to a max number of attempts, then marks them
as no_response.

Reads/Writes: leads.json
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

LEADS_FILE = "leads.json"
FOLLOWUP_COOLDOWN_SECONDS = 48 * 60 * 60
MAX_FOLLOWUPS = 3


class FollowupAgent(BaseAgent):
    name = "FollowupAgent"
    display_name = "Followup"
    description = "Re-engages contacted leads after 48h cooldown, max 3 attempts."
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
        self.leads = self._load_leads()
        self.tracker = EmailTracker()

    def _load_leads(self) -> list:
        try:
            with open(LEADS_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_leads(self) -> None:
        try:
            with open(LEADS_FILE, "w") as f:
                json.dump(self.leads, f, indent=2)
        except Exception as e:
            logger.error("Failed to save %s: %s", LEADS_FILE, e)

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
            if reply_scan.get("replies_found", 0) > 0:
                # Reload leads after reply scan updated them
                self.leads = self._load_leads()
        except Exception as e:
            logger.warning("Reply scan failed (continuing anyway): %s", e)

        # Step 2: Find leads that need follow-up
        leads_to_followup = self.find_leads_to_followup()
        if not leads_to_followup:
            return make_result(True,
                f"No leads due for follow-up. (Replies detected: {reply_scan.get('replies_found', 0)})",
                stats={"followed_up": 0, "rejected": 0,
                       "replies_detected": reply_scan.get("replies_found", 0),
                       "leads_marked_replied": reply_scan.get("leads_marked", 0)},
                duration=time.time() - start)

        followed_up = 0
        rejected = 0

        for lead in leads_to_followup:
            if self.has_reached_max_followups(lead):
                self.mark_lead_as_rejected(lead)
                rejected += 1
                continue

            followup_message = self.generate_followup_message(lead)
            sent = self.send_followup_message(lead, followup_message)

            lead["followup_count"] = lead.get("followup_count", 0) + 1
            lead["last_contacted_at"] = time.time()
            if sent:
                followed_up += 1

        self._save_leads()
        duration = time.time() - start
        summary = (f"Followups sent: {followed_up} | Rejected (max reached): {rejected} | "
                   f"Replies detected: {reply_scan.get('replies_found', 0)} | "
                   f"Leads marked replied: {reply_scan.get('leads_marked', 0)}")
        logger.info(summary)
        return make_result(True, summary,
                          stats={"followed_up": followed_up, "rejected": rejected,
                                 "replies_detected": reply_scan.get("replies_found", 0),
                                 "leads_marked_replied": reply_scan.get("leads_marked", 0)},
                          duration=duration)

    def find_leads_to_followup(self) -> list:
        return [
            lead for lead in self.leads
            if self.tracker.should_followup(lead)
        ]

    def is_due_for_followup(self, lead: dict) -> bool:
        last_contacted = lead.get("last_contacted_at")
        if last_contacted is None:
            return False
        return (time.time() - last_contacted) >= FOLLOWUP_COOLDOWN_SECONDS

    def has_reached_max_followups(self, lead: dict) -> bool:
        return lead.get("followup_count", 0) >= MAX_FOLLOWUPS

    def mark_lead_as_rejected(self, lead: dict) -> None:
        lead["status"] = "no_response"

    def generate_followup_message(self, lead: dict) -> str:
        task = (
            f"Generate a brief, polite follow-up message for "
            f"'{lead.get('business_name', 'the business')}' "
            f"in the {lead.get('niche', 'their')} niche. "
            f"This is follow-up #{lead.get('followup_count', 0) + 1}. "
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
        try:
            from outreach_agent import OutreachAgent
            sender = OutreachAgent()
            sent = sender.send_email(lead, message)
            if sent:
                self.tracker.record_sent(lead, channel="email", message=message)
            return sent
        except ImportError:
            logger.warning("outreach_agent.py not found — cannot send followup.")
            return False

    def report(self) -> str:
        contacted = sum(1 for l in self.leads if l.get("status") == "contacted")
        no_response = sum(1 for l in self.leads if l.get("status") == "no_response")
        return f"Followup Agent: Contacted (awaiting): {contacted} | No response: {no_response}"

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        leads_ok = os.path.exists(LEADS_FILE)
        if groq_ok and leads_ok:
            return make_health(True, "ready", "Followup agent ready.", keys)
        issues = []
        if not groq_ok:
            issues.append("GROQ_API_KEY not set")
        if not leads_ok:
            issues.append("leads.json not found")
        return make_health(True, "degraded", f"Degraded: {', '.join(issues)}", keys)


if __name__ == "__main__":
    agent = FollowupAgent()
    result = agent.run()
    print(result)
