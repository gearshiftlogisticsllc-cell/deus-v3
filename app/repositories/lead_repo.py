"""
app/repositories/lead_repo.py — Lead Repository
=================================================
Matches the current database.py lead functions using SQLAlchemy ORM.

Usage:
    from app.repositories import LeadRepository
    from app.db import SessionLocal

    with SessionLocal() as session:
        repo = LeadRepository(session)
        lead = repo.get(123)
        leads = repo.list_outreach_candidates(lead_type="scraped")
"""

from typing import Optional, List
from sqlalchemy import select, or_, func
from app.repositories.base import BaseRepository
from app.models.lead import Lead


class LeadRepository(BaseRepository[Lead]):
    def __init__(self, session):
        super().__init__(Lead, session)

    def get_by_email(self, email: str) -> Optional[Lead]:
        stmt = select(Lead).where(Lead.business_email == email)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_filtered(
        self,
        status: Optional[str] = None,
        outreach_ready: Optional[bool] = None,
        has_email: Optional[bool] = None,
        lead_type: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[Lead]:
        stmt = select(Lead)
        if status:
            stmt = stmt.where(Lead.status == status)
        if outreach_ready is not None:
            stmt = stmt.where(Lead.outreach_ready == (1 if outreach_ready else 0))
        if has_email is not None:
            if has_email:
                stmt = stmt.where(Lead.business_email.isnot(None), Lead.business_email != "")
            else:
                stmt = stmt.where(
                    or_(Lead.business_email.is_(None), Lead.business_email == "")
                )
        if lead_type:
            stmt = stmt.where(Lead.lead_type == lead_type)
        stmt = stmt.order_by(Lead.score.desc(), Lead.id.asc()).offset(offset).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def count_filtered(
        self,
        status: Optional[str] = None,
        outreach_ready: Optional[bool] = None,
    ) -> int:
        stmt = select(func.count()).select_from(Lead)
        if status:
            stmt = stmt.where(Lead.status == status)
        if outreach_ready is not None:
            stmt = stmt.where(Lead.outreach_ready == (1 if outreach_ready else 0))
        result = self.session.execute(stmt).scalar()
        return result or 0

    def list_outreach_candidates(
        self, limit: int = 25, lead_type: Optional[str] = None
    ) -> List[Lead]:
        stmt = select(Lead).where(
            Lead.business_email.isnot(None),
            Lead.business_email != "",
            Lead.status != "contacted",
        )
        if lead_type:
            stmt = stmt.where(Lead.lead_type == lead_type)
        stmt = stmt.order_by(Lead.score.desc(), Lead.id.asc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def is_contacted(self, email: str) -> bool:
        stmt = select(Lead.id).where(
            Lead.business_email == email, Lead.status == "contacted"
        ).limit(1)
        return self.session.execute(stmt).first() is not None

    def is_unsubscribed(self, email: str) -> bool:
        stmt = select(Lead.id).where(
            Lead.business_email == email,
            Lead.status.in_(["unsubscribed", "blocked"]),
        ).limit(1)
        return self.session.execute(stmt).first() is not None

    def mark_unsubscribed(self, email: str) -> bool:
        import time
        stmt = select(Lead).where(Lead.business_email == email)
        lead = self.session.execute(stmt).scalar_one_or_none()
        if not lead:
            return False
        lead.status = "unsubscribed"
        lead.updated_at = time.time()
        self.session.flush()
        return True

    def get_contacted_emails(self) -> set:
        stmt = select(Lead.business_email).where(
            Lead.status == "contacted",
            Lead.business_email.isnot(None),
            Lead.business_email != "",
        )
        return {row[0] for row in self.session.execute(stmt).all()}

    def mark_contacted(self, ids: list[int], channel: str = "email"):
        import time
        now = time.time()
        stmt = select(Lead).where(Lead.id.in_(ids))
        leads = list(self.session.execute(stmt).scalars().all())
        for lead in leads:
            lead.status = "contacted"
            lead.channel_used = channel
            lead.last_contacted_at = now
            lead.contact_count = (lead.contact_count or 0) + 1
            lead.updated_at = now
        self.session.flush()

    def update_partial(self, lead_id: int, updates: dict):
        lead = self.get(lead_id)
        if not lead:
            return
        import time
        allowed = {
            "business_name", "owner_name", "business_email", "phone", "website",
            "address", "niche", "category", "services_offered", "linkedin_url",
            "instagram_handle", "facebook_url", "source", "status", "outreach_ready",
            "needs_human", "needs_human_reason", "channel_used", "preferred_channel",
            "score", "notes", "lead_type", "import_batch_id", "import_filename",
            "email_verified", "email_verified_at", "verification_method",
            "first_contacted_at", "last_contacted_at", "contact_count",
        }
        for key, value in updates.items():
            if key in allowed:
                setattr(lead, key, value)
        lead.updated_at = time.time()
        self.session.flush()

    def batch_update(self, ids: list[int], updates: dict):
        for lid in ids:
            self.update_partial(lid, updates)

    def upsert_lead(self, data: dict) -> int:
        """Match on business_email, update if exists, insert if not."""
        email = data.get("business_email", "")
        if not email:
            return self.create(**data).id
        existing = self.get_by_email(email)
        if existing:
            for key, value in data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            import time
            existing.updated_at = time.time()
            self.session.flush()
            self.session.refresh(existing)
            return existing.id
        else:
            lead = self.create(**data)
            return lead.id

    def upsert_batch(self, leads: list[dict]) -> dict:
        imported = 0
        skipped = 0
        import time
        for data in leads:
            email = data.get("business_email", "")
            if not email and not data.get("phone"):
                skipped += 1
                continue
            data.setdefault("status", "new")
            data.setdefault("outreach_ready", bool(data.get("business_email")))
            data.setdefault("needs_human", 0)
            data.setdefault("preferred_channel", "email")
            data.setdefault("source", "import")
            data.setdefault("score", 0)
            data.setdefault("created_at", time.time())
            data.setdefault("updated_at", time.time())
            self.upsert_lead(data)
            imported += 1
        return {"imported": imported, "skipped": skipped}
