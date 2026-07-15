"""
reply_detector.py — DEUS 3.0
=============================
Gmail-API-based email reply detection. Scans the inbox for replies from
leads we've contacted. Uses the same OAuth token as gmail_sender.py, so
it works with 2FA and doesn't need App Passwords.

Also detects unsubscribe/stop keywords in replies and marks leads as
unsubscribed in the database.

Reads/Writes: leads (database via app.database)
Requires: Gmail API OAuth token (configured via /api/gmail/auth-url)
"""

import os
import json
import re
import time
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

REPLY_STATE_FILE = "reply_state.json"

UNSUBSCRIBE_KEYWORDS = [
    "stop", "unsubscribe", "not interested", "remove",
    "do not contact", "don't contact", "leave me alone",
    "spam", "block", "opt out", "opt-out",
]


def _normalize_email(addr: str) -> str:
    """Lowercase, strip whitespace and angle brackets."""
    if not addr:
        return ""
    addr = addr.strip().lower()
    addr = re.sub(r"[<>]", "", addr)
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", addr)
    return match.group(0) if match else addr


def _decode_body(payload: dict) -> str:
    """Extract plain text from a Gmail API message payload."""
    if not payload:
        return ""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return ""
    if "parts" in payload:
        texts = []
        for part in payload["parts"]:
            texts.append(_decode_body(part))
        return "\n".join(t for t in texts if t)
    return ""


def _parse_header(payload: dict, name: str) -> str:
    """Extract a specific header value from a Gmail API message payload headers."""
    headers = payload.get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _check_unsubscribe(text: str) -> bool:
    """Check if the message body contains unsubscribe/stop keywords."""
    if not text:
        return False
    text_lower = text.lower()
    for keyword in UNSUBSCRIBE_KEYWORDS:
        if keyword in text_lower:
            return True
    return False


