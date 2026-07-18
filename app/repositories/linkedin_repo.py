from typing import Optional, List, Dict, Any
from sqlalchemy import select, func
from app.repositories.base import BaseRepository
from app.models.linkedin import LinkedInQueue


class LinkedInQueueRepository(BaseRepository[LinkedInQueue]):
    def __init__(self, session):
        super().__init__(LinkedInQueue, session)

    def list_by_status(self, status: str, limit: int = 50) -> List[LinkedInQueue]:
        stmt = (
            select(LinkedInQueue)
            .where(LinkedInQueue.status == status)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_pending(self, limit: int = 50) -> List[LinkedInQueue]:
        return self.list_by_status("pending", limit)

    def count_by_status(self) -> Dict[str, int]:
        stmt = (
            select(LinkedInQueue.status, func.count())
            .group_by(LinkedInQueue.status)
        )
        return dict(self.session.execute(stmt).all())

    def get_by_lead_id(self, lead_id: int) -> Optional[LinkedInQueue]:
        stmt = select(LinkedInQueue).where(LinkedInQueue.lead_id == lead_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def mark_connection_sent(self, ids: List[int]) -> None:
        stmt = (
            select(LinkedInQueue)
            .where(LinkedInQueue.id.in_(ids))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        for row in rows:
            row.status = "connection_sent"
        self.session.flush()

    def mark_message_sent(self, ids: List[int]) -> None:
        stmt = (
            select(LinkedInQueue)
            .where(LinkedInQueue.id.in_(ids))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        for row in rows:
            row.status = "message_sent"
        self.session.flush()

    def mark_replied(self, ids: List[int]) -> None:
        stmt = (
            select(LinkedInQueue)
            .where(LinkedInQueue.id.in_(ids))
        )
        rows = list(self.session.execute(stmt).scalars().all())
        for row in rows:
            row.status = "replied"
        self.session.flush()

    def bulk_enqueue(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        imported = 0
        skipped = 0
        for item in items:
            lead_id = item.get("lead_id")
            linkedin_url = item.get("linkedin_url")
            if lead_id and linkedin_url:
                existing = self.get_by_lead_id(lead_id)
                if existing:
                    skipped += 1
                    continue
            self.create(**item)
            imported += 1
        return {"imported": imported, "skipped": skipped}
