from fastapi import APIRouter
from . import health
from .item.item import router as item_router
from .auth.auth import router as auth_router

router = APIRouter()

# Base health (no prefix under /api/v1)
router.include_router(health.router, tags=["health"])

# Versioned domains
router.include_router(item_router, prefix="/item", tags=["item"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])