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

# Allow the Next.js dev server (and any extra origins via CORS_ORIGINS) to call
# the REST + WS endpoints.
_default_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vnext_interview_router)


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "sviam-interview-lab"}
