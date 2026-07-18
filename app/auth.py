"""
app/auth.py — DEUS 3.0 Authentication Helpers
==============================================
Shared session/user helpers used by all routers.
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import HTTPException, Request
from typing import Optional

from app.database import (
    validate_session,
)


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("deus_session")
    if not token:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
    return validate_session(token)


def require_auth(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> dict:
    user = require_auth(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
