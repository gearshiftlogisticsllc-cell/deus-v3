"""
app/repositories/pending_repo.py — PendingChange Repository
============================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.pending import PendingChange


class PendingChangeRepository(BaseRepository[PendingChange]):
    def __init__(self, session):
        super().__init__(PendingChange, session)

    def list_pending(self) -> List[PendingChange]:
        stmt = select(PendingChange).where(
            PendingChange.status == "pending"
        ).order_by(PendingChange.created_at.asc())
        return list(self.session.execute(stmt).scalars().all())

    def list_by_user(self, user_id: int) -> List[PendingChange]:
        stmt = select(PendingChange).where(
            PendingChange.user_id == user_id
        ).order_by(PendingChange.created_at.desc())
        return list(self.session.execute(stmt).scalars().all())

    def list_by_status(self, status: str) -> List[PendingChange]:
        stmt = select(PendingChange).where(
            PendingChange.status == status
        ).order_by(PendingChange.created_at.desc())
        return list(self.session.execute(stmt).scalars().all())
