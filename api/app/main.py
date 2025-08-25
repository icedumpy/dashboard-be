# app/main.py
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.core.config.config import settings
from fastapi.openapi.utils import get_openapi

from app.core.middleware.auth_validate import jwt_middleware
from app.domain.v1.routers import router as v1_router

APP_TITLE = "QC API"
APP_VERSION = "1.0.0"
OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/docs"
REDOC_PATH = "/redoc"


def get_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    openapi_url=OPENAPI_PATH,
    docs_url=DOCS_PATH,
    redoc_url=REDOC_PATH,
    # keep the token in the UI so you don't have to re‑enter after refresh
    swagger_ui_parameters={"persistAuthorization": True},
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = PROJECT_ROOT / "images"

app.mount(f"/{settings.IMAGES_DIR}", StaticFiles(directory=str(IMAGES_DIR)), name="images")
@app.middleware("http")
async def add_cache_headers(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(f"/{settings.IMAGES_DIR}/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
    return resp

app.middleware("http")(jwt_middleware)

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
    # 👇 make bearer required by default for all operations
    schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi

# Helpful so Swagger keeps your token
app.swagger_ui_parameters = {"persistAuthorization": True}
