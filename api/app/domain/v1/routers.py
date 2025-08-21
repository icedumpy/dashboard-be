from fastapi import APIRouter
from . import health
from .item.item_router import router as item_router
from .auth.auth_router import router as auth_router
from .upload.upload_router import router as upload_router
from .production_line.production_line_router import router as production_line_router

router = APIRouter()

# Base health (no prefix under /api/v1)
router.include_router(health.router, tags=["health"])

# Versioned domains
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(item_router, prefix="/item", tags=["item"])
router.include_router(upload_router, prefix="/upload", tags=["upload"])
router.include_router(production_line_router, prefix="/production_line", tags=["line"])