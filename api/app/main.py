# app/main.py
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError
from typing import Iterable

from app.core.security.auth import decode_token
from app.domain.v1.routers import router as v1_router

APP_TITLE = "QC API"
APP_VERSION = "1.0.0"
OPENAPI_PATH = "/api/openapi.json"   # keep in sync with FastAPI init
DOCS_PATH = "/docs"
REDOC_PATH = "/redoc"

# Any path that should bypass JWT checks (exact or as a prefix)
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/health",
    DOCS_PATH,
    REDOC_PATH,
    OPENAPI_PATH,
)

def is_exempt(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(p) for p in prefixes)

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
)

@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    path = request.url.path

    # Bypass for public endpoints (login/refresh/health/docs/openapi)
    if is_exempt(path, EXEMPT_PREFIXES):
        return await call_next(request)

    token = get_bearer_token(request)
    if not token:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or invalid Authorization header"},
        )

    try:
        payload = decode_token(token)
        # Attach decoded claims for downstream handlers
        request.state.user = payload
    except JWTError:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or expired token"},
        )

    return await call_next(request)

# Versioned API
app.include_router(v1_router, prefix="/api/v1")
