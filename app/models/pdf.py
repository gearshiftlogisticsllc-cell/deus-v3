from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class PdfRule(Base):
    __tablename__ = "pdf_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(Float, default=None)
    active = Column(Integer, default=1)
