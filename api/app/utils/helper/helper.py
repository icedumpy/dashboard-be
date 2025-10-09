from fastapi import HTTPException, Request
from typing import List, Optional
from pathlib import Path, PurePosixPath
from app.core.config.config import settings
from fastapi import Request
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from app.core.db.repo.models import User, Item, ItemImage, ProductionLine, Role
from sqlalchemy.dialects import postgresql


IMAGES_DIR = settings.IMAGES_DIR
TZ = ZoneInfo("Asia/Bangkok")

def current_shift_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = (now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)) if now else datetime.now(TZ)
    today = now.date()

    day_start = datetime.combine(today, time(8, 0), TZ)
    day_end   = datetime.combine(today, time(20, 0), TZ)

    if day_start <= now < day_end:
        return day_start, day_end

    # - if now >= 20:00 → [20:00 today, 08:00 tomorrow)
    # - if now  < 08:00 → [20:00 yesterday, 08:00 today)
    if now >= day_end:
        return day_end, day_start + timedelta(days=1)
    else:
        return day_end - timedelta(days=1), day_start
    
def require_role(user: User, allowed: List[Role]) -> None:
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")

# def require_same_line(user: User, item: Item):
#     if user.line_id != item.line_id:
#         raise HTTPException(status_code=403, detail="Cross-line operation not allowed")


def safe_fs_path(relpath: str) -> Path:
    """
    Normalize relpath and ensure it resolves inside IMAGES_DIR.
    Blocks path traversal and absolute paths.
    """
    base = Path(IMAGES_DIR).resolve()  # <-- fix: make sure it's a Path

    rel_norm = PurePosixPath(relpath.replace("\\", "/")).as_posix().lstrip("/")

    fs = (base / rel_norm).resolve(strict=False)

    if not fs.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid image path")

    return fs

def print_sql(query):
    compiled = query.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True}  # inline params
    )
    print(str(compiled).replace("\n", " "))
    
def _subdir_for(kind: str) -> str:
    k = (kind or "FIX").upper()
    return "capture" if k == "DETECTED" else ("resolved" if k == "FIX" else "other")

async def get_base_image_relpath(
    db: AsyncSession,
    *,
    item_id: Optional[int],
    kind: str = "FIX",
) -> str:
    """
    Compute the base relative folder (under /images) to store images for an item.

    Returns a POSIX relative path like:
      '2025-08/21/line_3/roll/250814002D06/resolved'

    Behavior:
    - If the item has a previous DETECTED image, reuse its structure and replace the
      last segment ('capture') with the appropriate subdir for `kind`.
    - Otherwise, build from item fields (or date-only if item_id is None).
    """
    subdir = _subdir_for(kind)

    it: Optional[Item] = None
    line_code: Optional[str] = None

    if item_id is not None:
        it = await db.get(Item, item_id)
        if not it:
            item_id = None
        else:
            line_code = await db.scalar(
                select(ProductionLine.code).where(ProductionLine.id == it.line_id)
            ) or str(it.line_id)
    if it is not None:
        last_detected_path: Optional[str] = await db.scalar(
            select(ItemImage.path)
            .where(ItemImage.item_id == it.id, ItemImage.kind == "DETECTED")
            .order_by(ItemImage.uploaded_at.desc(), ItemImage.id.desc())
            .limit(1)
        )
        if last_detected_path:
            # e.g. 2025-08/21/line_3/roll/250814002D06/capture/698878.jpg
            p = PurePosixPath(last_detected_path)
            base_dir = p.parent.parent / subdir
            return base_dir.as_posix().lstrip("/")

    now = (it.detected_at if it and it.detected_at else datetime.now(timezone.utc))
    y_m = f"{now:%Y-%m}"
    d = f"{now:%d}"

    if it is not None:
        station_dir = (it.station or "").lower() or "unknown"
        number = it.roll_number or it.bundle_number or "unknown"
        lc = f"line_{line_code}" if line_code else f"line_{it.line_id}"
        base_dir = PurePosixPath(y_m) / d / lc / station_dir / str(number) / subdir
    else:
        # No item context
        base_dir = PurePosixPath(y_m) / d / "unbound" / subdir

    return base_dir.as_posix().lstrip("/")