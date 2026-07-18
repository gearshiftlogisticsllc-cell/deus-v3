"""
app/main.py — DEUS 3.0 FastAPI Application
=============================================
Entry point for Railway cloud deployment.
Run with: uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.auth_routes import router as auth_router
from app.api.lead_routes import router as lead_router
from app.api.support_routes import router as support_router
from app.auth import get_current_user
from app.database import init_db

app = FastAPI(
    title="DEUS 3.0 API",
    description="Digital Entity Unification System — Cloud Backend",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "https://deus-v3-production.up.railway.app").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if os.getenv("ENFORCE_HTTPS", "").lower() in ("1", "true", "yes"):
            if request.url.scheme != "https" and request.headers.get("x-forwarded-proto") != "https":
                url = request.url.replace(scheme="https")
                return RedirectResponse(url=str(url), status_code=301)
        return await call_next(request)


app.add_middleware(HTTPSRedirectMiddleware)

app.include_router(auth_router)
app.include_router(lead_router)
app.include_router(support_router)

# Initialize DB
init_db()

# Start daemon on boot (if enabled)
try:
    from daemon import get_daemon
    daemon_enabled = os.getenv("DAEMON_AUTO_START", "false").lower() == "true"
    if daemon_enabled:
        daemon = get_daemon()
        daemon.start()
except Exception as e:
    print(f"[DEUS] Daemon auto-start skipped: {e}")


@app.get("/login")
def login_page():
    html_path = os.path.join(os.path.dirname(__file__), "login.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/")
def root(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())
