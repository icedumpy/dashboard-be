from fastapi import HTTPException, Request
from typing import List
from datetime import datetime
from app.core.db.repo.user.user_entity import User
from app.core.db.repo.models import (
    Item
)
from fastapi import Request


def require_role(user: User, allowed: List[str]):
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")

def require_same_line(user: User, item: Item):
    if user.line_id != item.line_id:
        raise HTTPException(status_code=403, detail="Cross-line operation not allowed")

def require_same_shift_if_operator(user: User, item: Item):
    if user.role == "OPERATOR" and user.shift_id is not None and user.shift_id != getattr(item, "shift_id", user.shift_id):
        # item has no shift; we check only user's shift rule as you requested
        raise HTTPException(status_code=403, detail="Operator shift mismatch")

def precondition_if_unmodified_since(request: Request, last_updated_at: datetime):
    ims = request.headers.get("If-Unmodified-Since")
    if not ims:
        return
    try:
        # Expect RFC1123 or ISO8601; accept ISO for simplicity
        ts = datetime.fromisoformat(ims.replace("Z","+00:00"))
        if last_updated_at and last_updated_at > ts:
            raise HTTPException(status_code=412, detail="Precondition Failed")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid If-Unmodified-Since")
