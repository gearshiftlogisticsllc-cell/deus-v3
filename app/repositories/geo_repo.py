"""
app/repositories/geo_repo.py — GeoTarget Repository
=====================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.geo import GeoTarget


class GeoTargetRepository(BaseRepository[GeoTarget]):
    def __init__(self, session):
        super().__init__(GeoTarget, session)

    def list_enabled(self) -> List[GeoTarget]:
        stmt = select(GeoTarget).where(GeoTarget.enabled == 1)
        return list(self.session.execute(stmt).scalars().all())

    def list_by_country(self, country: str) -> List[GeoTarget]:
        stmt = select(GeoTarget).where(GeoTarget.country == country)
        return list(self.session.execute(stmt).scalars().all())

    def list_by_target_type(self, target_type: str) -> List[GeoTarget]:
        stmt = select(GeoTarget).where(GeoTarget.target_type == target_type)
        return list(self.session.execute(stmt).scalars().all())
