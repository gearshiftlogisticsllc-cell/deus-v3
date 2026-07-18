"""
app/schemas/user.py — Pydantic v2 Schemas for User & Session
==============================================================
"""

from typing import Optional
from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"


class UserRead(BaseModel):
    id: int
    username: str
    role: str
    created_at: Optional[float] = None
    last_login: Optional[float] = None

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    username: str
    password: str


class SessionRead(BaseModel):
    token: str
    user_id: int
    expires_at: float

    class Config:
        from_attributes = True
