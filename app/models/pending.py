from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class PendingChange(Base):
    __tablename__ = "pending_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(Float, default=None)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(Float, nullable=True)
