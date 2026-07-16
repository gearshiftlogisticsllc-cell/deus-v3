"""
daemon.py — DEUS 3.0
======================
Always-running background daemon that handles ALL agents:
  1. Auto-outreach for scout leads (lead_type='scraped')
  2. Follow-up email sending (campaign-driven + ad-hoc)
  3. Reply detection via IMAP scanning (ALWAYS active)
  4. Campaign step advancement
  5. Appointment agent (check + followup)
  6. Deal closer agent (check for responses)
  7. Report agent (periodic summary)

Runs on a configurable interval (default: 4 minutes).
Stores execution log in the daemon_log database table.

Usage:
    daemon = DeusDaemon()
    daemon.start()
    # ... later ...
    daemon.stop()

    # Or check status:
    print(daemon.status())
"""

import os
import sys
import time
import json
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 240  # 4 minutes
MAX_LOG_ENTRIES = 1000


@dataclass
class DaemonStatus:
    """Current daemon status."""
    running: bool = False
    pid: int = 0
    started_at: float = 0.0
    last_cycle_at: float = 0.0
    cycle_count: int = 0
    total_emails_sent: int = 0
    total_replies_detected: int = 0
    total_campaign_steps: int = 0
    errors: int = 0
    last_error: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    auto_stop_at: float = 0.0  # timestamp when daemon should auto-stop


