# app/main.py
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pathlib import Path
import logging

from app.core.logger.app_logger import ExcludeHlsAccessFilter
from app.core.config.config import settings
from app.core.middleware.auth_validate import jwt_middleware
from app.domain.v1.routers import router as v1_router

APP_TITLE = "QC API"
APP_VERSION = "1.0.0"
OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/docs"
REDOC_PATH = "/redoc"

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:4173",
    "http://192.168.10.200:5173",
    "http://192.168.10.200:4173",
    "http://172.16.71.115:4173",
    "http://172.16.71.115:5173",
]

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    openapi_url=OPENAPI_PATH,
    docs_url=DOCS_PATH,
    redoc_url=REDOC_PATH,
    swagger_ui_parameters={"persistAuthorization": True},
)

logging.getLogger("uvicorn.access").addFilter(ExcludeHlsAccessFilter())

base = Path(f"{settings.HLS_ROOT}")
base.mkdir(parents=True, exist_ok=True)

# ---- Static images ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = PROJECT_ROOT / "images"
IMAGES_PREFIX = f"/{settings.IMAGES_DIR}".rstrip("/")

app.mount(IMAGES_PREFIX, StaticFiles(directory=str(IMAGES_DIR)), name="images")
app.mount("/hls", StaticFiles(directory=settings.HLS_ROOT), name="hls")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # includes Authorization
    expose_headers=["Content-Disposition"],
    max_age=86400,
)

# ---- Add cache headers for static images ----
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path.startswith(f"{IMAGES_PREFIX}/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
    elif path.startswith("/hls/"):
        # HLS playlists/segments must not be cached, otherwise clients can get stuck on old footage.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

# ---- JWT middleware with bypass for OPTIONS & public paths ----
AUTH_PREFIX = "/api/v1/auth/"
@app.middleware("http")
async def jwt_bypass_wrapper(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if (
        path.startswith(DOCS_PATH)
        or path.startswith(REDOC_PATH)
        or path == OPENAPI_PATH
        or path.startswith(f"{IMAGES_PREFIX}/")
        or path.startswith(AUTH_PREFIX)        
        or path.startswith("/api/v1/health")        
        or path.startswith("/hls/")        
    ):
        return await call_next(request)

    return await jwt_middleware(request, call_next)

app.include_router(v1_router, prefix="/api/v1")

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=APP_TITLE,
        version=APP_VERSION,
        description="QC API with JWT Bearer auth",
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
