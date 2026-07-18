"""
app/repositories/schedule_repo.py — Schedule Repository
========================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.schedule import Schedule, ScheduleRun


class ScheduleRepository(BaseRepository[Schedule]):
    def __init__(self, session):
        super().__init__(Schedule, session)

    def get_by_name(self, name: str) -> Optional[Schedule]:
        stmt = select(Schedule).where(Schedule.name == name)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_enabled(self) -> List[Schedule]:
        stmt = select(Schedule).where(Schedule.enabled == 1)
        return list(self.session.execute(stmt).scalars().all())

    def list_due(self, now_timestamp: float) -> List[Schedule]:
        stmt = select(Schedule).where(
            Schedule.enabled == 1,
            Schedule.next_run_at <= now_timestamp,
        )
        return list(self.session.execute(stmt).scalars().all())


class ScheduleRunRepository(BaseRepository[ScheduleRun]):
    def __init__(self, session):
        super().__init__(ScheduleRun, session)

    def get_recent(self, limit: int = 20) -> List[ScheduleRun]:
        stmt = select(ScheduleRun).order_by(ScheduleRun.id.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_schedule(self, schedule_id: int) -> List[ScheduleRun]:
        stmt = select(ScheduleRun).where(
            ScheduleRun.schedule_id == schedule_id
        ).order_by(ScheduleRun.id.desc())
        return list(self.session.execute(stmt).scalars().all())
