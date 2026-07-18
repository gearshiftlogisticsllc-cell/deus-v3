from sqlalchemy import Column, Integer, String, Float
from app.db import Base


class GeoTarget(Base):
    __tablename__ = "geo_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String, nullable=False)
    state = Column(String, default="")
    city = Column(String, default="")
    niche = Column(String, default="")
    scheduled_day = Column(String, default="")
    scheduled_time = Column(String, default="")
    scheduled_date = Column(String, default="")
    target_type = Column(String, default="scout")
    enabled = Column(Integer, default=1)
    created_at = Column(Float, default=None)
