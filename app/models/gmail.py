from sqlalchemy import Column, Integer, String, Float, Text, CheckConstraint
from app.db import Base


class GmailToken(Base):
    __tablename__ = "gmail_tokens"

    id = Column(Integer, primary_key=True)
    token_json = Column(Text, nullable=False)
    sender_email = Column(String, default="")
    updated_at = Column(Float, default=None)

    __table_args__ = (
        CheckConstraint("id = 1"),
    )
