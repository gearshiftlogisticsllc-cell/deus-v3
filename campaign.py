"""
campaign.py — DEUS 3.0
========================
Campaign manager with scheduled steps. Each campaign has:
  - A name, description, and status
  - Multiple steps, each with a day_offset, subject template, and body template
  - Enrollment of leads into campaigns with per-lead step tracking

Campaigns replace the old simple followup with structured multi-touch sequences.

DB Tables: campaigns, campaign_steps, campaign_enrollments

Usage:
    cm = CampaignManager()
    cm.create_campaign("Cold Outreach v2", steps=[...])
    cm.enroll_leads(campaign_id, lead_ids)
    due = cm.get_due_enrollments()
    cm.mark_step_sent(enrollment_id, step_id)
"""

import os
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CampaignStep:
    step_id: int = 0
    campaign_id: int = 0
    step_order: int = 0
    day_offset: int = 0          # Days after enrollment to send this step
    subject_template: str = ""
    body_template: str = ""
    channel: str = "email"
    is_ai_generated: bool = True  # If True, LLM generates the content


@dataclass
class Campaign:
    campaign_id: int = 0
    name: str = ""
    description: str = ""
    status: str = "active"      # active | paused | completed | archived
    created_at: float = 0.0
    steps: list = field(default_factory=list)


