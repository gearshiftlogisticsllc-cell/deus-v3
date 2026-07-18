from sqlalchemy import Column, Integer, String, Float, ForeignKey
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    salt = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    created_at = Column(Float, default=None)
    last_login = Column(Float, default=0)


class Session(Base):
    __tablename__ = "sessions"

    token = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(Float, default=None)
    expires_at = Column(Float, nullable=False)
