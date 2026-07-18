"""
app/repositories/calendar_repo.py — CampaignCalendar Repository
================================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.calendar import CampaignCalendar


class CampaignCalendarRepository(BaseRepository[CampaignCalendar]):
    def __init__(self, session):
        super().__init__(CampaignCalendar, session)

    def get_by_campaign(self, campaign_id: int) -> List[CampaignCalendar]:
        stmt = select(CampaignCalendar).where(
            CampaignCalendar.campaign_id == campaign_id
        ).order_by(CampaignCalendar.scheduled_date.asc())
        return list(self.session.execute(stmt).scalars().all())

    def list_active(self) -> List[CampaignCalendar]:
        stmt = select(CampaignCalendar).where(CampaignCalendar.active == 1)
        return list(self.session.execute(stmt).scalars().all())

    def list_by_date(self, scheduled_date: str) -> List[CampaignCalendar]:
        stmt = select(CampaignCalendar).where(
            CampaignCalendar.scheduled_date == scheduled_date,
            CampaignCalendar.active == 1,
        )
        return list(self.session.execute(stmt).scalars().all())
