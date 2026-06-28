"""Aggregate the vNext interview REST + WS routers into one include-able symbol."""
from __future__ import annotations

from fastapi import APIRouter

from .rest import router as _rest_router
from .ws import router as _ws_router

router = APIRouter()
router.include_router(_rest_router)
router.include_router(_ws_router)

__all__ = ["router"]
