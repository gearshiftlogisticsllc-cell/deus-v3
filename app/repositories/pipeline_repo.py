"""
app/repositories/pipeline_repo.py — CustomPipeline Repository
==============================================================
"""

from typing import Optional
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.pipeline import CustomPipeline


class CustomPipelineRepository(BaseRepository[CustomPipeline]):
    def __init__(self, session):
        super().__init__(CustomPipeline, session)

    def get_by_name(self, name: str) -> Optional[CustomPipeline]:
        stmt = select(CustomPipeline).where(CustomPipeline.name == name)
        return self.session.execute(stmt).scalar_one_or_none()
