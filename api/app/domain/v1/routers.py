from fastapi import APIRouter
from . import health
from .item.router import router as item_router
from .auth.router import router as auth_router
from .image.router import router as image_router
from .production_line.router import router as production_line_router
from .review.router import router as review_router
from .defect_type.router import router as defect_type_router
from .change_status.router import router as change_status_router
from .dashboard.router import router as dashboard_router

router = APIRouter()

# Base health (no prefix under /api/v1)
router.include_router(health.router, tags=["health"])

# Versioned domains
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(item_router, prefix="/item", tags=["item"])
router.include_router(image_router, prefix="/image", tags=["upload"])
router.include_router(production_line_router, prefix="/production_line", tags=["line"])
router.include_router(review_router, prefix="/review", tags=["review"])
router.include_router(defect_type_router, prefix="/defect_type", tags=["defect_type"])
router.include_router(change_status_router, prefix="/change_status", tags=["change_status"])
router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])