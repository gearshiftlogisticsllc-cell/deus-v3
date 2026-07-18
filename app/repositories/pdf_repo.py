"""
app/repositories/pdf_repo.py — PdfRule Repository
==================================================
"""

from typing import Optional, List
from sqlalchemy import select
from app.repositories.base import BaseRepository
from app.models.pdf import PdfRule


class PdfRuleRepository(BaseRepository[PdfRule]):
    def __init__(self, session):
        super().__init__(PdfRule, session)

    def get_by_filename(self, filename: str) -> Optional[PdfRule]:
        stmt = select(PdfRule).where(PdfRule.filename == filename)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_active(self) -> List[PdfRule]:
        stmt = select(PdfRule).where(PdfRule.active == 1)
        return list(self.session.execute(stmt).scalars().all())
