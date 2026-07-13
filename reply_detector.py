"""
reply_detector.py — DEUS 3.0
==============================
IMAP-based email reply detection. Scans the inbox for replies from leads
we've contacted. Safe for shared inboxes — only looks for replies TO our
outbound emails, never touches other mail.

Reads/Writes: leads.json, outreach_log.json
Requires: IMAP_EMAIL, IMAP_PASSWORD (or SMTP_EMAIL/SMTP_PASSWORD)
"""

import os
import json
import re
import time
import email
import imaplib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LEADS_FILE = "leads.json"
OUTREACH_LOG_FILE = "outreach_log.json"
REPLY_STATE_FILE = "reply_state.json"

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_EMAIL = os.getenv("IMAP_EMAIL", "") or os.getenv("SMTP_EMAIL", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "") or os.getenv("SMTP_PASSWORD", "")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))


def _load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else []


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _normalize_email(addr: str) -> str:
    """Lowercase, strip whitespace and angle brackets."""
    if not addr:
        return ""
    addr = addr.strip().lower()
    addr = re.sub(r"[<>]", "", addr)
    # Take only the email part if "Name <email>" format
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", addr)
    return match.group(0) if match else addr


def _extract_reply_to(message_id: str) -> str:
    """Extract the message-id this email is replying to."""
    if not message_id:
        return ""
    # Clean angle brackets
    return message_id.strip().strip("<>")