class DeusDaemon:
    """
    Always-running daemon for automated follow-ups and reply detection.

    The daemon runs in a background thread and performs these tasks each cycle:
      1. Scan for email replies (IMAP)
      2. Send campaign steps that are due
      3. Send ad-hoc follow-ups for non-campaign leads
    """

    def __init__(self, interval_seconds: int = None):
        self._interval = interval_seconds or int(
            os.getenv("DAEMON_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callback: Optional[Callable] = None
        self._auto_stop_timer: Optional[threading.Thread] = None

        self._status = DaemonStatus(
            interval_seconds=self._interval,
            pid=os.getpid(),
        )

        self._ensure_tables()

    def _ensure_tables(self):
        """Create daemon_log table if it doesn't exist."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS daemon_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cycle_number INTEGER,
                        started_at REAL DEFAULT (strftime('%s','now')),
                        completed_at REAL,
                        replies_found INTEGER DEFAULT 0,
                        leads_marked INTEGER DEFAULT 0,
                        campaign_emails_sent INTEGER DEFAULT 0,
                        followup_emails_sent INTEGER DEFAULT 0,
                        auto_outreach_sent INTEGER DEFAULT 0,
                        appointment_checks INTEGER DEFAULT 0,
                        deal_checks INTEGER DEFAULT 0,
                        errors INTEGER DEFAULT 0,
                        error_message TEXT,
                        duration_seconds REAL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_dl_cycle ON daemon_log(cycle_number);
                """)
                # Migration: add new columns if missing
                for col in ["auto_outreach_sent", "appointment_checks", "deal_checks"]:
                    try:
                        conn.execute(f"ALTER TABLE daemon_log ADD COLUMN {col} INTEGER DEFAULT 0")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("Could not create daemon_log table: %s", e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, auto_stop_hours: int = 0):
        """Start the daemon background thread.
        
        Args:
            auto_stop_hours: If > 0, automatically stop after this many hours.
        """
        if self._running:
            logger.warning("Daemon already running")
            return

        self._running = True
        self._status.running = True
        self._status.started_at = time.time()
        self._stop_event.clear()

        if auto_stop_hours > 0:
            self._status.auto_stop_at = time.time() + auto_stop_hours * 3600
            self._auto_stop_timer = threading.Thread(
                target=self._auto_stop_waiter,
                args=(auto_stop_hours * 3600,),
                daemon=True,
                name="DEUS-AutoStop",
            )
            self._auto_stop_timer.start()
            logger.info("Daemon will auto-stop in %d hours", auto_stop_hours)
        else:
            self._status.auto_stop_at = 0
            self._auto_stop_timer = None

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="DEUS-Daemon",
        )
        self._thread.start()
        logger.info("Daemon started (interval: %ds)", self._interval)

    def _auto_stop_waiter(self, seconds: float):
        """Wait for the specified seconds, then stop the daemon."""
        self._stop_event.wait(timeout=seconds)
        if self._running:
            logger.info("Auto-stop timer expired — stopping daemon")
            self.stop()

    def stop(self):
        """Stop the daemon background thread."""
        self._running = False
        self._status.running = False
        self._status.auto_stop_at = 0
        self._stop_event.set()

        if self._auto_stop_timer and self._auto_stop_timer.is_alive():
            self._auto_stop_timer.join(timeout=2)
            self._auto_stop_timer = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        logger.info("Daemon stopped after %d cycles", self._status.cycle_count)

    def restart(self, interval_seconds: int = None):
        """Restart the daemon with optional new interval."""
        self.stop()
        if interval_seconds:
            self._interval = interval_seconds
            self._status.interval_seconds = interval_seconds
        time.sleep(1)
        self.start()

    def set_interval(self, interval_seconds: int):
        """Update the daemon interval (takes effect after current cycle)."""
        self._interval = interval_seconds
        self._status.interval_seconds = interval_seconds

    def set_callback(self, callback: Callable):
        """Set a callback to be called after each cycle with status dict."""
        self._callback = callback

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run_loop(self):
        """Main daemon loop — runs ALL agents autonomously."""
        logger.info("Daemon loop started")

        while self._running:
            cycle_start = time.time()
            self._status.cycle_count += 1
            cycle_num = self._status.cycle_count

            logger.info("--- Daemon cycle #%d starting ---", cycle_num)

            replies_found = 0
            leads_marked = 0
            campaign_emails = 0
            followup_emails = 0
            auto_outreach = 0
            appointment_checks = 0
            deal_checks = 0
            errors = 0
            error_msg = ""

            # Task 1: Auto-outreach for scout leads (lead_type='scraped')
            try:
                outreach_result = self._auto_outreach_scout()
                auto_outreach = outreach_result.get("sent", 0)
                self._status.total_emails_sent += auto_outreach
            except Exception as e:
                errors += 1
                error_msg = f"Auto-outreach: {e}"
                logger.error("Auto-outreach failed: %s", e)

            # Task 2: Scan for replies (ALWAYS)
            try:
                reply_result = self._scan_replies()
                replies_found = reply_result.get("replies_found", 0)
                leads_marked = reply_result.get("leads_marked", 0)
                self._status.total_replies_detected += replies_found
            except Exception as e:
                errors += 1
                error_msg = f"Reply scan: {e}"
                logger.error("Reply scan failed: %s", e)

            # Task 3: Send due campaign steps
            try:
                campaign_result = self._send_campaign_steps()
                campaign_emails = campaign_result.get("sent", 0)
                self._status.total_campaign_steps += campaign_emails
            except Exception as e:
                errors += 1
                error_msg = f"Campaign steps: {e}"
                logger.error("Campaign step sending failed: %s", e)

            # Task 4: Send ad-hoc follow-ups (non-campaign leads)
            try:
                followup_result = self._send_followups()
                followup_emails = followup_result.get("sent", 0)
                self._status.total_emails_sent += followup_emails
            except Exception as e:
                errors += 1
                error_msg = f"Follow-ups: {e}"
                logger.error("Follow-up sending failed: %s", e)

            # Task 5: Check appointment agent
            try:
                apt_result = self._run_appointment_agent()
                appointment_checks = apt_result.get("checked", 0)
            except Exception as e:
                errors += 1
                error_msg = f"Appointment: {e}"
                logger.error("Appointment check failed: %s", e)

            # Task 6: Check deal closer agent
            try:
                deal_result = self._run_deal_closer()
                deal_checks = deal_result.get("checked", 0)
            except Exception as e:
                errors += 1
                error_msg = f"Deal closer: {e}"
                logger.error("Deal closer check failed: %s", e)

            duration = time.time() - cycle_start
            self._status.last_cycle_at = time.time()
            if errors > 0:
                self._status.errors += errors
                self._status.last_error = error_msg

            self._log_cycle(
                cycle_num=cycle_num,
                replies_found=replies_found,
                leads_marked=leads_marked,
                campaign_emails=campaign_emails,
                followup_emails=followup_emails,
                errors=errors,
                error_msg=error_msg,
                duration=duration,
            )

            logger.info(
                "--- Cycle #%d done (%.1fs): auto_outreach=%d, replies=%d, campaign=%d, followup=%d, appt=%d, deal=%d, errors=%d ---",
                cycle_num, duration, auto_outreach, replies_found, campaign_emails,
                followup_emails, appointment_checks, deal_checks, errors,
            )

            if self._callback:
                try:
                    self._callback({
                        "cycle": cycle_num,
                        "auto_outreach": auto_outreach,
                        "replies": replies_found,
                        "campaign_sent": campaign_emails,
                        "followup_sent": followup_emails,
                        "appointment_checks": appointment_checks,
                        "deal_checks": deal_checks,
                        "errors": errors,
                        "duration": round(duration, 1),
                    })
                except Exception:
                    pass

            self._stop_event.wait(timeout=self._interval)

    # ------------------------------------------------------------------
    # Task implementations
    # ------------------------------------------------------------------

    def _scan_replies(self) -> dict:
        """Scan for email replies via IMAP."""
        try:
            from reply_detector import scan_for_replies
            result = scan_for_replies(days_back=7)
            return result
        except ImportError:
            logger.debug("reply_detector not available")
            return {"replies_found": 0, "leads_marked": 0}
        except Exception as e:
            logger.warning("Reply scan error: %s", e)
            return {"replies_found": 0, "leads_marked": 0}

    def _send_campaign_steps(self) -> dict:
        """Send due campaign steps."""
        try:
            from campaign import get_campaign_manager
            from email_sender import get_email_sender
            from spam_checker import SpamChecker
            from send_limiter import get_send_limiter

            cm = get_campaign_manager()
            sender = get_email_sender()
            spam_checker = SpamChecker()
            limiter = get_send_limiter()

            due = cm.get_due_enrollments()
            sent = 0
            failed = 0

            for enrollment in due:
                lead_email = enrollment.get("business_email", "")
                if not lead_email:
                    continue

                # Rate limit check
                check = limiter.can_send()
                if not check["allowed"]:
                    logger.info("Rate limit hit, pausing campaign sends: %s", check["reason"])
                    break

                step = enrollment["step"]
                subject = cm.render_subject(step["subject_template"], enrollment)
                body = cm.render_body(step["body_template"], enrollment)

                # Spam check
                spam_result = spam_checker.check_before_send(subject, body)
                if not spam_result["should_send"]:
                    logger.warning("Campaign step blocked by spam check (score=%d)", spam_result["score"])
                    cm.mark_step_failed(enrollment["enrollment_id"])
                    failed += 1
                    continue

                # Send
                result = sender.send(
                    to=lead_email,
                    subject=subject,
                    body=body,
                    lead_name=enrollment.get("business_name", ""),
                )

                if result["success"]:
                    cm.mark_step_sent(enrollment["enrollment_id"])
                    limiter.record_send()
                    sent += 1
                    logger.info("Campaign step sent to %s via %s", lead_email, result["method"])
                else:
                    cm.mark_step_failed(enrollment["enrollment_id"])
                    failed += 1
                    logger.warning("Campaign step failed for %s: %s", lead_email, result["message"])

            return {"sent": sent, "failed": failed}

        except Exception as e:
            logger.error("Campaign step sending error: %s", e)
            return {"sent": 0, "failed": 0}

    def _send_followups(self) -> dict:
        """Send ad-hoc follow-ups for leads not in a campaign."""
        try:
            from followup_agent import FollowupAgent
            agent = FollowupAgent()
            result = agent.run()
            return {
                "sent": result.stats.get("followed_up", 0),
                "rejected": result.stats.get("rejected", 0),
            }
        except Exception as e:
            logger.error("Follow-up sending error: %s", e)
            return {"sent": 0}

    def _auto_outreach_scout(self) -> dict:
        """Auto-send outreach to scout-found leads only (lead_type='scraped')."""
        from outreach_agent import manual_send_active
        if manual_send_active:
            logger.info("Manual send in progress — skipping auto-scout this cycle")
            return {"sent": 0}
        try:
            from outreach_agent import OutreachAgent
            agent = OutreachAgent()
            result = agent.run(mode="auto_scout", limit=10, channel="email")
            return {"sent": result.stats.get("auto_sent", 0)}
        except Exception as e:
            logger.error("Auto-outreach error: %s", e)
            return {"sent": 0}

    def _run_appointment_agent(self) -> dict:
        """Run appointment agent to check for new appointments."""
        try:
            from appointment_agent import AppointmentAgent
            agent = AppointmentAgent()
            result = agent.run()
            return {"checked": 1, "message": result.message[:100]}
        except Exception as e:
            logger.debug("Appointment agent not available: %s", e)
            return {"checked": 0}

    def _run_deal_closer(self) -> dict:
        """Run deal closer to check for responses and close deals."""
        try:
            from deal_closer_agent import DealCloserAgent
            agent = DealCloserAgent()
            result = agent.run()
            return {"checked": 1, "message": result.message[:100]}
        except Exception as e:
            logger.debug("Deal closer not available: %s", e)
            return {"checked": 0}

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_cycle(self, cycle_num, replies_found, leads_marked,
                   campaign_emails, followup_emails, errors, error_msg, duration):
        """Record cycle results in daemon_log table."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.execute(
                    """INSERT INTO daemon_log
                       (cycle_number, started_at, completed_at, replies_found,
                        leads_marked, campaign_emails_sent, followup_emails_sent,
                        errors, error_message, duration_seconds)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cycle_num,
                        time.time() - duration,
                        time.time(),
                        replies_found,
                        leads_marked,
                        campaign_emails,
                        followup_emails,
                        errors,
                        error_msg[:500] if error_msg else "",
                        duration,
                    ),
                )

                # Trim old entries
                count = conn.execute("SELECT COUNT(*) as c FROM daemon_log").fetchone()["c"]
                if count > MAX_LOG_ENTRIES:
                    conn.execute(
                        """DELETE FROM daemon_log WHERE id IN (
                           SELECT id FROM daemon_log ORDER BY started_at ASC LIMIT ?
                        )""",
                        (count - MAX_LOG_ENTRIES,),
                    )
        except Exception as e:
            logger.warning("Failed to log daemon cycle: %s", e)

    # ------------------------------------------------------------------
    # Status & history
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Get current daemon status."""
        auto_stop_at = self._status.auto_stop_at
        remaining = max(0, auto_stop_at - time.time()) if auto_stop_at > 0 else 0
        return {
            "running": self._status.running,
            "pid": self._status.pid,
            "interval_seconds": self._status.interval_seconds,
            "started_at": self._status.started_at,
            "uptime_seconds": time.time() - self._status.started_at if self._status.started_at else 0,
            "auto_stop_hours_remaining": round(remaining / 3600, 1) if remaining > 0 else 0,
            "auto_stop_at": auto_stop_at,
            "cycle_count": self._status.cycle_count,
            "total_emails_sent": self._status.total_emails_sent,
            "total_replies_detected": self._status.total_replies_detected,
            "total_campaign_steps": self._status.total_campaign_steps,
            "errors": self._status.errors,
            "last_error": self._status.last_error,
            "last_cycle_at": self._status.last_cycle_at,
            "agents_active": ["lead_scout", "outreach", "followup", "appointment", "deal_closer", "report"],
        }

    def get_log(self, limit: int = 50) -> list[dict]:
        """Get recent daemon log entries."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM daemon_log ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def get_log_stats(self) -> dict:
        """Get aggregated daemon stats from logs."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                total_cycles = conn.execute(
                    "SELECT COUNT(*) as c FROM daemon_log"
                ).fetchone()["c"]

                total_replies = conn.execute(
                    "SELECT SUM(replies_found) as s FROM daemon_log"
                ).fetchone()["s"] or 0

                total_campaign = conn.execute(
                    "SELECT SUM(campaign_emails_sent) as s FROM daemon_log"
                ).fetchone()["s"] or 0

                total_followup = conn.execute(
                    "SELECT SUM(followup_emails_sent) as s FROM daemon_log"
                ).fetchone()["s"] or 0

                total_errors = conn.execute(
                    "SELECT SUM(errors) as s FROM daemon_log"
                ).fetchone()["s"] or 0

                avg_duration = conn.execute(
                    "SELECT AVG(duration_seconds) as avg FROM daemon_log"
                ).fetchone()["avg"] or 0

                return {
                    "total_cycles": total_cycles,
                    "total_replies_detected": total_replies,
                    "total_campaign_emails": total_campaign,
                    "total_followup_emails": total_followup,
                    "total_errors": total_errors,
                    "avg_cycle_duration": round(avg_duration, 1),
                }
        except Exception:
            return {}

    def clear_log(self):
        """Clear daemon log entries."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.execute("DELETE FROM daemon_log")
        except Exception:
            pass


# Singleton
_daemon: Optional[DeusDaemon] = None


def get_daemon() -> DeusDaemon:
    """Get or create the singleton DeusDaemon."""
    global _daemon
    if _daemon is None:
        _daemon = DeusDaemon()
    return _daemon
