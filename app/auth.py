"""
app/auth.py — DEUS 3.0 Authentication Routes
=============================================
Login, logout, session management, role-based access.
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional

from app.database import (
    authenticate_user, create_session, validate_session, delete_session,
    init_db, create_pending_change, get_pending_changes, review_pending_change,
)

router = APIRouter()

# Initialize DB on import
init_db()


class LoginRequest(BaseModel):
    username: str
    password: str


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


@router.post("/api/auth/login")
def login(request: LoginRequest, response: Response):
    user = authenticate_user(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session(user["id"])
    response.set_cookie(
        key="deus_session",
        value=token,
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return {
        "success": True,
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("deus_session")
    if token:
        delete_session(token)
    response.delete_cookie("deus_session")
    return {"success": True}


@router.get("/api/auth/me")
def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# --- Pending Changes (Normal user creates, Admin approves) ---

@router.post("/api/pending/submit")
def submit_pending_change(request: Request, payload: dict):
    user = require_auth(request)
    change_id = create_pending_change(
        user_id=user["user_id"],
        action=payload.get("action", ""),
        target=payload.get("target", ""),
        payload=payload.get("data", {}),
    )
    return {"success": True, "change_id": change_id}


@router.get("/api/pending")
def list_pending(request: Request):
    user = require_admin(request)
    return get_pending_changes("pending")


@router.post("/api/pending/{change_id}/review")
def review_change(request: Request, change_id: int, payload: dict):
    user = require_admin(request)
    approved = payload.get("approved", False)
    review_pending_change(change_id, user["user_id"], approved)
    return {"success": True, "approved": approved}
