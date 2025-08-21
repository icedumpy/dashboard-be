from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError
from app.core.security.auth import decode_token
from typing import Iterable

EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/api/openapi.json",
)

def is_exempt(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(p) for p in prefixes)

async def jwt_middleware(request: Request, call_next):
    path = request.url.path

    if is_exempt(path, EXEMPT_PREFIXES):
        return await call_next(request)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing auth header"})

    token = auth.split(" ")[1]
    try:
        payload = decode_token(token)
        request.state.user = payload  # store payload for later use
    except JWTError:
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})

    return await call_next(request)