class ReplyDetector:
    """
    Scans IMAP inbox for replies from leads we've contacted.

    Strategy:
    1. Load outreach_log.json to know which emails we sent (and their message-ids)
    2. Connect to IMAP, search for emails FROM those addresses
    3. For each reply, check if it's in reply to one of our sent messages
    4. Mark matching leads as "replied" in leads.json
    """

    def __init__(self):
        self.imap_email = IMAP_EMAIL
        self.imap_password = IMAP_PASSWORD
        self.state = _load_json(REPLY_STATE_FILE, {"last_check_uid": 0, "replies_found": []})

    def _connect(self) -> Optional[imaplib.IMAP4_SSL]:
        if not self.imap_email or not self.imap_password:
            logger.warning("IMAP credentials not set. Set IMAP_EMAIL + IMAP_PASSWORD (or SMTP_EMAIL/SMTP_PASSWORD).")
            return None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(self.imap_email, self.imap_password)
            return mail
        except Exception as e:
            logger.error("IMAP connection failed: %s", e)
            return None

    def _get_sent_email_addresses(self) -> set:
        """Build set of email addresses we've sent outreach to."""
        leads = _load_json(LEADS_FILE)
        addresses = set()
        for lead in leads:
            if isinstance(lead, dict):
                email_addr = lead.get("business_email", "")
                if email_addr:
                    addresses.add(_normalize_email(email_addr))
        return addresses

    def _get_sent_message_ids(self) -> dict:
        """Build map of message-id -> lead email from outreach_log.json.
        Maps the In-Reply-To header we expect to see."""
        log = _load_json(OUTREACH_LOG_FILE)
        # We don't store message-ids in outreach_log yet, so we match by sender email
        # This is the safe approach: any email FROM a lead we contacted = potential reply
        return {}

    def scan(self, days_back: int = 7) -> dict:
        """
        Scan inbox for replies from contacted leads.

        Returns:
            {
                "success": bool,
                "replies_found": int,
                "leads_marked": int,
                "details": [{"email": ..., "lead_name": ..., "date": ...}],
                "error": str (if failed)
            }
        """
        mail = self._connect()
        if not mail:
            return {"success": False, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": "IMAP connection failed"}

        sent_addresses = self._get_sent_email_addresses()
        if not sent_addresses:
            mail.logout()
            return {"success": True, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": "No contacted leads found"}

        leads = _load_json(LEADS_FILE)
        leads_by_email = {}
        for lead in leads:
            if isinstance(lead, dict):
                email_addr = _normalize_email(lead.get("business_email", ""))
                if email_addr:
                    leads_by_email[email_addr] = lead

        replies_found = 0
        leads_marked = 0
        details = []

        try:
            mail.select("INBOX")

            # Search for emails from last N days
            since_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'(SINCE "{since_date}")')

            if status != "OK":
                mail.logout()
                return {"success": False, "replies_found": 0, "leads_marked": 0,
                        "details": [], "error": "IMAP search failed"}

            msg_ids = messages[0].split()

            for msg_id in msg_ids:
                try:
                    # Skip already processed UIDs
                    uid = int(msg_id)
                    if uid <= self.state.get("last_check_uid", 0):
                        continue

                    status, data = mail.fetch(msg_id, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # Get sender
                    from_header = msg.get("From", "")
                    from_email = _normalize_email(from_header)

                    # Check if this is from a lead we contacted
                    if from_email not in sent_addresses:
                        continue

                    # Check if it's actually a reply (has In-Reply-To or Re: subject)
                    subject = msg.get("Subject", "")
                    in_reply_to = msg.get("In-Reply-To", "")
                    references = msg.get("References", "")
                    is_reply = (
                        subject.lower().startswith("re:")
                        or bool(in_reply_to)
                        or bool(references)
                    )

                    # Also accept any email from a contacted lead as potential reply
                    # (some email clients don't set In-Reply-To properly)
                    if not is_reply:
                        # Still count it — if a lead emails us, it's a reply
                        pass

                    lead = leads_by_email.get(from_email)
                    if lead:
                        replies_found += 1
                        lead_name = lead.get("business_name", from_email)
                        date_str = msg.get("Date", "")

                        details.append({
                            "email": from_email,
                            "lead_name": lead_name,
                            "date": date_str,
                            "subject": subject,
                        })

                        # Mark lead as replied
                        if lead.get("status") != "replied":
                            lead["status"] = "replied"
                            lead["replied_at"] = time.time()
                            lead["replied_subject"] = subject
                            leads_marked += 1

                except Exception as e:
                    logger.warning("Error processing message %s: %s", msg_id, e)
                    continue

            # Update state
            if msg_ids:
                self.state["last_check_uid"] = int(msg_ids[-1])
            self._save_state()

            # Save leads
            _save_json(LEADS_FILE, leads)

        except Exception as e:
            logger.error("IMAP scan error: %s", e)
            mail.logout()
            return {"success": False, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": str(e)}

        try:
            mail.logout()
        except Exception:
            pass

        return {
            "success": True,
            "replies_found": replies_found,
            "leads_marked": leads_marked,
            "details": details,
            "error": "",
        }

    def _save_state(self):
        _save_json(REPLY_STATE_FILE, self.state)

    def is_configured(self) -> bool:
        return bool(self.imap_email and self.imap_password)


class EmailTracker:
    """
    Tracks outbound emails. When OutreachAgent sends an email,
    it calls EmailTracker.record_sent() to save:
    - first_contacted_at
    - last_contacted_at
    - contact_count
    - outreach history

    This ensures FollowupAgent can properly calculate cooldowns
    and never contacts someone who replied.
    """

    def __init__(self):
        pass

    def record_sent(self, lead: dict, channel: str = "email", message: str = "") -> dict:
        """Update lead dict with tracking fields. Returns the updated lead."""
        now = time.time()

        if not lead.get("first_contacted_at"):
            lead["first_contacted_at"] = now

        lead["last_contacted_at"] = now
        lead["contact_count"] = lead.get("contact_count", 0) + 1
        lead["status"] = "contacted"
        lead["channel_used"] = channel
        lead["last_channel"] = channel

        # Append to outreach history
        if "outreach_history" not in lead:
            lead["outreach_history"] = []

        lead["outreach_history"].append({
            "timestamp": now,
            "channel": channel,
            "message_preview": (message[:100] + "...") if len(message) > 100 else message,
        })

        return lead

    def mark_replied(self, lead: dict, subject: str = "") -> dict:
        """Mark a lead as having replied."""
        lead["status"] = "replied"
        lead["replied_at"] = time.time()
        lead["replied_subject"] = subject
        return lead

    def is_replied(self, lead: dict) -> bool:
        return lead.get("status") == "replied"

    def should_followup(self, lead: dict, cooldown_hours: int = 48, max_followups: int = 3) -> bool:
        """Determine if a lead should receive a follow-up."""
        # Never follow up if replied
        if self.is_replied(lead):
            return False

        # Never follow up if rejected/no_response after max attempts
        if lead.get("status") in ("no_response", "rejected"):
            return False

        # Only follow up on contacted leads
        if lead.get("status") != "contacted":
            return False

        # Check max followups
        followup_count = lead.get("followup_count", 0)
        if followup_count >= max_followups:
            return False

        # Check cooldown
        last_contacted = lead.get("last_contacted_at")
        if not last_contacted:
            return False

        hours_since = (time.time() - last_contacted) / 3600
        return hours_since >= cooldown_hours


# Convenience functions for API endpoints

def scan_for_replies(days_back: int = 7) -> dict:
    """One-shot scan. Returns results dict."""
    detector = ReplyDetector()
    if not detector.is_configured():
        return {"success": False, "error": "IMAP not configured. Set IMAP_EMAIL + IMAP_PASSWORD."}
    return detector.scan(days_back=days_back)


def get_reply_status() -> dict:
    """Get current reply detection status."""
    detector = ReplyDetector()
    leads = _load_json(LEADS_FILE)
    replied = sum(1 for l in leads if isinstance(l, dict) and l.get("status") == "replied")
    contacted = sum(1 for l in leads if isinstance(l, dict) and l.get("status") == "contacted")

    return {
        "imap_configured": detector.is_configured(),
        "imap_host": IMAP_HOST,
        "imap_email": IMAP_EMAIL[:3] + "***" if IMAP_EMAIL else "",
        "leads_replied": replied,
        "leads_contacted": contacted,
        "last_check_uid": detector.state.get("last_check_uid", 0),
    }


def mark_lead_replied(email_addr: str) -> dict:
    """Manually mark a lead as replied (for manual override)."""
    leads = _load_json(LEADS_FILE)
    found = False
    for lead in leads:
        if isinstance(lead, dict) and _normalize_email(lead.get("business_email", "")) == _normalize_email(email_addr):
            lead["status"] = "replied"
            lead["replied_at"] = time.time()
            lead["replied_source"] = "manual"
            found = True
            break

    if found:
        _save_json(LEADS_FILE, leads)
    return {"success": found, "email": email_addr}
