from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class CustomPipeline(Base):
    __tablename__ = "custom_pipelines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, default="")
    steps = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Float, default=None)
