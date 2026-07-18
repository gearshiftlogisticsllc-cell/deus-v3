"""
app/repositories/analytics_repo.py — Analytics Repository
==========================================================
"""

from typing import Optional, List, Dict, Any
from sqlalchemy import select, func
from app.repositories.base import BaseRepository
from app.models.analytics import Analytics, AnalyticsDelivery, AnalyticsDaily


class AnalyticsRepository(BaseRepository[Analytics]):
    def __init__(self, session):
        super().__init__(Analytics, session)

    def get_by_metric(self, metric: str, date: Optional[str] = None) -> List[Analytics]:
        stmt = select(Analytics).where(Analytics.metric == metric)
        if date is not None:
            stmt = stmt.where(Analytics.date == date)
        stmt = stmt.order_by(Analytics.id.desc())
        return list(self.session.execute(stmt).scalars().all())

    def get_summary(self) -> Dict[str, Any]:
        stmt = select(
            Analytics.metric,
            func.sum(Analytics.value).label("total"),
            func.count(Analytics.id).label("count"),
        ).group_by(Analytics.metric)
        rows = self.session.execute(stmt).all()
        summary = {}
        for row in rows:
            summary[row.metric] = {"total": row.total or 0, "count": row.count}
        return summary


class AnalyticsDeliveryRepository(BaseRepository[AnalyticsDelivery]):
    def __init__(self, session):
        super().__init__(AnalyticsDelivery, session)

    def get_by_inbox_status(self, status: str) -> List[AnalyticsDelivery]:
        stmt = select(AnalyticsDelivery).where(AnalyticsDelivery.inbox_status == status)
        return list(self.session.execute(stmt).scalars().all())


class AnalyticsDailyRepository(BaseRepository[AnalyticsDaily]):
    def __init__(self, session):
        super().__init__(AnalyticsDaily, session)

    def get_by_date(self, date: str) -> List[AnalyticsDaily]:
        stmt = select(AnalyticsDaily).where(AnalyticsDaily.date == date)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_metric(self, metric: str) -> List[AnalyticsDaily]:
        stmt = select(AnalyticsDaily).where(AnalyticsDaily.metric == metric)
        return list(self.session.execute(stmt).scalars().all())
