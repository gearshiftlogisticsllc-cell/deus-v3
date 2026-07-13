"""
app/main.py — DEUS 3.0 FastAPI Application
============================================
Entry point for Railway cloud deployment.
Run with: uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys

# Ensure project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Load .env from project root
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="DEUS 3.0 API",
    description="Digital Entity Unification System — Cloud Backend",
    version="3.0.0",
)

# CORS — allow GUI and any client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {
        "name": "DEUS 3.0",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
    }