class CampaignManager:
    """Manages email campaigns with multi-step sequences."""

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """Create tables if they don't exist."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS campaigns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        status TEXT DEFAULT 'active',
                        created_at REAL DEFAULT (strftime('%s','now')),
                        updated_at REAL DEFAULT (strftime('%s','now'))
                    );

                    CREATE TABLE IF NOT EXISTS campaign_steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        campaign_id INTEGER NOT NULL,
                        step_order INTEGER NOT NULL,
                        day_offset INTEGER NOT NULL DEFAULT 0,
                        subject_template TEXT DEFAULT '',
                        body_template TEXT DEFAULT '',
                        channel TEXT DEFAULT 'email',
                        is_ai_generated INTEGER DEFAULT 1,
                        FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
                    );

                    CREATE TABLE IF NOT EXISTS campaign_enrollments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        campaign_id INTEGER NOT NULL,
                        lead_id INTEGER NOT NULL,
                        current_step INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'active',
                        enrolled_at REAL DEFAULT (strftime('%s','now')),
                        last_sent_at REAL,
                        completed_at REAL,
                        FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
                        FOREIGN KEY (lead_id) REFERENCES leads(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_ce_campaign ON campaign_enrollments(campaign_id);
                    CREATE INDEX IF NOT EXISTS idx_ce_lead ON campaign_enrollments(lead_id);
                    CREATE INDEX IF NOT EXISTS idx_ce_status ON campaign_enrollments(status);
                """)
        except Exception as e:
            logger.warning("Could not create campaign tables: %s", e)

    # ------------------------------------------------------------------
    # Campaign CRUD
    # ------------------------------------------------------------------

    def create_campaign(self, name: str, description: str = "", steps: list = None) -> int:
        """Create a campaign with optional steps. Returns campaign ID."""
        from app.database import db_conn
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT INTO campaigns (name, description) VALUES (?, ?)",
                (name, description),
            )
            campaign_id = cur.lastrowid

            if steps:
                for i, step in enumerate(steps):
                    conn.execute(
                        """INSERT INTO campaign_steps
                           (campaign_id, step_order, day_offset, subject_template,
                            body_template, channel, is_ai_generated)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            campaign_id,
                            i + 1,
                            step.get("day_offset", 0),
                            step.get("subject_template", ""),
                            step.get("body_template", ""),
                            step.get("channel", "email"),
                            1 if step.get("is_ai_generated", True) else 0,
                        ),
                    )

        logger.info("Campaign created: %s (id=%d)", name, campaign_id)
        return campaign_id

    def get_campaign(self, campaign_id: int) -> Optional[Campaign]:
        """Get a campaign by ID with its steps."""
        from app.database import db_conn
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if not row:
                return None

            steps = conn.execute(
                "SELECT * FROM campaign_steps WHERE campaign_id = ? ORDER BY step_order",
                (campaign_id,),
            ).fetchall()

            return Campaign(
                campaign_id=row["id"],
                name=row["name"],
                description=row["description"],
                status=row["status"],
                created_at=row["created_at"],
                steps=[
                    CampaignStep(
                        step_id=s["id"],
                        campaign_id=s["campaign_id"],
                        step_order=s["step_order"],
                        day_offset=s["day_offset"],
                        subject_template=s["subject_template"],
                        body_template=s["body_template"],
                        channel=s["channel"],
                        is_ai_generated=bool(s["is_ai_generated"]),
                    )
                    for s in steps
                ],
            )

    def list_campaigns(self, status: str = None) -> list[Campaign]:
        """List all campaigns, optionally filtered by status."""
        from app.database import db_conn
        with db_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM campaigns WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM campaigns ORDER BY created_at DESC"
                ).fetchall()

            result = []
            for row in rows:
                camp = self.get_campaign(row["id"])
                if camp:
                    result.append(camp)
            return result

    def update_campaign_status(self, campaign_id: int, status: str):
        """Update campaign status (active/paused/completed/archived)."""
        from app.database import db_conn
        valid_statuses = ("active", "paused", "completed", "archived")
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")
        with db_conn() as conn:
            conn.execute(
                "UPDATE campaigns SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), campaign_id),
            )
        logger.info("Campaign %d status -> %s", campaign_id, status)

    def delete_campaign(self, campaign_id: int):
        """Delete a campaign and all its steps/enrollments."""
        from app.database import db_conn
        with db_conn() as conn:
            conn.execute("DELETE FROM campaign_enrollments WHERE campaign_id = ?", (campaign_id,))
            conn.execute("DELETE FROM campaign_steps WHERE campaign_id = ?", (campaign_id,))
            conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        logger.info("Campaign %d deleted", campaign_id)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def add_step(self, campaign_id: int, step: dict, position: int = None) -> int:
        """Add a step to a campaign. Returns step ID."""
        from app.database import db_conn
        with db_conn() as conn:
            if position is None:
                row = conn.execute(
                    "SELECT MAX(step_order) as mx FROM campaign_steps WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                position = (row["mx"] or 0) + 1

            cur = conn.execute(
                """INSERT INTO campaign_steps
                   (campaign_id, step_order, day_offset, subject_template,
                    body_template, channel, is_ai_generated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    campaign_id,
                    position,
                    step.get("day_offset", 0),
                    step.get("subject_template", ""),
                    step.get("body_template", ""),
                    step.get("channel", "email"),
                    1 if step.get("is_ai_generated", True) else 0,
                ),
            )
            return cur.lastrowid

    def update_step(self, step_id: int, updates: dict):
        """Update a campaign step."""
        from app.database import db_conn
        allowed = {"day_offset", "subject_template", "body_template", "channel", "is_ai_generated", "step_order"}
        sets = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if sets:
            vals.append(step_id)
            with db_conn() as conn:
                conn.execute(
                    f"UPDATE campaign_steps SET {', '.join(sets)} WHERE id = ?",
                    vals,
                )

    def delete_step(self, step_id: int):
        """Delete a campaign step."""
        from app.database import db_conn
        with db_conn() as conn:
            conn.execute("DELETE FROM campaign_steps WHERE id = ?", (step_id,))

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll_leads(self, campaign_id: int, lead_ids: list[int]) -> dict:
        """Enroll leads into a campaign. Skips already-enrolled leads."""
        from app.database import db_conn
        enrolled = 0
        skipped = 0

        with db_conn() as conn:
            for lid in lead_ids:
                existing = conn.execute(
                    """SELECT id FROM campaign_enrollments
                       WHERE campaign_id = ? AND lead_id = ? AND status = 'active'""",
                    (campaign_id, lid),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                conn.execute(
                    """INSERT INTO campaign_enrollments (campaign_id, lead_id)
                       VALUES (?, ?)""",
                    (campaign_id, lid),
                )
                enrolled += 1

        logger.info("Enrolled %d leads into campaign %d (skipped %d)", enrolled, campaign_id, skipped)
        return {"enrolled": enrolled, "skipped": skipped}

    def unenroll_lead(self, campaign_id: int, lead_id: int):
        """Remove a lead from a campaign."""
        from app.database import db_conn
        with db_conn() as conn:
            conn.execute(
                """UPDATE campaign_enrollments SET status = 'removed'
                   WHERE campaign_id = ? AND lead_id = ? AND status = 'active'""",
                (campaign_id, lead_id),
            )

    def get_enrollments(self, campaign_id: int, status: str = "active") -> list[dict]:
        """Get all enrollments for a campaign."""
        from app.database import db_conn
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT ce.*, l.business_name, l.business_email, l.niche
                   FROM campaign_enrollments ce
                   LEFT JOIN leads l ON ce.lead_id = l.id
                   WHERE ce.campaign_id = ? AND ce.status = ?
                   ORDER BY ce.enrolled_at""",
                (campaign_id, status),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_lead_campaigns(self, lead_id: int) -> list[dict]:
        """Get all active campaigns for a lead."""
        from app.database import db_conn
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT ce.*, c.name as campaign_name, c.status as campaign_status
                   FROM campaign_enrollments ce
                   JOIN campaigns c ON ce.campaign_id = c.id
                   WHERE ce.lead_id = ? AND ce.status = 'active'""",
                (lead_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Due steps / Sending
    # ------------------------------------------------------------------

    def get_due_enrollments(self) -> list[dict]:
        """
        Find enrollments where a step is due based on day_offset.
        Returns list of dicts with enrollment info + step info.
        """
        from app.database import db_conn
        now = time.time()

        with db_conn() as conn:
            # Get active campaigns with active enrollments
            rows = conn.execute(
                """SELECT
                       ce.id as enrollment_id,
                       ce.campaign_id,
                       ce.lead_id,
                       ce.current_step,
                       ce.enrolled_at,
                       ce.last_sent_at,
                       l.business_name,
                       l.business_email,
                       l.niche,
                       c.name as campaign_name
                   FROM campaign_enrollments ce
                   JOIN campaigns c ON ce.campaign_id = c.id
                   JOIN leads l ON ce.lead_id = l.id
                   WHERE ce.status = 'active'
                     AND c.status = 'active'
                     AND l.business_email IS NOT NULL
                     AND l.business_email != ''"""
            ).fetchall()

            due = []
            for row in rows:
                enrollment = dict(row)
                next_step_num = enrollment["current_step"] + 1

                # Get the next step
                step = conn.execute(
                    """SELECT * FROM campaign_steps
                       WHERE campaign_id = ? AND step_order = ?
                       LIMIT 1""",
                    (enrollment["campaign_id"], next_step_num),
                ).fetchone()

                if not step:
                    # No more steps — campaign complete for this lead
                    continue

                step = dict(step)
                enrolled_at = enrollment["enrolled_at"]
                day_offset = step["day_offset"]

                # Check if enough time has passed
                due_at = enrolled_at + (day_offset * 86400)
                if now >= due_at:
                    # Check cooldown since last send (min 4 hours)
                    last_sent = enrollment.get("last_sent_at") or 0
                    if last_sent and (now - last_sent) < 14400:
                        continue

                    enrollment["step"] = step
                    enrollment["due_at"] = due_at
                    due.append(enrollment)

            return due

    def mark_step_sent(self, enrollment_id: int):
        """Mark current step as sent, advance to next step."""
        from app.database import db_conn
        with db_conn() as conn:
            enrollment = conn.execute(
                "SELECT * FROM campaign_enrollments WHERE id = ?",
                (enrollment_id,),
            ).fetchone()
            if not enrollment:
                return

            new_step = enrollment["current_step"] + 1
            now = time.time()

            # Check if there are more steps
            next_step = conn.execute(
                """SELECT id FROM campaign_steps
                   WHERE campaign_id = ? AND step_order = ?
                   LIMIT 1""",
                (enrollment["campaign_id"], new_step + 1),
            ).fetchone()

            if next_step:
                conn.execute(
                    """UPDATE campaign_enrollments
                       SET current_step = ?, last_sent_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_step, now, now, enrollment_id),
                )
            else:
                # Campaign complete for this lead
                conn.execute(
                    """UPDATE campaign_enrollments
                       SET current_step = ?, last_sent_at = ?, status = 'completed',
                           completed_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_step, now, now, now, enrollment_id),
                )

    def mark_step_failed(self, enrollment_id: int):
        """Mark a step send as failed (don't advance). Will retry next cycle."""
        from app.database import db_conn
        with db_conn() as conn:
            conn.execute(
                """UPDATE campaign_enrollments
                   SET updated_at = ?
                   WHERE id = ?""",
                (time.time(), enrollment_id),
            )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_campaign_stats(self, campaign_id: int) -> dict:
        """Get stats for a campaign."""
        from app.database import db_conn
        with db_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()["c"]

            active = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments WHERE campaign_id = ? AND status = 'active'",
                (campaign_id,),
            ).fetchone()["c"]

            completed = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments WHERE campaign_id = ? AND status = 'completed'",
                (campaign_id,),
            ).fetchone()["c"]

            removed = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments WHERE campaign_id = ? AND status = 'removed'",
                (campaign_id,),
            ).fetchone()["c"]

            steps_count = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_steps WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()["c"]

            return {
                "campaign_id": campaign_id,
                "total_enrolled": total,
                "active": active,
                "completed": completed,
                "removed": removed,
                "total_steps": steps_count,
            }

    def get_all_stats(self) -> dict:
        """Get stats across all campaigns."""
        from app.database import db_conn
        with db_conn() as conn:
            campaigns = conn.execute("SELECT COUNT(*) as c FROM campaigns").fetchone()["c"]
            active = conn.execute(
                "SELECT COUNT(*) as c FROM campaigns WHERE status = 'active'"
            ).fetchone()["c"]
            total_enrolled = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments"
            ).fetchone()["c"]
            total_active_enrollments = conn.execute(
                "SELECT COUNT(*) as c FROM campaign_enrollments WHERE status = 'active'"
            ).fetchone()["c"]

            return {
                "total_campaigns": campaigns,
                "active_campaigns": active,
                "total_enrolled": total_enrolled,
                "active_enrollments": total_active_enrollments,
            }

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def render_subject(self, subject_template: str, lead: dict) -> str:
        """Render a subject template with lead fields."""
        try:
            return subject_template.format(
                business_name=lead.get("business_name", "your business"),
                niche=lead.get("niche", ""),
                owner_name=lead.get("owner_name", ""),
            )
        except (KeyError, IndexError):
            return subject_template

    def render_body(self, body_template: str, lead: dict) -> str:
        """Render a body template with lead fields."""
        try:
            return body_template.format(
                business_name=lead.get("business_name", "your business"),
                niche=lead.get("niche", ""),
                owner_name=lead.get("owner_name", ""),
                services=lead.get("services_offered", ""),
            )
        except (KeyError, IndexError):
            return body_template


# Singleton
_campaign_manager: Optional[CampaignManager] = None


def get_campaign_manager() -> CampaignManager:
    """Get or create the singleton CampaignManager."""
    global _campaign_manager
    if _campaign_manager is None:
        _campaign_manager = CampaignManager()
    return _campaign_manager
