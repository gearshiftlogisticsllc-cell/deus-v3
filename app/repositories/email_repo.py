from typing import Optional, List
import time
from sqlalchemy import select, func, and_
from app.repositories.base import BaseRepository
from app.models.email import EmailLog, EmailEvent


class EmailRepository(BaseRepository[EmailLog]):
    def __init__(self, session):
        super().__init__(EmailLog, session)

    def get_by_message_id(self, message_id: str) -> Optional[EmailLog]:
        stmt = select(EmailLog).where(EmailLog.message_id == message_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_by_lead_email(self, email: str) -> List[EmailLog]:
        stmt = (
            select(EmailLog)
            .where(EmailLog.lead_email == email)
            .order_by(EmailLog.sent_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_replied(self, limit: int = 50) -> List[EmailLog]:
        stmt = (
            select(EmailLog)
            .where(EmailLog.replied_at.isnot(None))
            .order_by(EmailLog.replied_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_pending_reply_check(self, since_hours: int = 24) -> List[EmailLog]:
        cutoff = time.time() - since_hours * 3600
        stmt = (
            select(EmailLog)
            .where(
                EmailLog.sent_at >= cutoff,
                EmailLog.replied_at.is_(None),
                EmailLog.bounced_at.is_(None),
                EmailLog.complained_at.is_(None),
            )
            .order_by(EmailLog.sent_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_by_status(self, status: str) -> int:
        stmt = select(func.count()).select_from(EmailLog).where(EmailLog.status == status)
        result = self.session.execute(stmt).scalar()
        return result or 0

    def count_replied_today(self) -> int:
        today_start = time.time() - (time.time() % 86400)
        stmt = (
            select(func.count())
            .select_from(EmailLog)
            .where(EmailLog.replied_at >= today_start)
        )
        result = self.session.execute(stmt).scalar()
        return result or 0
