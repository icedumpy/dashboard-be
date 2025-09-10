from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError
from app.core.security.auth import decode_token

# in jwt_middleware

ALLOWED = {
    "http://localhost:4173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://127.0.0.1:5173",
    "http://192.168.10.200:4173",
    "http://192.168.10.200:5173",
    "http://172.16.71.115:4173",
    "http://172.16.71.115:5173",
}

def _cors_headers_for(request: Request) -> dict:
    origin = request.headers.get("origin")
    if origin and origin in ALLOWED:
        # echo back the origin so the browser accepts credentials
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}

async def jwt_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path.startswith("/api/v1/auth/") or path in ("/api/openapi.json", "/docs", "/redoc"):
        return await call_next(request)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing auth header"},
            headers=_cors_headers_for(request),  # <— add ACAO on 401
        )

    token = auth.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        request.state.user = payload
    except JWTError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid token"},
            headers=_cors_headers_for(request),  # <— add ACAO on 401
        )

    return await call_next(request)
