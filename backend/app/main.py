"""Standalone FastAPI app for the SViam interview lab.

Exposes only the vNext interview REST + WebSocket routes. In-memory store by
default; no database or secrets required to run the demo.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.vnext.interview import router as vnext_interview_router  # this IS the APIRouter

app = FastAPI(title="SViam Interview Lab", docs_url="/docs")

# Allow the Next.js dev server to call the REST + WS endpoints. The regex permits
# any localhost / 127.0.0.1 port so it works no matter which port the frontend
# runs on (dev machines vary). Add deployed origins via CORS_ORIGINS (comma-sep).
_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vnext_interview_router)


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "sviam-interview-lab"}
