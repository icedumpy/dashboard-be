from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jose import JWTError
from app.core.security.auth import decode_token

app = FastAPI()

@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    # skip for login/refresh endpoints
    if request.url.path.startswith("/auth/login") or request.url.path.startswith("/auth/refresh"):
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