class ReplyDetector:
    """
    Scans Gmail inbox for replies from leads we've contacted.

    Strategy:
    1. Query contacted leads from database
    2. Search Gmail inbox for recent messages FROM those addresses
    3. Check for reply indicators (Re: subject, or any from-lead message)
    4. Check for unsubscribe keywords in body
    5. Mark matching leads as "replied" or "unsubscribed" in database
    """

    def __init__(self):
        self._service = None
        self._init_service()
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            with open(REPLY_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"last_check_id": "", "replies_found": []}

    def _save_state(self):
        try:
            with open(REPLY_STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save reply state: %s", e)

    def _init_service(self):
        """Initialize Gmail API service with OAuth2 credentials from DB."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from app.database import get_gmail_token

            SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

            token_json = get_gmail_token()
            if not token_json:
                logger.info("No Gmail token in database — reply detector disabled")
                return

            creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("Gmail token refreshed for reply detector")
                except Exception as e:
                    logger.warning("Token refresh failed for reply detector: %s", e)
                    return

            self._service = build("gmail", "v1", credentials=creds)
            logger.info("Gmail API initialized for reply detection")

        except ImportError:
            logger.info("Gmail API packages not installed — reply detector disabled")
        except Exception as e:
            logger.warning("Gmail API init failed for reply detector: %s", e)

    def _get_contacted_emails(self) -> dict:
        """Build dict of {email: business_name} from database."""
        result = {}
        try:
            from app.database import db_conn
            with db_conn() as conn:
                rows = conn.execute(
                    """SELECT business_email, business_name, owner_name, id
                       FROM leads
                       WHERE business_email IS NOT NULL AND business_email != ''
                       AND status IN ('contacted', 'replied')"""
                ).fetchall()
                for row in rows:
                    email = _normalize_email(row["business_email"])
                    if email:
                        result[email] = {
                            "business_name": row["business_name"] or "",
                            "owner_name": row["owner_name"] or "",
                            "id": row["id"],
                        }
        except Exception as e:
            logger.warning("Failed to load contacted leads from DB: %s", e)
        return result

    def scan(self, days_back: int = 7) -> dict:
        """
        Scan Gmail inbox for replies from contacted leads.

        Returns:
            {
                "success": bool,
                "replies_found": int,
                "leads_marked": int,
                "details": [{"email": ..., "lead_name": ..., "date": ..., "subject": ...}],
                "error": str (if failed)
            }
        """
        if not self._service:
            return {"success": False, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": "Gmail API not configured. Authorize via /api/gmail/auth-url first."}

        contacted = self._get_contacted_emails()
        if not contacted:
            return {"success": True, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": "No contacted leads found in database"}

        replies_found = 0
        leads_marked = 0
        unsubscribed_marked = 0
        details = []
        last_seen_id = self.state.get("last_check_id", "")

        try:
            since_epoch = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
            query = f"is:inbox after:{since_epoch}"

            response = self._service.users().messages().list(
                userId="me",
                q=query,
                maxResults=200,
            ).execute()

            messages = response.get("messages", [])
            if not messages:
                return {"success": True, "replies_found": 0, "leads_marked": 0,
                        "details": [], "error": ""}

            for msg in messages:
                msg_id = msg["id"]

                # Skip already processed messages
                if last_seen_id and msg_id <= last_seen_id:
                    continue

                try:
                    msg_data = self._service.users().messages().get(
                        userId="me",
                        id=msg_id,
                        format="full",
                    ).execute()

                    payload = msg_data.get("payload", {})
                    headers = payload.get("headers", [])

                    from_header = _parse_header(payload, "From")
                    from_email = _normalize_email(from_header)
                    if not from_email or from_email not in contacted:
                        continue

                    subject = _parse_header(payload, "Subject")
                    date_str = _parse_header(payload, "Date")
                    in_reply_to = _parse_header(payload, "In-Reply-To")
                    references = _parse_header(payload, "References")

                    # Decode body for unsubscribe check
                    body_text = _decode_body(payload)
                    snippet = msg_data.get("snippet", "")

                    is_reply = bool(
                        subject.lower().startswith("re:")
                        or in_reply_to
                        or references
                    )

                    lead_info = contacted[from_email]
                    business_name = lead_info.get("business_name", from_email)

                    details.append({
                        "email": from_email,
                        "lead_name": business_name,
                        "date": date_str,
                        "subject": subject,
                        "msg_id": msg_id,
                    })
                    replies_found += 1

                    # Check for unsubscribe keywords
                    full_text = f"{subject} {snippet} {body_text}"
                    if _check_unsubscribe(full_text):
                        try:
                            from app.database import mark_lead_unsubscribed
                            if mark_lead_unsubscribed(from_email):
                                unsubscribed_marked += 1
                                logger.info("Unsubscribed %s due to keyword in reply", from_email)
                        except Exception as e:
                            logger.warning("Failed to mark %s unsubscribed: %s", from_email, e)
                    else:
                        # Mark as replied in database
                        try:
                            from app.database import db_conn
                            with db_conn() as conn:
                                conn.execute(
                                    """UPDATE leads
                                       SET status = 'replied',
                                           replied_at = ?,
                                           replied_subject = ?
                                       WHERE business_email = ? AND status != 'replied' AND status != 'unsubscribed'""",
                                    (time.time(), subject, from_email),
                                )
                                if conn.total_changes > 0:
                                    leads_marked += 1
                        except Exception as e:
                            logger.warning("Failed to mark %s replied: %s", from_email, e)

                    # Mark as read so we don't re-scan
                    try:
                        self._service.users().messages().modify(
                            userId="me",
                            id=msg_id,
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute()
                    except Exception:
                        pass

                except Exception as e:
                    logger.warning("Error processing message %s: %s", msg_id, e)
                    continue

            # Update state
            if messages:
                self.state["last_check_id"] = messages[-1]["id"]
            self._save_state()

        except Exception as e:
            logger.error("Gmail API scan error: %s", e)
            return {"success": False, "replies_found": 0, "leads_marked": 0,
                    "details": [], "error": str(e)}

        return {
            "success": True,
            "replies_found": replies_found,
            "leads_marked": leads_marked,
            "unsubscribed_marked": unsubscribed_marked,
            "details": details,
            "error": "",
        }

    def is_configured(self) -> bool:
        """Check if Gmail API is available for reply detection."""
        return self._service is not None


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

    Operates on lead dicts (not database) — stateless.
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
        if self.is_replied(lead):
            return False
        if lead.get("status") in ("no_response", "rejected", "unsubscribed", "blocked"):
            return False
        if lead.get("status") != "contacted":
            return False

        followup_count = lead.get("followup_count", 0)
        if followup_count >= max_followups:
            return False

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
        return {"success": False, "replies_found": 0, "leads_marked": 0,
                "details": [], "error": "Gmail API not configured. Authorize via /api/gmail/auth-url first."}
    return detector.scan(days_back=days_back)


def get_reply_status() -> dict:
    """Get current reply detection status."""
    detector = ReplyDetector()
    try:
        from app.database import db_conn
        with db_conn() as conn:
            replied = conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE status = 'replied'"
            ).fetchone()["c"]
            contacted = conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE status = 'contacted'"
            ).fetchone()["c"]
            unsubscribed = conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE status = 'unsubscribed'"
            ).fetchone()["c"]
    except Exception:
        replied = 0
        contacted = 0
        unsubscribed = 0

    return {
        "gmail_configured": detector.is_configured(),
        "leads_replied": replied,
        "leads_contacted": contacted,
        "leads_unsubscribed": unsubscribed,
        "last_check_id": detector.state.get("last_check_id", ""),
    }


def mark_lead_replied(email_addr: str) -> dict:
    """Manually mark a lead as replied (for manual override)."""
    try:
        from app.database import db_conn
        with db_conn() as conn:
            cursor = conn.execute(
                "UPDATE leads SET status = 'replied', replied_at = ?, replied_source = 'manual' WHERE business_email = ?",
                (time.time(), email_addr),
            )
            found = cursor.rowcount > 0
    except Exception:
        found = False
    return {"success": found, "email": email_addr}
