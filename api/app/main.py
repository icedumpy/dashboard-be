# app/main.py
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pathlib import Path

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
]

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    openapi_url=OPENAPI_PATH,
    docs_url=DOCS_PATH,
    redoc_url=REDOC_PATH,
    swagger_ui_parameters={"persistAuthorization": True},
)

# ---- Static images ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = PROJECT_ROOT / "images"
IMAGES_PREFIX = f"/{settings.IMAGES_DIR}".rstrip("/")

app.mount(IMAGES_PREFIX, StaticFiles(directory=str(IMAGES_DIR)), name="images")

# ---- CORS (must be BEFORE auth) ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,      
    allow_credentials=True,           
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-Match", "If-None-Match"],
    expose_headers=["Content-Disposition", "ETag"],
    max_age=86400,
)

# ---- Add cache headers for static images ----
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(f"{IMAGES_PREFIX}/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
    return resp

# ---- JWT middleware with bypass for OPTIONS & public paths ----
@app.middleware("http")
async def jwt_bypass_wrapper(request: Request, call_next):
    # Allow CORS preflight
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if (
        path.startswith(DOCS_PATH)
        or path.startswith(REDOC_PATH)
        or path == OPENAPI_PATH
        or path.startswith(f"{IMAGES_PREFIX}/")
    ):
        return await call_next(request)
    return await jwt_middleware(request, call_next)

# ---- Routers ----
app.include_router(v1_router, prefix="/api/v1")

# ---- Swagger/OpenAPI: add Bearer auth & set as default security ----
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
