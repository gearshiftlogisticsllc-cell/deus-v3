"""
app/repositories/gmail_repo.py — GmailToken Repository
=======================================================
"""

from typing import Optional
from sqlalchemy import select
import time
from app.repositories.base import BaseRepository
from app.models.gmail import GmailToken


class GmailTokenRepository(BaseRepository[GmailToken]):
    def __init__(self, session):
        super().__init__(GmailToken, session)

    def get_token(self) -> Optional[GmailToken]:
        stmt = select(GmailToken).where(GmailToken.id == 1)
        return self.session.execute(stmt).scalar_one_or_none()

    def save_token(self, token_json: str, sender_email: str = "") -> GmailToken:
        token = self.get_token()
        if token:
            token.token_json = token_json
            token.sender_email = sender_email
            token.updated_at = time.time()
            self.session.flush()
            self.session.refresh(token)
            return token
        return self.create(
            id=1,
            token_json=token_json,
            sender_email=sender_email,
            updated_at=time.time(),
        )
