"""
app/repositories/daemon_repo.py — Daemon Repository
=====================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.daemon import DaemonConfig, DaemonLog


class DaemonConfigRepository(BaseRepository[DaemonConfig]):
    def __init__(self, session):
        super().__init__(DaemonConfig, session)

    def get_by_agent_name(self, agent_name: str) -> Optional[DaemonConfig]:
        stmt = select(DaemonConfig).where(DaemonConfig.agent_name == agent_name)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_enabled(self) -> List[DaemonConfig]:
        stmt = select(DaemonConfig).where(DaemonConfig.enabled == 1)
        return list(self.session.execute(stmt).scalars().all())


class DaemonLogRepository(BaseRepository[DaemonLog]):
    def __init__(self, session):
        super().__init__(DaemonLog, session)

    def get_latest(self) -> Optional[DaemonLog]:
        stmt = select(DaemonLog).order_by(DaemonLog.id.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_recent(self, limit: int = 20) -> List[DaemonLog]:
        stmt = select(DaemonLog).order_by(DaemonLog.id.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())
